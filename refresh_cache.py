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

import db as _db

from pipeline import census, elevation, osm, daymet, prism, noaa_normals, era5, facilities, state_tax, osm_detail, osm_trails
from regions import CONUS


# Broad criteria — catch everything a user might plausibly search for.
# Tighter searches will always find a cached subset of this universe.
POPULATION = {"min": 1000, "max": 150000}
STATES     = list(CONUS) + ["Alaska", "Hawaii"]   # all US states including AK and HI
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

    # Step 2: Elevation (USGS EPQS — needed for NOAA station matching)
    _log("Enriching elevation...")
    try:
        candidates = elevation.enrich(candidates, cache_only=False)
        _log(f"Elevation done — {candidates['elevation_ft'].notna().sum():,} places have elevation data")
    except Exception:
        _log("Elevation pass failed (non-fatal):")
        traceback.print_exc()

    # Step 4: OSM walkability (live fetches for uncached/stale)
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
    # Purge non-CONUS rows from the PRISM cache so they get recomputed with
    # the corrected NaN logic (old rows had prism_snow_in=0.0 from nansum bug).
    # PRISM rasters are already on disk — this is a fast re-extraction, not a re-download.
    # One-time: purge non-CONUS rows that were cached with prism_snow_in=0.0
    # from the nansum bug. Once they're gone the check finds nothing to remove.
    try:
        NON_CONUS_STATES = {"Alaska", "Hawaii"}
        non_conus_geoids = set(candidates.loc[
            candidates["state_name"].isin(NON_CONUS_STATES), "geoid"
        ])
        if non_conus_geoids:
            prism_cache = _db.read_cache("prism_cache", prism.CACHE_PATH, [])
            if not prism_cache.empty and "geoid" in prism_cache.columns:
                overlap = set(prism_cache["geoid"]) & non_conus_geoids
                if overlap:
                    prism_cache = prism_cache[~prism_cache["geoid"].isin(overlap)]
                    _db.write_cache_replace("prism_cache", prism.CACHE_PATH, prism_cache)
                    _log(f"Purged {len(overlap)} non-CONUS rows from PRISM cache — will recompute as NaN")
    except Exception:
        _log("PRISM cache purge failed (non-fatal):")
        traceback.print_exc()

    _log("Enriching PRISM...")
    try:
        candidates = prism.enrich(candidates, cache_only=False)
        _log(f"PRISM done — {candidates['prism_winter_f'].notna().sum():,} places have PRISM data")
    except Exception:
        _log("PRISM pass failed:")
        traceback.print_exc()

    # Step 5: NOAA station-based snow normals (AK/HI + coastal places)
    _log("Enriching NOAA climate normals...")
    try:
        candidates = noaa_normals.enrich(candidates, cache_only=False)
        _log(f"NOAA normals done — {candidates['noaa_snow_in'].notna().sum():,} places have station snow data")
    except Exception:
        _log("NOAA normals pass failed:")
        traceback.print_exc()

    # Step 6: ERA5 warming trends
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

    # Step 7: Re-freshen any osm_detail and osm_trails rows already in cache
    # (can't pre-populate — we don't know which places will rank top 25,
    #  but we can keep previously-seen top results fresh)
    _log("Refreshing stale osm_detail entries...")
    try:
        detail_cache = _db.read_cache("osm_detail_cache", osm_detail.CACHE_PATH, osm_detail.DETAIL_COLS)
        if not detail_cache.empty:
            candidates_subset = candidates[candidates["geoid"].isin(detail_cache["geoid"])]
            if not candidates_subset.empty:
                osm_detail.enrich(candidates_subset)
                _log(f"osm_detail refreshed {len(candidates_subset):,} cached places")
    except Exception:
        _log("osm_detail refresh failed:")
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
