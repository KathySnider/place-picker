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
    1. Download 24 ZIP files (12 monthly ppt + 12 monthly tmean) from the
       PRISM web service — one-time ~300MB download, no login required.
    2. Extract GeoTIFFs and keep them in data/raw/prism/.
    3. For each candidate, sample the nearest grid point from each raster.
    4. Derive snowfall: sum monthly ppt where tmean < SNOW_THRESHOLD_C.
    5. Compute summer (JJA) and winter (DJF) mean temps.
    6. Cache results to data/processed/prism_cache.parquet.

Output columns:
    prism_snow_in      -- estimated annual snowfall depth (inches)
    prism_summer_f     -- mean summer (JJA) daily mean temp (deg F)
    prism_winter_f     -- mean winter (DJF) daily mean temp (deg F)

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

PRISM_COLS = ["geoid", "prism_snow_in", "prism_summer_f", "prism_winter_f"]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _tif_path(element: str, month: str) -> str:
    return os.path.join(RASTER_DIR, f"prism_{element}_{month}.tif")


def _download_rasters():
    """Download monthly ppt and tmean normals from PRISM data directory."""
    os.makedirs(RASTER_DIR, exist_ok=True)
    needed = []
    for element in ("ppt", "tmean"):
        for month in MONTHS:
            if not os.path.exists(_tif_path(element, month)):
                needed.append((element, month))

    if not needed:
        return

    print(f"[prism] Downloading {len(needed)} raster files from PRISM "
          f"(one-time ~72 MB)...")
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

    # Load all 24 rasters into memory (4km CONUS ~ 10MB each uncompressed)
    print("[prism] Loading rasters into memory...")
    ppt   = {}  # month -> (data array, transform, nodata)
    tmean = {}

    for element, store in (("ppt", ppt), ("tmean", tmean)):
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

    print(f"[prism] Extracting values for {len(candidates):,} candidates...")
    rows = []
    for row in candidates.itertuples():
        if pd.isna(row.lat) or pd.isna(row.lng):
            rows.append({"geoid": row.geoid,
                         "prism_snow_in": np.nan,
                         "prism_summer_f": np.nan,
                         "prism_winter_f": np.nan})
            continue

        monthly_ppt   = []
        monthly_tmean = []

        for month in MONTHS:
            p = ppt[month]
            t = tmean[month]
            # Convert lat/lng to row/col using affine transform
            col, r = ~p["transform"] * (row.lng, row.lat)
            col, r = int(col), int(r)
            h, w = p["data"].shape
            if 0 <= r < h and 0 <= col < w:
                monthly_ppt.append(p["data"][r, col])
                monthly_tmean.append(t["data"][r, col])
            else:
                monthly_ppt.append(np.nan)
                monthly_tmean.append(np.nan)

        mp = np.array(monthly_ppt)    # mm precip per month (12 values)
        mt = np.array(monthly_tmean)  # deg C mean temp per month (12 values)

        # Snowfall: ppt in months where tmean < threshold, converted to depth
        snow_ppt_mm = np.nansum(
            np.where((mt < SNOW_THRESHOLD_C) & ~np.isnan(mp), mp, 0)
        )
        snow_in = round(snow_ppt_mm * 10 / 25.4, 1)  # mm SWE -> snow depth inches

        # Summer / winter mean temps (month indices are 0-based here)
        sum_idx = [m - 1 for m in SUMMER_MONTHS]
        win_idx = [m - 1 for m in WINTER_MONTHS]
        summer_c = np.nanmean(mt[sum_idx]) if not np.all(np.isnan(mt[sum_idx])) else np.nan
        winter_c = np.nanmean(mt[win_idx]) if not np.all(np.isnan(mt[win_idx])) else np.nan

        rows.append({
            "geoid":          row.geoid,
            "prism_snow_in":  snow_in if not np.isnan(snow_in) else np.nan,
            "prism_summer_f": round(_c_to_f(summer_c), 1) if not np.isnan(summer_c) else np.nan,
            "prism_winter_f": round(_c_to_f(winter_c), 1) if not np.isnan(winter_c) else np.nan,
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Public enrich() function
# ---------------------------------------------------------------------------

def enrich(candidates: pd.DataFrame) -> pd.DataFrame:
    """
    Add PRISM-derived climate columns to candidates DataFrame.
    Downloads rasters on first run (~300 MB, one-time).
    Subsequent runs load from cache instantly.
    """
    cache = _db.read_cache("prism_cache", CACHE_PATH, PRISM_COLS)

    cached_geoids = set(cache["geoid"].tolist())
    needed = set(candidates["geoid"].tolist()) - cached_geoids

    if not needed:
        print("[prism] All candidates already in PRISM cache.")
    else:
        print(f"[prism] Computing PRISM climate for {len(needed):,} candidates...")
        _download_rasters()

        todo   = candidates[candidates["geoid"].isin(needed)].copy()
        new_df = _process_candidates(todo)

        cache = pd.concat([cache, new_df], ignore_index=True)
        _db.write_cache("prism_cache", CACHE_PATH, cache)
        # Write sidecar with dataset provenance
        meta = {
            "cache_updated":  str(date.today()),
            "normals_period": "1991-2020",
            "resolution":     "4km (~2.5 arc-min)",
            "source":         "PRISM Climate Group, Oregon State University",
            "note": "Delete data/raw/prism/ and this cache to re-download rasters.",
        }
        with open(META_PATH, "w") as f:
            json.dump(meta, f, indent=2)
        print(f"[prism] Cache saved: {len(new_df):,} places")

    # Print sidecar info so user knows how current the data is
    if os.path.exists(META_PATH):
        with open(META_PATH) as f:
            meta = json.load(f)
        print(f"[prism] Dataset: {meta.get('normals_period')} normals  "
              f"cached {meta.get('cache_updated')}")

    return candidates.merge(cache[PRISM_COLS], on="geoid", how="left")
