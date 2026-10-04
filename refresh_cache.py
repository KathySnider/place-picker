"""
refresh_cache.py
----------------
Background cache refresh worker — runs continuously on Railway (or locally).

Two threads run in parallel each pass:

  Thread 1 — OSM (Overpass-dependent, slow):
    1. OSM walkability  — up to STALE_PER_RUN stale + all new places
    2. OSM detail       — top known results first, then up to BACKFILL_PER_PASS
                          uncached places from the full walkability set
    3. OSM trails       — same priority: known results first, then up to
                          BACKFILL_PER_PASS uncached from walkability set

  Thread 2 — Everything else (fast I/O, no Overpass):
    Elevation → Daymet → PRISM → NOAA normals → ERA5 → Facilities

Both threads receive a copy of the full census candidates DataFrame.
The main thread waits for both, logs elapsed time, then sleeps.

Environment variables:
    DATABASE_URL      — Postgres connection string (required on Railway)
    REFRESH_INTERVAL  — hours between passes (default: 24)
    CDS_API_KEY       — required for ERA5 downloads
"""

import argparse
import sys
import threading
import time
import traceback
from datetime import datetime

import pandas as pd

import db as _db
from pipeline import (
    census, elevation, osm, daymet, prism, noaa_normals, wrcc,
    era5, facilities, state_tax, osm_detail, osm_trails,
)
from regions import CONUS


POPULATION       = {"min": 1000, "max": 150000}
STATES           = list(CONUS) + ["Alaska", "Hawaii"]
METRO_MAX        = None
BACKFILL_PER_PASS = 200   # uncached detail/trails places to backfill per pass


def _log(msg: str):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Thread 1: OSM (walkability → detail → trails)
# ---------------------------------------------------------------------------

def _osm_thread(candidates: pd.DataFrame):
    # 1. OSM walkability (stale cap enforced inside osm.enrich)
    _log("OSM: Enriching walkability...")
    try:
        candidates = osm.enrich(candidates, cache_only=False)
        n_walk = candidates["practical_800m"].notna().sum()
        _log(f"OSM: Walkability done — {n_walk:,} places have data")
    except Exception:
        _log("OSM: Walkability pass failed:")
        traceback.print_exc()

    # Build the full set of places that have walkability data — used to
    # prioritize and backfill detail + trails caches.
    walk_geoids = set(
        candidates.loc[candidates["practical_800m"].notna(), "geoid"].tolist()
    )
    walk_candidates = candidates[candidates["geoid"].isin(walk_geoids)].copy()

    # 2. OSM detail — top known results first, then backfill
    _log("OSM: Enriching detail cache...")
    try:
        detail_cache = _db.read_cache(
            "osm_detail_cache", osm_detail.CACHE_PATH, osm_detail.DETAIL_COLS
        )
        detail_geoids   = set(detail_cache["geoid"]) if not detail_cache.empty else set()
        uncached_detail = walk_geoids - detail_geoids

        # Known results first (staleness check + refresh)
        if detail_geoids:
            known = walk_candidates[walk_candidates["geoid"].isin(detail_geoids)]
            if not known.empty:
                osm_detail.enrich(known)

        # Backfill up to BACKFILL_PER_PASS uncached places
        if uncached_detail:
            backfill_geoids = set(list(uncached_detail)[:BACKFILL_PER_PASS])
            backfill = walk_candidates[walk_candidates["geoid"].isin(backfill_geoids)]
            if not backfill.empty:
                _log(f"OSM: Backfilling detail for {len(backfill):,} places "
                     f"({len(uncached_detail):,} total uncached)...")
                osm_detail.enrich(backfill)

        total_detail = len(detail_geoids) + min(len(uncached_detail), BACKFILL_PER_PASS)
        _log(f"OSM: Detail done — ~{total_detail:,} places in cache")
    except Exception:
        _log("OSM: Detail pass failed:")
        traceback.print_exc()

    # 3. OSM trails — same priority logic
    _log("OSM: Enriching trails cache...")
    try:
        trails_cache = _db.read_cache(
            "osm_trails_cache", osm_trails.CACHE_PATH, osm_trails.TRAIL_COLS
        )
        trails_geoids   = set(trails_cache["geoid"]) if not trails_cache.empty else set()
        uncached_trails = walk_geoids - trails_geoids

        # Known results first (staleness check + refresh)
        if trails_geoids:
            known = walk_candidates[walk_candidates["geoid"].isin(trails_geoids)]
            if not known.empty:
                osm_trails.enrich(known)

        # Backfill up to BACKFILL_PER_PASS uncached places
        if uncached_trails:
            backfill_geoids = set(list(uncached_trails)[:BACKFILL_PER_PASS])
            backfill = walk_candidates[walk_candidates["geoid"].isin(backfill_geoids)]
            if not backfill.empty:
                _log(f"OSM: Backfilling trails for {len(backfill):,} places "
                     f"({len(uncached_trails):,} total uncached)...")
                osm_trails.enrich(backfill)

        total_trails = len(trails_geoids) + min(len(uncached_trails), BACKFILL_PER_PASS)
        _log(f"OSM: Trails done — ~{total_trails:,} places in cache")
    except Exception:
        _log("OSM: Trails pass failed:")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Thread 2: Everything else
# ---------------------------------------------------------------------------

def _enrich_thread(candidates: pd.DataFrame):
    # Elevation (needed for NOAA station matching)
    _log("Enriching elevation...")
    try:
        candidates = elevation.enrich(candidates, cache_only=False)
        _log(f"Elevation done — {candidates['elevation_ft'].notna().sum():,} places have elevation data")
    except Exception:
        _log("Elevation pass failed (non-fatal):")
        traceback.print_exc()

    # Daymet climate
    _log("Enriching Daymet climate...")
    try:
        candidates = daymet.enrich(candidates, cache_only=False)
        _log(f"Daymet done — {candidates['winter_temp_f'].notna().sum():,} places have climate data")
    except Exception:
        _log("Daymet pass failed:")
        traceback.print_exc()

    # PRISM climate normals
    _log("Enriching PRISM...")
    try:
        candidates = prism.enrich(candidates, cache_only=False)
        _log(f"PRISM done — {candidates['prism_winter_f'].notna().sum():,} places have PRISM data")
    except Exception:
        _log("PRISM pass failed:")
        traceback.print_exc()

    # NOAA station-based normals (AK/HI + coastal)
    _log("Enriching NOAA climate normals...")
    try:
        candidates = noaa_normals.enrich(candidates, cache_only=False)
        _log(f"NOAA normals done — {candidates['noaa_snow_in'].notna().sum():,} places have station snow data")
    except Exception:
        _log("NOAA normals pass failed:")
        traceback.print_exc()

    # WRCC/ACIS fallback for places still missing snow or temperature after NOAA
    _log("Enriching WRCC/ACIS climate fallback...")
    try:
        candidates = wrcc.enrich(candidates, cache_only=False)
        n_wrcc_snow = candidates["wrcc_snow_in"].notna().sum() if "wrcc_snow_in" in candidates.columns else 0
        _log(f"WRCC done — {n_wrcc_snow:,} places filled via ACIS fallback")
    except Exception:
        _log("WRCC pass failed (non-fatal):")
        traceback.print_exc()

    # ERA5 warming trends
    _log("Enriching ERA5...")
    try:
        candidates = era5.enrich(candidates, cache_only=False)
        _log(f"ERA5 done — {candidates['summer_trend_f_dec'].notna().sum():,} places have trend data")
    except Exception:
        _log("ERA5 pass failed (CDS_API_KEY required):")
        traceback.print_exc()

    # Facilities (hospitals, colleges, libraries)
    _log("Enriching facilities...")
    try:
        candidates = facilities.enrich(candidates)
        _log(f"Facilities done — {candidates['hospital_distance_miles'].notna().sum():,} places enriched")
    except Exception:
        _log("Facilities pass failed:")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Main pass
# ---------------------------------------------------------------------------

def run_pass():
    _log("=== Cache refresh pass starting ===")
    t0 = time.time()

    _log("Loading census places...")
    candidates = census.load(
        population=POPULATION,
        regions=[],
        states=STATES,
        metro_max=METRO_MAX,
    )
    candidates = state_tax.enrich(candidates)
    _log(f"Census: {len(candidates):,} places")

    # Give each thread its own copy so they don't share DataFrame state
    t1 = threading.Thread(
        target=_osm_thread,
        args=(candidates.copy(),),
        name="osm-thread",
        daemon=True,
    )
    t2 = threading.Thread(
        target=_enrich_thread,
        args=(candidates.copy(),),
        name="enrich-thread",
        daemon=True,
    )

    t1.start()
    t2.start()
    t1.join()
    t2.join()

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
