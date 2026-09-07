"""
pipeline/elevation.py
---------------------
Fetches ground elevation (feet) for each census place using the USGS
National Map Elevation Point Query Service (EPQS).

No API key required. Covers all 50 US states including Alaska and Hawaii.

Output column:
    elevation_ft  — ground elevation in feet (NaN if fetch fails)
"""

import os
import sys
import time

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db as _db

CACHE_PATH = "data/processed/elevation_cache.parquet"
ELEV_COLS  = ["geoid", "elevation_ft"]

EPQS_URL = (
    "https://epqs.nationalmap.gov/v1/json"
    "?x={lon}&y={lat}&wkid=4326&units=Feet&includeDate=false"
)
HEADERS     = {"User-Agent": "place-picker/1.0 (personal location research)"}
RATE_LIMIT  = 0.25   # seconds between requests — USGS asks for polite use
FLUSH_EVERY = 100
REFRESH_DAYS = 3650  # elevation doesn't change; re-fetch only after 10 years


def enrich(candidates: pd.DataFrame, cache_only: bool = False) -> pd.DataFrame:
    os.makedirs("data/processed", exist_ok=True)

    cache = _db.read_cache("elevation_cache", CACHE_PATH, ELEV_COLS)
    if "fetched_at" not in cache.columns:
        cache["fetched_at"] = pd.NaT
    else:
        cache["fetched_at"] = pd.to_datetime(cache["fetched_at"], errors="coerce")

    today  = pd.Timestamp(pd.Timestamp.now().date())
    cutoff = today - pd.Timedelta(days=REFRESH_DAYS)

    cached_geoids = set(cache["geoid"])
    stale_geoids  = set(cache.loc[cache["fetched_at"] < cutoff, "geoid"]) if len(cache) else set()
    todo_geoids   = (set(candidates["geoid"]) - cached_geoids) | (stale_geoids & set(candidates["geoid"]))
    todo = candidates[candidates["geoid"].isin(todo_geoids)].drop_duplicates("geoid").copy()

    if todo.empty:
        print("[elevation] All candidates already cached.")
    elif cache_only:
        print(f"[elevation] cache_only — skipping {len(todo)} uncached places")
    else:
        print(f"[elevation] Fetching USGS elevation for {len(todo)} places...")

        new_rows = []

        def _flush(current_cache):
            if not new_rows:
                return current_cache
            ndf = pd.DataFrame(new_rows)
            updated = set(ndf["geoid"])
            merged = pd.concat(
                [current_cache[~current_cache["geoid"].isin(updated)], ndf],
                ignore_index=True,
            )
            _db.write_cache("elevation_cache", CACHE_PATH, merged)
            new_rows.clear()
            return merged

        for i, row in enumerate(todo.itertuples(), 1):
            lat = getattr(row, "lat", None)
            lon = getattr(row, "lng", None)
            elev_ft = float("nan")

            if lat is not None and lon is not None and not pd.isna(lat) and not pd.isna(lon):
                url = EPQS_URL.format(lat=lat, lon=lon)
                try:
                    r = requests.get(url, headers=HEADERS, timeout=15)
                    r.raise_for_status()
                    data = r.json()
                    raw = data.get("value", None)
                    if raw is not None:
                        v = float(raw)
                        if v > -1000:   # USGS returns -1000000 for ocean/error
                            elev_ft = round(v, 1)
                except Exception as e:
                    print(f"  [elevation] fetch error for {row.geoid}: {e}", flush=True)

                time.sleep(RATE_LIMIT)

            new_rows.append({
                "geoid":        row.geoid,
                "elevation_ft": elev_ft if not np.isnan(elev_ft) else None,
                "fetched_at":   today,
            })

            if i % FLUSH_EVERY == 0:
                cache = _flush(cache)
                print(f"[elevation] Saved progress ({i}/{len(todo)})", flush=True)

        cache = _flush(cache)
        print(f"[elevation] Done — {len(todo)} places fetched.")

    available = [c for c in ELEV_COLS if c in cache.columns]
    # Merge into candidates; if candidates already has elevation_ft, overwrite
    if "elevation_ft" in candidates.columns:
        candidates = candidates.drop(columns=["elevation_ft"])
    return candidates.merge(cache[available], on="geoid", how="left")
