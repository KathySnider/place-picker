"""
pipeline/osm_trails.py
----------------------
Measures walkable trail and path infrastructure near each top-ranked place.

Two metrics:
    trail_miles_10mi  — total miles of hiking/walking trail ways within 10 miles
                        (highway=path, track, bridleway — excludes sidewalks)
    footway_miles_1mi — total miles of pedestrian ways within 1600m (~1 mile)
                        (highway=footway, pedestrian, path — excludes sidewalks)

Length is computed by summing haversine distances between consecutive nodes
in each OSM way geometry (requires out geom in the Overpass query).

Ways are bucketed by centroid distance from the OSM anchor point.
Sidewalks (footway=sidewalk or highway=footway + footway=sidewalk) are excluded
from both metrics — they measure recreational/pedestrian infrastructure, not road
infrastructure.

Cache: data/processed/osm_trails_cache.parquet
    Keyed by geoid. Rows older than CACHE_DAYS are refreshed.
    Only successful results are cached.
"""

import math
import time
import requests
import pandas as pd
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db as _db

CACHE_PATH   = "data/processed/osm_trails_cache.parquet"
CACHE_DAYS   = 180
RATE_LIMIT   = 3.0
RETRY_WAIT   = 15
MAX_RETRIES  = 3   # attempts per server before rotating

TRAIL_RADIUS_M = 16_093   # 10 miles
FOOT_RADIUS_M  = 1_600    # ~1 mile

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

TRAIL_COLS = [
    "geoid", "trails_fetched_date",
    "trail_miles_10mi", "footway_miles_1mi",
]

HEADERS = {
    "User-Agent": "place-picker/1.0 (personal location search tool)",
    "Accept":     "application/json",
}

# Fetch all trail and footway ways within the larger radius in one call.
# out geom returns inline node coordinates so we can compute lengths.
QUERY = """
[out:json][timeout:90];
(
  way(around:{trail_r},{lat},{lon})[highway~"^(path|track|bridleway)$"];
  way(around:{foot_r},{lat},{lon})[highway~"^(footway|pedestrian)$"];
);
out geom qt;
"""


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _way_length_m(geometry: list) -> float:
    """Sum haversine distances between consecutive nodes."""
    total = 0.0
    for i in range(len(geometry) - 1):
        total += _haversine_m(
            geometry[i]["lat"],   geometry[i]["lon"],
            geometry[i+1]["lat"], geometry[i+1]["lon"],
        )
    return total


def _centroid_dist_m(geometry: list, anchor_lat: float, anchor_lon: float) -> float:
    """Distance from anchor to the centroid of a way's nodes."""
    lats = [p["lat"] for p in geometry]
    lons = [p["lon"] for p in geometry]
    return _haversine_m(anchor_lat, anchor_lon,
                        sum(lats) / len(lats), sum(lons) / len(lons))


def _is_sidewalk(tags: dict) -> bool:
    return tags.get("footway") == "sidewalk" or tags.get("sidewalk") == "yes"


def _fetch_one(lat: float, lon: float) -> dict | None:
    """Query Overpass for one place. Returns trail metric dict or None on error."""
    query = QUERY.format(trail_r=TRAIL_RADIUS_M, foot_r=FOOT_RADIUS_M,
                         lat=lat, lon=lon)
    elements = None
    for server in OVERPASS_SERVERS:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.post(server, data={"data": query},
                                     headers=HEADERS, timeout=120)
                if resp.status_code == 429:
                    wait = RETRY_WAIT * attempt
                    print(f" (rate-limited, waiting {wait}s)", end="", flush=True)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                elements = resp.json().get("elements", [])
                break
            except Exception as e:
                if attempt < MAX_RETRIES:
                    wait = RETRY_WAIT * attempt
                    print(f" (attempt {attempt} failed: {type(e).__name__}, retrying in {wait}s)",
                          end="", flush=True)
                    time.sleep(wait)
                else:
                    print(f" (server {server.split('/')[2]} failed after {MAX_RETRIES} attempts)",
                          end="", flush=True)
        if elements is not None:
            break

    if elements is None:
        return None

    trail_m   = 0.0
    footway_m = 0.0

    for el in elements:
        if el.get("type") != "way":
            continue
        tags     = el.get("tags", {})
        geometry = el.get("geometry", [])
        if len(geometry) < 2:
            continue
        if _is_sidewalk(tags):
            continue

        highway = tags.get("highway", "")
        dist_m  = _centroid_dist_m(geometry, lat, lon)
        length_m = _way_length_m(geometry)

        if highway in ("path", "track", "bridleway") and dist_m <= TRAIL_RADIUS_M:
            trail_m += length_m

        if highway in ("footway", "pedestrian", "path") and dist_m <= FOOT_RADIUS_M:
            footway_m += length_m

    m_to_mi = 1 / 1609.344
    return {
        "trail_miles_10mi":  round(trail_m  * m_to_mi, 1),
        "footway_miles_1mi": round(footway_m * m_to_mi, 1),
    }


def enrich(top_results: pd.DataFrame) -> pd.DataFrame:
    """
    Add trail and footway mileage columns to the top results DataFrame.
    Uses anchor_lat/anchor_lng from OSM cache if available, else lat/lng.
    Caches results; refreshes rows older than CACHE_DAYS.
    """
    cache = _db.read_cache("osm_trails_cache", CACHE_PATH, TRAIL_COLS)
    if "trails_fetched_date" not in cache.columns:
        cache["trails_fetched_date"] = pd.NaT

    today     = pd.Timestamp(date.today())
    stale_age = pd.Timedelta(days=CACHE_DAYS)

    cached = cache.copy()
    cached["trails_fetched_date"] = pd.to_datetime(cached["trails_fetched_date"])

    fresh_geoids = set(
        cached.loc[
            (today - cached["trails_fetched_date"]) < stale_age,
            "geoid"
        ].tolist()
    )
    needed = [
        row for row in top_results.itertuples()
        if row.geoid not in fresh_geoids
    ]

    if not needed:
        print("[osm_trails] All top results already in trails cache.")
    else:
        print(f"[osm_trails] Fetching trail data for {len(needed)} places "
              f"(10-mile radius — may be slow)...")
        new_rows = []
        for i, row in enumerate(needed, 1):
            lat = getattr(row, "anchor_lat", None) or row.lat
            lon = getattr(row, "anchor_lng", None) or row.lng
            print(f"[osm_trails]   ({i}/{len(needed)}) {row.place_name}...",
                  end=" ", flush=True)
            result = _fetch_one(lat, lon)
            if result is None:
                print("error — skipping")
                continue
            new_rows.append({"geoid": row.geoid,
                             "trails_fetched_date": today,
                             **result})
            print(f"trails: {result['trail_miles_10mi']} mi | "
                  f"footways: {result['footway_miles_1mi']} mi")
            if i < len(needed):
                time.sleep(RATE_LIMIT)

        if new_rows:
            new_df = pd.DataFrame(new_rows)
            refreshed = set(new_df["geoid"].tolist())
            cache = cache[~cache["geoid"].isin(refreshed)]
            cache = pd.concat([cache, new_df], ignore_index=True)
            _db.write_cache("osm_trails_cache", CACHE_PATH, cache)
            print(f"[osm_trails] Cache updated: {len(new_df)} places")

    keep_cols = ["geoid", "trail_miles_10mi", "footway_miles_1mi"]
    available = [c for c in keep_cols if c in cache.columns]
    return top_results.merge(cache[available], on="geoid", how="left")
