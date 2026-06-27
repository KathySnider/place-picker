"""
pipeline/daymet.py
------------------
Fetches Daymet single-pixel climate data for each candidate place.

Daymet is a 1km-resolution spatially interpolated climate dataset from
NASA/ORNL. It accounts for elevation, terrain, and local effects (lake
influence, coastal effects) — much more accurate for specific locations
than county-level averages or distant weather stations.

Variables fetched:
    tmax  — daily max temperature (°C)
    tmin  — daily min temperature (°C)
    prcp  — daily precipitation (mm)
    swe   — snow water equivalent (kg/m²) — proxy for snowpack/snowfall

Derived outputs per place:
    winter_temp_f   — avg daily high Dec–Feb (°F)
    summer_temp_f   — avg daily high Jun–Aug (°F)
    annual_precip_mm
    snowfall_swe_mm — annual sum of SWE (proxy for snowiness)

Years averaged: configurable (default last 5 complete years).

Coverage: CONUS + Hawaii + Puerto Rico. Alaska is NOT covered by Daymet.
Rate limit: ~1 request/second to be polite to the ORNL server.
Results are cached in data/processed/daymet_cache.parquet.
"""

import time
import io
import random
import requests
import pandas as pd
import numpy as np
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db as _db

CACHE_PATH    = "data/processed/daymet_cache.parquet"
DAYMET_URL    = "https://daymet.ornl.gov/single-pixel/api/data"
RATE_LIMIT    = 1.2   # seconds between requests
REFRESH_DAYS  = 365   # re-fetch rows older than this many days
NUM_YEARS     = 5     # how many recent complete years to average
FLUSH_EVERY   = 25    # save cache to disk every N successful fetches

def _latest_complete_year() -> int:
    """
    Daymet releases each year's data with roughly a 6-12 month lag.
    Assume the previous calendar year is complete if we're past June;
    otherwise use the year before that to be safe.
    """
    today = date.today()
    return today.year - 1 if today.month >= 6 else today.year - 2

def _years() -> list[int]:
    last = _latest_complete_year()
    return list(range(last - NUM_YEARS + 1, last + 1))

# Months by season
WINTER_MONTHS = [12, 1, 2]
SUMMER_MONTHS = [6, 7, 8]


def _c_to_f(c: float) -> float:
    return c * 9 / 5 + 32


def _fetch_place(lat: float, lon: float) -> dict | None:
    """
    Fetch Daymet data for a single lat/lon point, averaged across YEARS.
    Returns a dict of derived climate metrics, or None on failure.
    """
    years = _years()
    params = {
        "lat":   lat,
        "lon":   lon,
        "vars":  "tmax,tmin,prcp,swe",
        "years": ",".join(str(y) for y in years),
    }
    resp = requests.get(DAYMET_URL, params=params, timeout=60)
    resp.raise_for_status()

    # Response is CSV with a 7-line header
    lines = resp.text.splitlines()
    header_end = next(i for i, l in enumerate(lines) if l.startswith("year"))
    df = pd.read_csv(io.StringIO("\n".join(lines[header_end:])))

    # Parse the 'yday' column into a month (approximate: 1-365 → month)
    # Daymet uses day-of-year; convert to month for seasonal aggregation
    df["month"] = pd.to_datetime(
        df["year"].astype(str) + "-" + df["yday"].astype(str),
        format="%Y-%j"
    ).dt.month

    winter = df[df["month"].isin(WINTER_MONTHS)]
    summer = df[df["month"].isin(SUMMER_MONTHS)]

    # Sum only positive day-to-day changes in SWE — this captures new snowfall
    # rather than counting the same snowpack sitting on the ground repeatedly.
    swe_series = df.groupby("year")["swe (kg/m^2)"].apply(
        lambda s: s.diff().clip(lower=0).sum()
    )
    swe_mm = round(swe_series.mean(), 1)
    # Approximate snow depth: 1mm SWE ≈ 10mm (0.39 in) of snow at avg density
    snowfall_in = round(swe_mm * 10 / 25.4, 1)

    return {
        "winter_temp_f":      round(_c_to_f(winter["tmax (deg c)"].mean()), 1),
        "summer_temp_f":      round(_c_to_f(summer["tmax (deg c)"].mean()), 1),
        "annual_precip_mm":   round(df["prcp (mm/day)"].sum() / len(years), 1),
        "snowfall_swe_mm":    swe_mm,
        "snowfall_in_approx": snowfall_in,
    }


def enrich(candidates: pd.DataFrame, stop_event=None) -> pd.DataFrame:
    """
    Add Daymet climate columns to the candidates DataFrame.

    Parameters
    ----------
    candidates : DataFrame with columns geoid, lat, lng, place_name, state_name

    Returns
    -------
    DataFrame with winter_temp_f, summer_temp_f, annual_precip_mm,
    snowfall_swe_mm added. Each row in the cache carries a fetched_at date;
    rows older than REFRESH_DAYS are re-fetched automatically.
    """
    climate_cols = ["geoid", "winter_temp_f", "summer_temp_f",
                    "annual_precip_mm", "snowfall_swe_mm", "snowfall_in_approx",
                    "fetched_at"]

    cache = _db.read_cache("daymet_cache", CACHE_PATH, climate_cols)
    if "fetched_at" not in cache.columns:
        cache["fetched_at"] = pd.NaT

    today = date.today()
    cutoff = pd.Timestamp(today) - pd.Timedelta(days=REFRESH_DAYS)

    # Stale = fetched_at is missing or older than cutoff
    if "fetched_at" in cache.columns:
        stale_mask = cache["fetched_at"].isna() | (cache["fetched_at"] < cutoff)
        stale_geoids = set(cache.loc[stale_mask, "geoid"].tolist())
    else:
        stale_geoids = set()

    cached_geoids = set(cache["geoid"].tolist())
    new_geoids    = set(candidates["geoid"].tolist()) - cached_geoids
    todo_geoids   = new_geoids | (stale_geoids & set(candidates["geoid"].tolist()))
    todo = candidates[candidates["geoid"].isin(todo_geoids)].copy()

    if todo.empty:
        print("[daymet] All candidates already cached and current.")
    else:
        n_new   = len(new_geoids & todo_geoids)
        n_stale = len(stale_geoids & todo_geoids)
        fetch_years = _years()
        print(f"[daymet] Fetching climate data for {len(todo)} places "
              f"({n_new} new, {n_stale} stale) "
              f"(years {fetch_years[0]}–{fetch_years[-1]})...")
        new_rows = []

        def _flush(label: str):
            nonlocal cache, new_rows
            if new_rows:
                new_df        = pd.DataFrame(new_rows)
                flushed_geoids = set(new_df["geoid"].tolist())
                # Remove only the geoids we're about to write (handles stale replacements)
                cache = cache[~cache["geoid"].isin(flushed_geoids)]
                cache = pd.concat([cache, new_df], ignore_index=True)
                _db.write_cache("daymet_cache", CACHE_PATH, cache)
                new_rows = []
                print(f"[daymet] {label} — cache updated")

        try:
            for i, row in enumerate(todo.itertuples(), 1):
                if stop_event and stop_event.is_set():
                    print("[daymet] Stop signal received — saving progress...")
                    break
                if pd.isna(row.lat) or pd.isna(row.lng):
                    new_rows.append({"geoid": row.geoid,
                                     **{c: None for c in climate_cols[1:-1]},
                                     "fetched_at": pd.Timestamp(today)})
                    continue

                try:
                    climate = _fetch_place(row.lat, row.lng)
                    jitter_days = random.randint(0, REFRESH_DAYS - 1)
                    fetch_date  = pd.Timestamp(today - timedelta(days=jitter_days))
                    new_rows.append({"geoid": row.geoid, **climate,
                                     "fetched_at": fetch_date})
                    print(f"  [daymet][{i}/{len(todo)}] {row.place_name}, {row.state_name} "
                          f"— winter: {climate['winter_temp_f']}°F  "
                          f"summer: {climate['summer_temp_f']}°F  "
                          f"snow: {climate['snowfall_in_approx']}\"")
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 400:
                        # 400 = location not covered by Daymet (ocean, outside CONUS, etc.)
                        # Cache as null so it stops retrying
                        new_rows.append({"geoid": row.geoid,
                                         **{c: None for c in climate_cols[1:-1]},
                                         "fetched_at": pd.Timestamp(today)})
                        print(f"  [daymet][{i}/{len(todo)}] {row.place_name} — "
                              f"not covered by Daymet (cached as null)")
                    else:
                        print(f"  [daymet][{i}/{len(todo)}] {row.place_name} — ERROR: {e} (will retry)")
                except Exception as e:
                    print(f"  [daymet][{i}/{len(todo)}] {row.place_name} — ERROR: {e} (will retry)")

                if len(new_rows) % FLUSH_EVERY == 0 and new_rows:
                    _flush("Cache flushed")

                # wait() wakes immediately if stop_event is set
                if stop_event:
                    stop_event.wait(RATE_LIMIT)
                else:
                    time.sleep(RATE_LIMIT)

        except KeyboardInterrupt:
            print("\n[daymet] Interrupted — saving progress...")
            _flush("Cache saved")
            return candidates.merge(cache[climate_cols], on="geoid", how="left")

        _flush("Cache updated")

    result = candidates.merge(
        cache[climate_cols],
        on="geoid",
        how="left",
    )
    return result
