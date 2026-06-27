"""
push_cache.py
-------------
One-time script to push existing local parquet caches into Railway Postgres.
Run this before search.py to avoid re-fetching data that's already cached locally.

Usage:
    python push_cache.py
"""
import os
import sys
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

if not os.environ.get("DATABASE_URL"):
    print("ERROR: DATABASE_URL not set in .env")
    sys.exit(1)

import db as _db

TABLES = [
    ("osm_cache",        "data/processed/osm_cache.parquet"),
    ("osm_detail_cache", "data/processed/osm_detail_cache.parquet"),
    ("osm_trails_cache", "data/processed/osm_trails_cache.parquet"),
    ("daymet_cache",     "data/processed/daymet_cache.parquet"),
    ("prism_cache",      "data/processed/prism_cache.parquet"),
    ("era5_cache",       "data/processed/era5_cache.parquet"),
    ("facilities_cache", "data/processed/facilities_cache.parquet"),
    ("coastal_cache",    "data/processed/coastal_cache.parquet"),
    ("census_places",    "data/processed/census_places.parquet"),
]

for table, path in TABLES:
    if not os.path.exists(path):
        print(f"  SKIP  {table} — no local parquet found")
        continue
    df = pd.read_parquet(path)
    print(f"  PUSH  {table} — {len(df):,} rows... ", end="", flush=True)
    _db.write_cache(table, path, df)
    print("done")

print("\nAll done. Re-run search.py now.")
