"""
pipeline/era5.py
----------------
Downloads ERA5 monthly reanalysis data from the Copernicus Climate Data Store
and computes per-place climate warming trends.

ERA5 is ECMWF's global reanalysis dataset — the gold standard for historical
climate data. It accounts for terrain, lakes, and mesoscale effects better
than station-based datasets.

Strategy:
    1. Download one NetCDF grid file covering CONUS (1980-2024, all months)
       This is a one-time ~100-150 MB download via the CDS API.
    2. For each candidate, extract the nearest grid point time series.
    3. Compute per-place summer/winter averages and linear warming trends.
    4. Cache results to data/processed/era5_cache.parquet.

Output columns:
    summer_f_1980s    — avg summer daily mean temp °F (1980-1994 baseline)
    summer_f_recent   — avg summer daily mean temp °F (2015-2024 recent)
    summer_trend_f_dec — summer warming trend °F/decade (positive = warming)
    winter_f_1980s    — avg winter daily mean temp °F (1980-1994 baseline)
    winter_f_recent   — avg winter daily mean temp °F (2015-2024 recent)
    winter_trend_f_dec — winter warming trend °F/decade
    snow_mm_1980s     — avg annual snowfall mm water equiv (1980-1994 baseline)
    snow_mm_recent    — avg annual snowfall mm water equiv (2015-2024 recent)
    snow_trend_dec    — snowfall trend mm/decade (negative = declining snowfall)

Requires:
    CDSAPI_URL and CDSAPI_KEY in .env (or ~/.cdsapirc)
    pip install cdsapi netCDF4
"""

import os
import json
import numpy as np
import pandas as pd
from datetime import date
from dotenv import load_dotenv

load_dotenv()

CACHE_PATH  = "data/processed/era5_cache.parquet"
META_PATH   = "data/processed/era5_cache_meta.json"
NC_PATH     = "data/raw/era5_monthly.nc"   # may be a ZIP from new CDS API
NC_TEMP     = "data/raw/era5_t2m.nc"       # extracted temperature file
NC_SNOW     = "data/raw/era5_sf.nc"        # extracted snowfall file

# CONUS bounding box: [North, West, South, East]
AREA = [50, -130, 24, -65]

YEARS       = list(range(1980, 2025))
BASELINE    = (1980, 1994)   # early period for comparison
RECENT      = (2015, 2024)   # recent period for comparison

WINTER_MONTHS = [12, 1, 2]
SUMMER_MONTHS = [6, 7, 8]

ERA5_COLS = [
    "geoid",
    "summer_f_1980s", "summer_f_recent", "summer_trend_f_dec",
    "winter_f_1980s", "winter_f_recent", "winter_trend_f_dec",
    "snow_mm_1980s",  "snow_mm_recent",  "snow_trend_dec",
]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _download_era5():
    """Submit CDS request and download ERA5 monthly NetCDF to NC_PATH."""
    try:
        import cdsapi
    except ImportError:
        raise ImportError("cdsapi not installed — run: pip install cdsapi")

    os.makedirs("data/raw", exist_ok=True)
    print("[era5] Submitting CDS request (may take several minutes to queue)...")
    print("[era5] Downloading 1980-2024 monthly means for CONUS...")

    c = cdsapi.Client(
        url=os.getenv("CDSAPI_URL", "https://cds.climate.copernicus.eu/api"),
        key=os.getenv("CDSAPI_KEY"),
        quiet=False,
    )
    c.retrieve(
        "reanalysis-era5-single-levels-monthly-means",
        {
            "product_type": "monthly_averaged_reanalysis",
            "variable":     ["2m_temperature", "snowfall"],
            "year":         [str(y) for y in YEARS],
            "month":        [f"{m:02d}" for m in range(1, 13)],
            "time":         "00:00",
            "area":         AREA,
            "data_format":  "netcdf",
        },
        NC_PATH,
    )
    print(f"[era5] Downloaded: {NC_PATH}")


# ---------------------------------------------------------------------------
# Unzip helper (new CDS API wraps NetCDF files in a ZIP archive)
# ---------------------------------------------------------------------------

def _extract_zip():
    """If NC_PATH is a ZIP, extract t2m and sf NetCDF files to NC_TEMP / NC_SNOW."""
    import zipfile
    if not zipfile.is_zipfile(NC_PATH):
        return  # already a plain NetCDF (old API behaviour)
    print("[era5] Extracting NetCDF files from ZIP archive...")
    with zipfile.ZipFile(NC_PATH) as z:
        for name in z.namelist():
            data = z.read(name)
            if "avgua" in name or "t2m" in name:
                dest = NC_TEMP
            elif "avgad" in name or "sf" in name:
                dest = NC_SNOW
            else:
                continue
            with open(dest, "wb") as f:
                f.write(data)
    print(f"[era5] Extracted to {NC_TEMP} and {NC_SNOW}")


# ---------------------------------------------------------------------------
# Extract and compute trends
# ---------------------------------------------------------------------------

def _k_to_f(k): return (k - 273.15) * 9 / 5 + 32


def _linear_trend(years: np.ndarray, values: np.ndarray) -> float:
    """Return slope in units/decade via least-squares fit. NaN-safe."""
    mask = ~np.isnan(values)
    if mask.sum() < 5:
        return np.nan
    slope = np.polyfit(years[mask], values[mask], 1)[0]
    return round(slope * 10, 3)   # per decade


def _process_grid(candidates: pd.DataFrame) -> pd.DataFrame:
    """Open the NetCDF file and extract per-candidate climate trends."""
    try:
        import netCDF4 as nc4
    except ImportError:
        raise ImportError("netCDF4 not installed — run: pip install netCDF4")

    # Support both old (single file) and new (two extracted files) layouts
    if os.path.exists(NC_TEMP) and os.path.exists(NC_SNOW):
        print(f"[era5] Opening {NC_TEMP} and {NC_SNOW}...")
        ds_t = nc4.Dataset(NC_TEMP)
        ds_s = nc4.Dataset(NC_SNOW)
    else:
        print(f"[era5] Opening {NC_PATH}...")
        ds_t = nc4.Dataset(NC_PATH)
        ds_s = ds_t

    # Time: use temperature file as reference
    time_var  = ds_t.variables["valid_time"]
    times     = nc4.num2date(time_var[:], time_var.units, calendar="standard")
    months_dt = pd.DatetimeIndex([pd.Timestamp(str(t)) for t in times])

    # Grid coordinates
    lats = ds_t.variables["latitude"][:]
    lons = ds_t.variables["longitude"][:]

    # Temperature (K) and snowfall
    t2m      = ds_t.variables["t2m"][:]     # shape: (time, lat, lon), units: K
    snowfall = ds_s.variables["sf"][:]      # shape: (time, lat, lon)

    # sf stepType=avgad = average DAILY snowfall (m water equiv/day)
    # multiply by days in month to get monthly total, then m->mm
    days_in_month = months_dt.days_in_month.values
    snow_mm = snowfall * days_in_month[:, None, None] * 1000  # m/day -> mm/month

    years_arr = np.array(YEARS)
    rows = []

    for row in candidates.itertuples():
        if pd.isna(row.lat) or pd.isna(row.lng):
            rows.append({"geoid": row.geoid, **{c: None for c in ERA5_COLS[1:]}})
            continue

        # Nearest grid point
        lat_idx = int(np.argmin(np.abs(lats - row.lat)))
        lon_idx = int(np.argmin(np.abs(lons - row.lng)))

        # Build annual summer/winter/snow time series
        summer_by_year = []
        winter_by_year = []
        snow_by_year   = []

        for yr in YEARS:
            yr_mask    = months_dt.year == yr
            sum_mask   = yr_mask & months_dt.month.isin(SUMMER_MONTHS)
            win_mask   = yr_mask & months_dt.month.isin(WINTER_MONTHS)

            t_summer = t2m[sum_mask, lat_idx, lon_idx]
            t_winter = t2m[win_mask, lat_idx, lon_idx]
            s_annual = snow_mm[yr_mask, lat_idx, lon_idx]

            # December belongs to previous year's winter — handle edge
            if yr == YEARS[0]:
                t_winter = t2m[
                    (months_dt.year == yr) & months_dt.month.isin([1, 2]),
                    lat_idx, lon_idx
                ]

            summer_by_year.append(float(np.ma.mean(t_summer)) if len(t_summer) else np.nan)
            winter_by_year.append(float(np.ma.mean(t_winter)) if len(t_winter) else np.nan)
            snow_by_year.append(float(np.ma.sum(s_annual))    if len(s_annual) else np.nan)

        summer_f = [_k_to_f(v) if not np.isnan(v) else np.nan for v in summer_by_year]
        winter_f = [_k_to_f(v) if not np.isnan(v) else np.nan for v in winter_by_year]
        snow_arr = np.array(snow_by_year)
        sum_arr  = np.array(summer_f)
        win_arr  = np.array(winter_f)

        def _period_avg(arr, start, end):
            mask = (years_arr >= start) & (years_arr <= end)
            vals = arr[mask]
            valid = vals[~np.isnan(vals)]
            return round(float(np.mean(valid)), 1) if len(valid) else np.nan

        rows.append({
            "geoid":              row.geoid,
            "summer_f_1980s":     _period_avg(sum_arr,  *BASELINE),
            "summer_f_recent":    _period_avg(sum_arr,  *RECENT),
            "summer_trend_f_dec": _linear_trend(years_arr, sum_arr),
            "winter_f_1980s":     _period_avg(win_arr,  *BASELINE),
            "winter_f_recent":    _period_avg(win_arr,  *RECENT),
            "winter_trend_f_dec": _linear_trend(years_arr, win_arr),
            "snow_mm_1980s":      _period_avg(snow_arr, *BASELINE),
            "snow_mm_recent":     _period_avg(snow_arr, *RECENT),
            "snow_trend_dec":     _linear_trend(years_arr, snow_arr),
        })

    ds_t.close()
    if ds_s is not ds_t:
        ds_s.close()
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Public enrich() function
# ---------------------------------------------------------------------------

def enrich(candidates: pd.DataFrame) -> pd.DataFrame:
    """
    Add ERA5 climate trend columns to candidates DataFrame.
    Downloads the ERA5 grid file on first run (~100-150 MB, one-time).
    Subsequent runs load from cache instantly.
    """
    # Load or initialize cache
    if os.path.exists(CACHE_PATH):
        cache = pd.read_parquet(CACHE_PATH)
        for col in ERA5_COLS[1:]:
            if col not in cache.columns:
                cache[col] = np.nan
    else:
        cache = pd.DataFrame(columns=ERA5_COLS)

    cached_geoids = set(cache["geoid"].tolist())
    needed = set(candidates["geoid"].tolist()) - cached_geoids

    if not needed:
        print("[era5] All candidates already in ERA5 cache.")
    else:
        print(f"[era5] Computing ERA5 trends for {len(needed):,} candidates...")

        # Download grid if not already local
        if not os.path.exists(NC_PATH):
            _download_era5()
        else:
            print(f"[era5] Using existing grid file: {NC_PATH}")

        # Unzip if the CDS API returned a ZIP archive
        _extract_zip()

        todo    = candidates[candidates["geoid"].isin(needed)].copy()
        new_df  = _process_grid(todo)

        cache = pd.concat([cache, new_df], ignore_index=True)
        os.makedirs("data/processed", exist_ok=True)
        cache.to_parquet(CACHE_PATH, index=False)
        # Write sidecar with dataset provenance
        meta = {
            "cache_updated":  str(date.today()),
            "era5_years":     f"{min(YEARS)}-{max(YEARS)}",
            "baseline_period": f"{BASELINE[0]}-{BASELINE[1]}",
            "recent_period":   f"{RECENT[0]}-{RECENT[1]}",
            "note": "Re-download era5_monthly.nc and delete this cache to refresh trends.",
        }
        with open(META_PATH, "w") as f:
            json.dump(meta, f, indent=2)
        print(f"[era5] Cache updated: {CACHE_PATH} ({len(new_df):,} places)")

    # Print sidecar info so user knows how current the data is
    if os.path.exists(META_PATH):
        with open(META_PATH) as f:
            meta = json.load(f)
        print(f"[era5] Dataset: {meta.get('era5_years')}  "
              f"cached {meta.get('cache_updated')}")

    return candidates.merge(cache[ERA5_COLS], on="geoid", how="left")
