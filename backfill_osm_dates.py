"""
One-time script to backfill fetched_at dates in the OSM cache.

Splits existing rows into thirds, with jitter so refreshes spread out
day-by-day rather than hitting all at once when a third goes stale:

  - 1/3  NaT                        → stale now, re-fetched on next run
  - 1/3  random date in [91..180] days ago  → re-fetches spread over next 3 months
  - 1/3  random date in [0..90] days ago    → re-fetches spread over 3-6 months out

Run once:  python backfill_osm_dates.py
"""

import pandas as pd
import numpy as np
from datetime import date

CACHE_PATH   = "data/processed/osm_cache.parquet"
REFRESH_DAYS = 180

cache = pd.read_parquet(CACHE_PATH)
print(f"Loaded {len(cache):,} rows from {CACHE_PATH}")

if "fetched_at" not in cache.columns:
    cache["fetched_at"] = pd.NaT
else:
    cache["fetched_at"] = pd.to_datetime(cache["fetched_at"])

unfilled = cache["fetched_at"].isna()
n = unfilled.sum()
print(f"  {n:,} rows need backfill, {(~unfilled).sum():,} already dated")

if n == 0:
    print("Nothing to do.")
else:
    idx = cache[unfilled].index.tolist()
    rng = np.random.default_rng(seed=42)
    rng.shuffle(idx)

    third = n // 3
    today = pd.Timestamp(date.today())

    # First third: leave as NaT → stale immediately

    # Second third: random dates in the window that will expire in 0-90 days
    # i.e. fetched 91-180 days ago
    mid_idx = idx[third:2*third]
    mid_offsets = rng.integers(91, REFRESH_DAYS + 1, size=len(mid_idx))
    for i, offset in zip(mid_idx, mid_offsets):
        cache.at[i, "fetched_at"] = today - pd.Timedelta(days=int(offset))

    # Final third: random dates in the window that won't expire for 90-180 days
    # i.e. fetched 0-90 days ago
    fresh_idx = idx[2*third:]
    fresh_offsets = rng.integers(0, 91, size=len(fresh_idx))
    for i, offset in zip(fresh_idx, fresh_offsets):
        cache.at[i, "fetched_at"] = today - pd.Timedelta(days=int(offset))

    stale = cache["fetched_at"].isna().sum()
    mid   = ((cache["fetched_at"].notna()) &
             (cache["fetched_at"] < today - pd.Timedelta(days=90))).sum()
    fresh = (cache["fetched_at"] >= today - pd.Timedelta(days=90)).sum()

    print(f"  Stale (re-fetch now):             {stale:,}")
    print(f"  Mid   (re-fetch over next 3 mo):  {mid:,}")
    print(f"  Fresh (re-fetch 3-6 mo from now): {fresh:,}")
    print(f"  Expected ~{mid // 90:.0f} re-fetches/day once mid window opens")

    cache.to_parquet(CACHE_PATH, index=False)
    print(f"Saved to {CACHE_PATH}")
