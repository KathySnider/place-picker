"""
refresh_cache.py
----------------
Background cache refresh worker — runs continuously on Railway (or locally).

Each pass fetches and caches OSM, Daymet, PRISM, and ERA5 data for all
Census places matching broad criteria. Stale entries are refreshed
automatically by each pipeline module's REFRESH_DAYS logic.

Run once:   python refresh_cache.py
Run loop:   python refresh_cache.py --loop   (default on Railway)

Environment variables:
    DATABASE_URL      — Postgres connection string (required on Railway)
    REFRESH_INTERVAL  — hours between passes (default: 24)
    CDS_API_KEY       — required for ERA5 downloads
"""

import argparse
import sys
import time
import traceback
from datetime import datetime

from pipeline import census, osm, daymet, prism, era5, facilities, state_tax
from regions import CONUS


# Broad criteria — catch everything a user might plausibly search for.
# Tighter searches will always find a cached subset of this universe.
POPULATION = {"min": 1000, "max": 150000}
STATES     = list(CONUS)   # all contiguous US states (passed as states=, not regions=)
METRO_MAX  = None          # no metro filter — cache everything


def _log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def run_pass():
    _log("=== Cache refresh pass starting ===")
    t0 = time.time()

    # Step 1: Census — full broad load
    _log("Loading census places...")
    candidates = census.load(
        population=POPULATION,
        regions=[],
        states=STATES,
        metro_max=METRO_MAX,
    )
    candidates = state_tax.enrich(candidates)
    _log(f"Census: {len(candidates):,} places")

    # Step 2: OSM walkability (live fetches for uncached/stale)
    _log("Enriching OSM walkability...")
    try:
        candidates = osm.enrich(candidates, cache_only=False)
        _log(f"OSM done — {candidates['practical_800m'].notna().sum():,} places have walkability data")
    except Exception:
        _log("OSM pass failed:")
        traceback.print_exc()

    # Step 3: Daymet climate
    _log("Enriching Daymet climate...")
    try:
        candidates = daymet.enrich(candidates, cache_only=False)
        _log(f"Daymet done — {candidates['winter_temp_f'].notna().sum():,} places have climate data")
    except Exception:
        _log("Daymet pass failed:")
        traceback.print_exc()

    # Step 4: PRISM climate normals
    _log("Enriching PRISM...")
    try:
        candidates = prism.enrich(candidates, cache_only=False)
        _log(f"PRISM done — {candidates['prism_winter_f'].notna().sum():,} places have PRISM data")
    except Exception:
        _log("PRISM pass failed:")
        traceback.print_exc()

    # Step 5: ERA5 warming trends
    _log("Enriching ERA5...")
    try:
        candidates = era5.enrich(candidates, cache_only=False)
        _log(f"ERA5 done — {candidates['summer_trend_f_dec'].notna().sum():,} places have trend data")
    except Exception:
        _log("ERA5 pass failed (CDS_API_KEY required):")
        traceback.print_exc()

    # Step 6: Facilities
    _log("Enriching facilities...")
    try:
        candidates = facilities.enrich(candidates)
        _log(f"Facilities done — {candidates['hospital_distance_miles'].notna().sum():,} places enriched")
    except Exception:
        _log("Facilities pass failed:")
        traceback.print_exc()

    elapsed = time.time() - t0
    _log(f"=== Pass complete in {elapsed / 3600:.1f}h ===")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", default=False,
                        help="Run continuously (default: run once)")
    parser.add_argument("--interval", type=float, default=24.0,
                        help="Hours between passes when --loop is set (default: 24)")
    args = parser.parse_args()

    if not args.loop:
        run_pass()
        return

    _log(f"Starting loop mode — interval: {args.interval}h")
    while True:
        try:
            run_pass()
        except Exception:
            _log("Unexpected error in refresh pass:")
            traceback.print_exc()
        _log(f"Sleeping {args.interval}h until next pass...")
        time.sleep(args.interval * 3600)


if __name__ == "__main__":
    main()
