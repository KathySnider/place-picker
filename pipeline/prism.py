"""
pipeline/prism.py
-----------------
Downloads PRISM 30-year climate normals (1991-2020) at 4km resolution and
derives per-place snowfall, summer temp, and winter temp.

PRISM (Parameter-elevation Regressions on Independent Slopes Model) from
Oregon State University accounts for terrain effects including lake-effect
snow and elevation gradients — making it significantly more accurate than
Daymet or ERA5 for small towns in complex terrain.

Strategy:
    1. Download 48 ZIP files (12 monthly ppt + 12 monthly tmean + 12 monthly tmax
       + 12 monthly tmin) from the PRISM web service — one-time ~600MB download,
       no login required.
    2. Extract GeoTIFFs and keep them in data/raw/prism/.
    3. For each candidate, sample the nearest grid point from each raster.
    4. Derive snowfall: sum monthly ppt where tmean < SNOW_THRESHOLD_C.
    5. Compute summer (JJA) and winter (DJF) mean temps (tmean).
    6. Extract July average daily high (tmax) as the primary summer heat measure.
    7. Extract January average daily low (tmin) as the primary winter cold measure.
    8. Cache results to data/processed/prism_cache.parquet.

Output columns:
    prism_snow_in      -- estimated annual snowfall depth (inches)
    prism_summer_f     -- mean summer (JJA) daily mean temp (deg F, tmean)
    prism_winter_f     -- mean winter (DJF) daily mean temp (deg F, tmean)
    prism_july_tmax_f  -- July average daily high temp (deg F, tmax)
    prism_jan_tmin_f   -- January average daily low temp (deg F, tmin)

Requires:
    pip install rasterio
"""

import os
import io
import json
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db as _db
import requests
import numpy as np
import pandas as pd
from datetime import date

CACHE_PATH  = "data/processed/prism_cache.parquet"
META_PATH   = "data/processed/prism_cache_meta.json"
RASTER_DIR  = "data/raw/prism"

# PRISM data directory — no auth required
# Files named: prism_{element}_us_25m_2020{MM}_avg_30y.zip  (25m = 2.5 arc-min ~ 4km)
BASE_URL = "https://data.prism.oregonstate.edu/normals/us/4km"

MONTHS = [f"{m:02d}" for m in range(1, 13)]

# Rain/snow partitioning threshold: precip falls as snow when tmean < this (deg C)
SNOW_THRESHOLD_C = -2.0

WINTER_MONTHS = [12, 1, 2]   # DJF
SUMMER_MONTHS = [6, 7, 8]    # JJA

PRISM_COLS = ["geoid", "prism_snow_in", "prism_summer_f", "prism_winter_f", "prism_july_tmax_f", "prism_jan_tmin_f"]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _tif_path(element: str, month: str) -> str:
    return os.path.join(RASTER_DIR, f"prism_{element}_{month}.tif")


def _download_rasters():
    """Download monthly ppt, tmean, and tmax normals from PRISM data directory."""
    os.makedirs(RASTER_DIR, exist_ok=True)
    needed = []
    for element in ("ppt", "tmean", "tmax", "tmin"):
        for month in MONTHS:
            if not os.path.exists(_tif_path(element, month)):
                needed.append((element, month))

    if not needed:
        return

    print(f"[prism] Downloading {len(needed)} raster files from PRISM "
          f"(one-time ~600 MB)...")
    for i, (element, month) in enumerate(needed, 1):
        # Filename format: prism_ppt_us_25m_202001_avg_30y.zip
        filename = f"prism_{element}_us_25m_2020{month}_avg_30y.zip"
        url = f"{BASE_URL}/{element}/monthly/{filename}"
        dest = _tif_path(element, month)
        print(f"[prism]   ({i}/{len(needed)}) {element} month {month}...", end=" ", flush=True)
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            tif_names = [n for n in z.namelist() if n.endswith(".tif")]
            if not tif_names:
                raise ValueError(f"No .tif found in ZIP for {element}/{month}")
            with z.open(tif_names[0]) as src, open(dest, "wb") as out:
                out.write(src.read())
        print("done")
    print("[prism] All rasters downloaded.")


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _c_to_f(c):
    return c * 9 / 5 + 32


def _process_candidates(candidates: pd.DataFrame) -> pd.DataFrame:
    """Sample PRISM rasters for each candidate and derive climate stats."""
    try:
        import rasterio
    except ImportError:
        raise ImportError("rasterio not installed -- run: pip install rasterio")

    # Load all 36 rasters into memory (4km CONUS ~ 10MB each uncompressed)
    print("[prism] Loading rasters into memory...")
    ppt   = {}
    tmean = {}
    tmax  = {}
    tmin  = {}

    for element, store in (("ppt", ppt), ("tmean", tmean), ("tmax", tmax), ("tmin", tmin)):
        for month in MONTHS:
            with rasterio.open(_tif_path(element, month)) as ds:
                store[month] = {
                    "data":      ds.read(1).astype(float),
                    "transform": ds.transform,
                    "nodata":    ds.nodata,
                    "crs":       ds.crs,
                }
                if ds.nodata is not None:
                    store[month]["data"][store[month]["data"] == ds.nodata] = np.nan

    JULY = "07"
    JAN  = "01"

    print(f"[prism] Extracting values for {len(candidates):,} candidates...")
    tx_sample = tmax[JULY]
    print(f"[prism] tmax July shape={tx_sample['data'].shape} nodata={tx_sample['nodata']} "
          f"min={np.nanmin(tx_sample['data']):.1f} max={np.nanmax(tx_sample['data']):.1f}")
    # Diagnostic: test first candidate lookup
    _first = candidates.iloc[0]
    _tx = tmax[JULY]
    _col, _r = ~_tx["transform"] * (_first.lng, _first.lat)
    _col, _r = int(_col), int(_r)
    _h, _w = _tx["data"].shape
    _val = _tx["data"][_r, _col] if (0 <= _r < _h and 0 <= _col < _w) else "OUT OF BOUNDS"
    print(f"[prism] tmax test: lat={_first.lat} lng={_first.lng} -> row={_r} col={_col} bounds=({_h},{_w}) val={_val}")
    _pp = ppt[JULY]
    _col2, _r2 = ~_pp["transform"] * (_first.lng, _first.lat)
    _col2, _r2 = int(_col2), int(_r2)
    _val2 = _pp["data"][_r2, _col2] if (0 <= _r2 < _h and 0 <= _col2 < _w) else "OUT OF BOUNDS"
    print(f"[prism] ppt  test: lat={_first.lat} lng={_first.lng} -> row={_r2} col={_col2} val={_val2}")
    rows = []
    for row in candidates.itertuples():
        if pd.isna(row.lat) or pd.isna(row.lng):
            rows.append({"geoid": row.geoid,
                         "prism_snow_in": np.nan,
                         "prism_summer_f": np.nan,
                         "prism_winter_f": np.nan,
                         "prism_july_tmax_f": np.nan,
                         "prism_jan_tmin_f": np.nan})
            continue

        monthly_ppt   = []
        monthly_tmean = []

        for month in MONTHS:
            p = ppt[month]
            t = tmean[month]
            col, r = ~p["transform"] * (row.lng, row.lat)
            col, r = int(col), int(r)
            h, w = p["data"].shape
            if 0 <= r < h and 0 <= col < w:
                monthly_ppt.append(p["data"][r, col])
                monthly_tmean.append(t["data"][r, col])
            else:
                monthly_ppt.append(np.nan)
                monthly_tmean.append(np.nan)

        # July tmax
        tx = tmax[JULY]
        col, r = ~tx["transform"] * (row.lng, row.lat)
        col, r = int(col), int(r)
        h, w = tx["data"].shape
        july_tmax_c = tx["data"][r, col] if (0 <= r < h and 0 <= col < w) else np.nan
        if not np.isnan(july_tmax_c) and july_tmax_c < -100:
            july_tmax_c = np.nan  # nodata sentinel not caught by rasterio

        # January tmin
        tn = tmin[JAN]
        col, r = ~tn["transform"] * (row.lng, row.lat)
        col, r = int(col), int(r)
        h, w = tn["data"].shape
        jan_tmin_c = tn["data"][r, col] if (0 <= r < h and 0 <= col < w) else np.nan
        if not np.isnan(jan_tmin_c) and jan_tmin_c < -100:
            jan_tmin_c = np.nan  # nodata sentinel not caught by rasterio

        mp = np.array(monthly_ppt)
        mt = np.array(monthly_tmean)

        snow_ppt_mm = np.nansum(
            np.where((mt < SNOW_THRESHOLD_C) & ~np.isnan(mp), mp, 0)
        )
        snow_in = round(snow_ppt_mm * 10 / 25.4, 1)

        sum_idx = [m - 1 for m in SUMMER_MONTHS]
        win_idx = [m - 1 for m in WINTER_MONTHS]
        summer_c = np.nanmean(mt[sum_idx]) if not np.all(np.isnan(mt[sum_idx])) else np.nan
        winter_c = np.nanmean(mt[win_idx]) if not np.all(np.isnan(mt[win_idx])) else np.nan

        rows.append({
            "geoid":             row.geoid,
            "prism_snow_in":     snow_in if not np.isnan(snow_in) else np.nan,
            "prism_summer_f":    round(_c_to_f(summer_c), 1) if not np.isnan(summer_c) else np.nan,
            "prism_winter_f":    round(_c_to_f(winter_c), 1) if not np.isnan(winter_c) else np.nan,
            "prism_july_tmax_f": round(_c_to_f(july_tmax_c), 1) if not np.isnan(july_tmax_c) else np.nan,
            "prism_jan_tmin_f":  round(_c_to_f(jan_tmin_c), 1) if not np.isnan(jan_tmin_c) else np.nan,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Public enrich() function
# ---------------------------------------------------------------------------

def enrich(candidates: pd.DataFrame, cache_only: bool = False) -> pd.DataFrame:
    """
    Add PRISM-derived climate columns to candidates DataFrame.
    Downloads rasters on first run (~300 MB, one-time).
    Subsequent runs load from cache instantly.
    """
    cache = _db.read_cache("prism_cache", CACHE_PATH, PRISM_COLS)

    # Add any new columns introduced since the cache was last written
    for col in PRISM_COLS:
        if col not in cache.columns:
            cache[col] = np.nan

    cached_geoids = set(cache["geoid"].tolist())
    # Also re-process rows that are missing the new tmax/tmin columns
    missing_new_cols = set(
        cache.loc[cache[["prism_july_tmax_f", "prism_jan_tmin_f"]].isna().all(axis=1), "geoid"].tolist()
    ) & cached_geoids
    needed = (set(candidates["geoid"].tolist()) - cached_geoids) | (missing_new_cols & set(candidates["geoid"].tolist()))

    if not needed:
        print("[prism] All candidates already in PRISM cache.")
    elif cache_only:
        print(f"[prism] cache_only=True — skipping raster fetch for {len(needed):,} uncached places")
        needed = set()
    else:
        print(f"[prism] Computing PRISM climate for {len(needed):,} candidates...")
        _download_rasters()

        todo   = candidates[candidates["geoid"].isin(needed)].drop_duplicates(subset="geoid").copy()
        new_df = _process_candidates(todo)

        cache = pd.concat([cache, new_df], ignore_index=True)
        cache = cache.drop_duplicates(subset="geoid", keep="last")
        _db.write_cache_replace("prism_cache", CACHE_PATH, cache)
        # Write sidecar with dataset provenance
        meta = {
            "cache_updated":  str(date.today()),
            "normals_period": "1991-2020",
            "resolution":     "4km (~2.5 arc-min)",
            "source":         "PRISM Climate Group, Oregon State University",
            "note": "Delete data/raw/prism/ and this cache to re-download rasters.",
        }
        try:
            os.makedirs(os.path.dirname(META_PATH), exist_ok=True)
            with open(META_PATH, "w") as f:
                json.dump(meta, f, indent=2)
        except OSError:
            pass
        print(f"[prism] Cache saved: {len(new_df):,} places")

    if os.path.exists(META_PATH):
        try:
            with open(META_PATH) as f:
                meta = json.load(f)
            print(f"[prism] Dataset: {meta.get('normals_period')} normals  "
                  f"cached {meta.get('cache_updated')}")
        except OSError:
            pass

    available = [c for c in PRISM_COLS if c in cache.columns]
    return candidates.merge(cache[available], on="geoid", how="left")
