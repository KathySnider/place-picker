#!/usr/bin/env python3
"""
recover_wrcc_station_ids.py
---------------------------
One-time recovery: fills NULL station IDs in wrcc_cache by re-running StnMeta
for each town and matching the returned stations against the stored name,
distance, and elevation difference.

Does NOT re-run StnData or alter any snowfall / temperature values.
Recovers all three metrics (snow, tmax, tavg) independently.
Ambiguous or unverifiable matches are left NULL and reported.

Run from the place-picker root directory:
    python recover_wrcc_station_ids.py
"""

import math
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(__file__))
import db as _db
from pipeline import census, elevation
from pipeline.wrcc import (
    ACIS_BASE, CACHE_PATH, HEADERS, MAX_DIST_MI, WRCC_COLS,
    _bbox_for, _haversine_mi, _preferred_sid,
)
from regions import CONUS

# ── Matching tolerances ────────────────────────────────────────────────────────
# Stored dist_mi is round(..., 2) → max rounding error 0.005; use 0.02 for slop.
# Stored elev_diff is round(..., 0) → max rounding error 0.5; use 1.5 for slop.
DIST_TOL_MI   = 0.02
ELEV_DIFF_TOL = 1.5

# Three metrics, each with its own set of station columns
METRICS = [
    (
        "snow",
        "wrcc_winter_snow_station_name",
        "wrcc_winter_snow_station_dist_mi",
        "wrcc_winter_snow_station_elev_diff_ft",
        "wrcc_winter_snow_station_id",
    ),
    (
        "tmax",
        "wrcc_july_tmax_station_name",
        "wrcc_july_tmax_station_dist_mi",
        "wrcc_july_tmax_station_elev_diff_ft",
        "wrcc_july_tmax_station_id",
    ),
    (
        "tavg",
        "wrcc_winter_tavg_station_name",
        "wrcc_winter_tavg_station_dist_mi",
        "wrcc_winter_tavg_station_elev_diff_ft",
        "wrcc_winter_tavg_station_id",
    ),
]


# ── ACIS StnMeta ───────────────────────────────────────────────────────────────

def _stn_meta(lat: float, lon: float) -> list[dict]:
    """Return stations within MAX_DIST_MI of (lat, lon), enriched with dist_mi."""
    bbox = _bbox_for(lat, lon)
    payload = {"bbox": bbox, "meta": "name,state,sids,ll,elev,uid"}
    try:
        resp = requests.post(
            f"{ACIS_BASE}/StnMeta",
            json=payload,
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  StnMeta error: {e}", flush=True)
        return []

    stations = []
    for s in data.get("meta", []):
        ll = s.get("ll")
        if not ll or len(ll) < 2:
            continue
        slon, slat = float(ll[0]), float(ll[1])
        dist = _haversine_mi(lat, lon, slat, slon)
        if dist > MAX_DIST_MI:
            continue
        stations.append({
            "name":    s.get("name", ""),
            "elev":    s.get("elev"),       # feet, may be None
            "sids":    s.get("sids", []),
            "dist_mi": dist,
        })
    return stations


# ── Station matching ───────────────────────────────────────────────────────────

def _is_nan(v) -> bool:
    try:
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


def _match_station(
    stations: list[dict],
    stored_name: str,
    stored_dist_mi: float,
    stored_elev_diff,   # float or None/NaN when town elevation was unknown
    town_elev_ft,       # float or None/NaN
) -> tuple[str | None, str]:
    """
    Find the unique station matching stored metadata.

    Matching rules (applied in order; a station must pass all applicable checks):
      1. Name must match exactly (case-insensitive).
      2. Distance must be within DIST_TOL_MI of stored_dist_mi.
      3. If stored_elev_diff is not NULL and town elevation is known:
         recompute abs(station_elev - town_elev) and check it is within
         ELEV_DIFF_TOL of stored_elev_diff.

    Returns:
      (sid, "ok")        — exactly one match; sid is the preferred station ID
      (None, "no_match") — zero candidates passed all checks
      (None, "ambiguous")— more than one candidate passed all checks
    """
    have_elev_check = (
        stored_elev_diff is not None
        and not _is_nan(stored_elev_diff)
        and not _is_nan(town_elev_ft)
    )

    matched = []
    for stn in stations:
        # 1. Name
        if stn["name"].upper() != stored_name.upper():
            continue

        # 2. Distance
        if abs(stn["dist_mi"] - stored_dist_mi) > DIST_TOL_MI:
            continue

        # 3. Elevation difference (only when we have the data to check)
        if have_elev_check and stn["elev"] is not None:
            computed_diff = abs(float(stn["elev"]) - float(town_elev_ft))
            if abs(computed_diff - float(stored_elev_diff)) > ELEV_DIFF_TOL:
                continue

        matched.append(stn)

    if len(matched) == 1:
        sid = _preferred_sid(matched[0]["sids"])
        return sid, "ok"
    if len(matched) == 0:
        return None, "no_match"
    return None, "ambiguous"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Loading wrcc_cache...", flush=True)
    cache = _db.read_cache("wrcc_cache", CACHE_PATH, WRCC_COLS)
    if cache.empty:
        print("wrcc_cache is empty — nothing to recover.")
        return
    print(f"  {len(cache):,} rows loaded.", flush=True)

    # Ensure all station_id columns exist (they should, but may be all-NULL)
    for _, _, _, _, col_id in METRICS:
        if col_id not in cache.columns:
            cache[col_id] = None

    print("Loading census + elevation (cache_only)...", flush=True)
    candidates = census.load(
        population={"min": 1000, "max": 150000},
        regions=[],
        states=list(CONUS) + ["Alaska", "Hawaii"],
        metro_max=None,
    )
    candidates = elevation.enrich(candidates, cache_only=True)
    loc = (
        candidates[["geoid", "lat", "lng", "elevation_ft"]]
        .drop_duplicates("geoid")
        .set_index("geoid")
    )
    print(f"  {len(loc):,} places in census+elevation.", flush=True)

    # Determine which rows need work for each metric
    needs: dict[str, set] = {}
    for metric, col_name, _, _, col_id in METRICS:
        if col_name not in cache.columns or col_id not in cache.columns:
            needs[metric] = set()
            continue
        mask = (
            cache[col_name].notna()
            & (cache[col_name] != "")
            & (cache[col_id].isna() | (cache[col_id] == ""))
        )
        needs[metric] = set(cache.loc[mask, "geoid"].tolist())

    needs_any = set().union(*needs.values())
    print(f"\nRows needing at least one ID recovered: {len(needs_any):,}", flush=True)
    for metric, *_ in METRICS:
        print(f"  {metric}: {len(needs[metric]):,}", flush=True)

    if not needs_any:
        print("Nothing to do.")
        return

    # Index cache by geoid for fast lookup
    cache_idx = cache.set_index("geoid")

    # Work on a mutable copy (indexed by geoid for easy update)
    updated = cache.set_index("geoid").copy()

    # Per-metric stats
    stats = {m: {"recovered": 0, "no_match": 0, "ambiguous": 0, "no_coords": 0}
             for m, *_ in METRICS}

    # Report buffers (printed at end to avoid spamming during run)
    issues: list[str] = []

    # Cache StnMeta results by rounded (lat, lon) — many metrics for the same
    # town share the same result
    stn_cache: dict[tuple, list[dict]] = {}

    todo_geoids = sorted(needs_any)
    n = len(todo_geoids)

    for i, geoid in enumerate(todo_geoids, 1):
        # Get coordinates
        if geoid not in loc.index:
            for metric, *_ in METRICS:
                if geoid in needs[metric]:
                    stats[metric]["no_coords"] += 1
            continue

        town = loc.loc[geoid]
        lat, lon = float(town["lat"]), float(town["lng"])
        town_elev = town.get("elevation_ft")
        if _is_nan(town_elev):
            town_elev = None

        coord_key = (round(lat, 5), round(lon, 5))
        if coord_key not in stn_cache:
            stations = _stn_meta(lat, lon)
            stn_cache[coord_key] = stations
            time.sleep(0.25)   # StnMeta is cheap; light rate limit
        else:
            stations = stn_cache[coord_key]

        for metric, col_name, col_dist, col_elev_diff, col_id in METRICS:
            if geoid not in needs[metric]:
                continue

            stored_name      = updated.at[geoid, col_name]
            stored_dist      = updated.at[geoid, col_dist]
            stored_elev_diff = updated.at[geoid, col_elev_diff]

            # Guard: dist must be a usable float
            try:
                stored_dist_f = float(stored_dist)
            except (TypeError, ValueError):
                stats[metric]["no_match"] += 1
                issues.append(f"{geoid} {metric}: stored_dist is not numeric ({stored_dist!r})")
                continue

            sid, status = _match_station(
                stations, stored_name, stored_dist_f, stored_elev_diff, town_elev,
            )

            if status == "ok" and sid:
                updated.at[geoid, col_id] = sid
                stats[metric]["recovered"] += 1
            elif status == "no_match":
                stats[metric]["no_match"] += 1
                issues.append(
                    f"{geoid} {metric}: no_match  name='{stored_name}' "
                    f"dist={stored_dist_f:.2f}mi elev_diff={stored_elev_diff}"
                )
            else:
                stats[metric]["ambiguous"] += 1
                issues.append(f"{geoid} {metric}: ambiguous  name='{stored_name}'")

        if i % 1000 == 0 or i == n:
            pct = 100 * i / n
            print(f"  [{i:,}/{n:,} {pct:.0f}%] StnMeta cache size: {len(stn_cache):,}", flush=True)

    # Write back only the rows that were candidates for recovery
    print("\nSaving updated rows...", flush=True)
    rows_to_write = updated.loc[list(needs_any)].reset_index()
    _db.write_cache("wrcc_cache", CACHE_PATH, rows_to_write)
    print(f"  Wrote {len(rows_to_write):,} rows.", flush=True)

    # Summary
    print("\n=== Recovery summary ===")
    for metric, *_ in METRICS:
        s = stats[metric]
        total = len(needs[metric])
        print(
            f"  {metric:4s}: {total:,} needed → "
            f"{s['recovered']:,} recovered, "
            f"{s['no_match']:,} no_match, "
            f"{s['ambiguous']:,} ambiguous, "
            f"{s['no_coords']:,} no_coords"
        )

    if issues:
        print(f"\n{len(issues):,} unresolved issues "
              f"(first 50 shown):")
        for line in issues[:50]:
            print(f"  {line}")
        if len(issues) > 50:
            print(f"  ... and {len(issues) - 50:,} more.")
    else:
        print("\nNo unresolved issues.")


if __name__ == "__main__":
    main()
