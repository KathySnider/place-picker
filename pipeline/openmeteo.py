"""
pipeline/openmeteo.py
---------------------
Fetches 30-year climate normals (1991-2020) from the Open-Meteo Climate API.

Model: MRI_AGCM3_2_S — handles terrain and lake-effect better than Daymet's
SWE proxy, especially for mountainous regions and Great Lakes snow belts.

Variables fetched:
    snowfall_sum      — daily snowfall (cm); summed to annual inches
    temperature_2m_max — daily max temperature (°C)

Derived outputs per place:
    om_snowfall_in   — avg annual snowfall (inches), 30-year normal
    om_winter_temp_f — avg daily high Dec-Feb (°F)
    om_summer_temp_f — avg daily high Jun-Aug (°F)

Coverage: CONUS + most of the world. Rate limit: ~1 request/second.
Cache flushed every FLUSH_EVERY successful fetches.
"""

import time
import random
import requests
import pandas as pd
import numpy as np
import os
from datetime import date, timedelta

CACHE_PATH   = "data/processed/openmeteo_cache.parquet"
BASE_URL     = "https://climate-api.open-meteo.com/v1/climate"
MODEL        = "MRI_AGCM3_2_S"
START_DATE   = "1991-01-01"
END_DATE     = "2020-12-31"
RATE_LIMIT   = 2.0    # seconds between requests
REFRESH_DAYS = 730    # 30-year normals don't change — refresh every 2 years
FLUSH_EVERY  = 25

WINTER_MONTHS = [12, 1, 2]
SUMMER_MONTHS = [6, 7, 8]


def _fetch_place(lat: float, lon: float) -> dict | None:
    params = {
        "latitude":   lat,
        "longitude":  lon,
        "start_date": START_DATE,
        "end_date":   END_DATE,
        "models":     MODEL,
        "daily":      "snowfall_sum,temperature_2m_max",
    }
    for attempt in range(3):
        resp = requests.get(BASE_URL, params=params, timeout=60)
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)
            print(f"    [openmeteo] 429 rate limit — waiting {wait}s...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        break
    data = resp.json()

    if "error" in data:
        raise ValueError(data.get("reason", "Unknown error from Open-Meteo"))

    df = pd.DataFrame(data["daily"])
    df["date"]  = pd.to_datetime(df["time"])
    df["month"] = df["date"].dt.month

    # Annual snowfall: sum per year then average across years (cm → inches)
    annual_snow_in = (
        df.groupby(df["date"].dt.year)["snowfall_sum"]
        .sum()
        .mean()
    ) / 2.54

    winter = df[df["month"].isin(WINTER_MONTHS)]["temperature_2m_max"].mean()
    summer = df[df["month"].isin(SUMMER_MONTHS)]["temperature_2m_max"].mean()

    def c_to_f(c): return round(c * 9 / 5 + 32, 1)

    return {
        "om_snowfall_in":   round(annual_snow_in, 1),
        "om_winter_temp_f": c_to_f(winter),
        "om_summer_temp_f": c_to_f(summer),
    }


def enrich(candidates: pd.DataFrame, stop_event=None) -> pd.DataFrame:
    """
    Add om_snowfall_in, om_winter_temp_f, om_summer_temp_f to candidates.
    Uses 30-year normals (1991-2020) from Open-Meteo MRI_AGCM3_2_S model.
    """
    cache_cols = ["geoid", "om_snowfall_in", "om_winter_temp_f",
                  "om_summer_temp_f", "fetched_at"]

    if os.path.exists(CACHE_PATH):
        cache = pd.read_parquet(CACHE_PATH)
        for col in cache_cols[1:]:
            if col not in cache.columns:
                cache[col] = None
    else:
        cache = pd.DataFrame(columns=cache_cols)

    today   = date.today()
    cutoff  = pd.Timestamp(today) - pd.Timedelta(days=REFRESH_DAYS)

    stale_mask   = cache["fetched_at"].isna() | (cache["fetched_at"] < cutoff)
    stale_geoids = set(cache.loc[stale_mask, "geoid"].tolist())
    cached_geoids = set(cache["geoid"].tolist())
    new_geoids    = set(candidates["geoid"].tolist()) - cached_geoids
    todo_geoids   = new_geoids | (stale_geoids & set(candidates["geoid"].tolist()))
    todo = candidates[candidates["geoid"].isin(todo_geoids)].copy()

    if todo.empty:
        print("[openmeteo] All candidates already cached.")
    else:
        n_new   = len(new_geoids & todo_geoids)
        n_stale = len(stale_geoids & todo_geoids)
        print(f"[openmeteo] Fetching 30-year climate normals for {len(todo)} places "
              f"({n_new} new, {n_stale} stale)...")

        new_rows = []

        def _flush(label: str):
            nonlocal cache, new_rows
            if new_rows:
                new_df         = pd.DataFrame(new_rows)
                flushed_geoids = set(new_df["geoid"].tolist())
                cache = cache[~cache["geoid"].isin(flushed_geoids)]
                cache = pd.concat([cache, new_df], ignore_index=True)
                os.makedirs("data/processed", exist_ok=True)
                cache.to_parquet(CACHE_PATH, index=False)
                new_rows = []
                print(f"[openmeteo] {label} → {CACHE_PATH}")

        try:
            for i, row in enumerate(todo.itertuples(), 1):
                if stop_event and stop_event.is_set():
                    print("[openmeteo] Stop signal received — saving progress...")
                    break
                if pd.isna(row.lat) or pd.isna(row.lng):
                    new_rows.append({"geoid": row.geoid,
                                     **{c: None for c in cache_cols[1:-1]},
                                     "fetched_at": pd.Timestamp(today)})
                    continue

                try:
                    climate = _fetch_place(row.lat, row.lng)
                    jitter_days = random.randint(0, REFRESH_DAYS - 1)
                    fetch_date  = pd.Timestamp(today - timedelta(days=jitter_days))
                    new_rows.append({"geoid": row.geoid, **climate,
                                     "fetched_at": fetch_date})
                    print(f"  [{i}/{len(todo)}] {row.place_name}, {row.state_name} "
                          f"— snow: {climate['om_snowfall_in']}\"  "
                          f"winter: {climate['om_winter_temp_f']}°F  "
                          f"summer: {climate['om_summer_temp_f']}°F")
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 400:
                        new_rows.append({"geoid": row.geoid,
                                         **{c: None for c in cache_cols[1:-1]},
                                         "fetched_at": pd.Timestamp(today)})
                        print(f"  [{i}/{len(todo)}] {row.place_name} — "
                              f"not covered by Open-Meteo (cached as null)")
                    else:
                        print(f"  [{i}/{len(todo)}] {row.place_name} — ERROR: {e} (will retry)")
                except Exception as e:
                    print(f"  [{i}/{len(todo)}] {row.place_name} — ERROR: {e} (will retry)")

                if len(new_rows) % FLUSH_EVERY == 0 and new_rows:
                    _flush("Cache flushed")

                if stop_event:
                    stop_event.wait(RATE_LIMIT)
                else:
                    time.sleep(RATE_LIMIT)

        except KeyboardInterrupt:
            print("\n[openmeteo] Interrupted — saving progress...")
            _flush("Cache saved")
            return candidates.merge(cache[cache_cols], on="geoid", how="left")

        _flush("Cache updated")

    return candidates.merge(cache[cache_cols], on="geoid", how="left")
