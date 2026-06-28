"""
pipeline/osm.py
---------------
Queries the OpenStreetMap Overpass API to count walkable amenities
near each candidate place.

Anchor point: the OSM place=* node (city/town/village center) nearest to
the Census centroid. This gives a more accurate downtown anchor than the
Census geographic centroid, which can be pulled toward water or park edges.

Two radii:
    800m  — ~half-mile walk
    1600m — ~one-mile walk

Two categories:
    practical — groceries, pharmacy, medical, bank, ATM, post office, library
    lifestyle — restaurants, cafes, bars, shops, parks, galleries, transit, etc.

One API call per place fetches place nodes + all amenity nodes within 2200m.
Python finds the nearest place node and recounts from there at 800/1600m.

No API key required. Rate limit: 3s between requests.
Only successful results are cached — errors are retried on the next run.
Cache is flushed to disk every 10 successful queries.
"""

import math
import time
import requests
import pandas as pd
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db as _db

CACHE_PATH   = "data/processed/osm_cache.parquet"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
RATE_LIMIT   = 10.0
RETRY_WAIT   = 30
FLUSH_EVERY  = 10
REFRESH_DAYS = 180   # re-fetch rows older than this

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Fetch place nodes + amenity nodes within 2200m of Census centroid.
# The extra buffer ensures we don't miss amenities near the OSM town center
# even if it's offset from the Census centroid.
AMENITY_QUERY = """
[out:json][timeout:45];
(
  node(around:2200,{lat},{lon})[place~"city|town|village|borough|hamlet"];
  node(around:2200,{lat},{lon})[shop];
  node(around:2200,{lat},{lon})[amenity~"restaurant|cafe|bar|fast_food|pub"];
  node(around:2200,{lat},{lon})[amenity~"pharmacy|doctors|dentist|hospital|clinic"];
  node(around:2200,{lat},{lon})[amenity~"supermarket|grocery|convenience"];
  node(around:2200,{lat},{lon})[shop~"supermarket|convenience|greengrocer|butcher|bakery|food"];
  node(around:2200,{lat},{lon})[amenity~"bank|atm|post_office"];
  node(around:2200,{lat},{lon})[amenity~"library"];
  node(around:2200,{lat},{lon})[amenity~"community_centre|social_centre"];
  node(around:2200,{lat},{lon})[amenity~"theatre|cinema|arts_centre|museum"];
  node(around:2200,{lat},{lon})[tourism~"gallery|museum|artwork"];
  node(around:2200,{lat},{lon})[public_transport~"stop_position|platform"];
  node(around:2200,{lat},{lon})[highway~"bus_stop"];
  node(around:2200,{lat},{lon})[leisure~"park|garden|playground"];
);
out body qt;
"""

HEADERS = {
    "User-Agent": "place-picker/1.0 (personal location search tool)",
    "Accept":     "application/json",
}

# ---------------------------------------------------------------------------
# Category classification
# ---------------------------------------------------------------------------

def _classify(tags: dict) -> str | None:
    """
    Return 'practical', 'lifestyle', 'place', or None.
    place = OSM town/city/village center node used as anchor.
    """
    place   = tags.get("place", "")
    amenity = tags.get("amenity", "")
    shop    = tags.get("shop", "")
    leisure = tags.get("leisure", "")
    tourism = tags.get("tourism", "")
    pt      = tags.get("public_transport", "")
    highway = tags.get("highway", "")

    # Place center node
    if place in ("city", "town", "village", "borough", "hamlet"):
        return "place"

    # Practical
    if amenity in ("pharmacy",):
        return "practical"
    if amenity in ("doctors", "dentist", "hospital", "clinic"):
        return "practical"
    if amenity in ("bank", "atm", "post_office"):
        return "practical"
    if amenity in ("library",):
        return "practical"
    if amenity in ("supermarket", "grocery", "convenience"):
        return "practical"
    if shop in ("supermarket", "convenience", "greengrocer",
                "butcher", "bakery", "food", "deli", "seafood",
                "farm", "health_food", "pasta", "spices"):
        return "practical"

    # Lifestyle
    if amenity in ("restaurant", "cafe", "bar", "fast_food", "pub",
                   "ice_cream", "food_court", "biergarten"):
        return "lifestyle"
    if amenity in ("theatre", "cinema", "arts_centre", "museum",
                   "community_centre", "social_centre", "nightclub",
                   "events_venue"):
        return "lifestyle"
    if shop:
        return "lifestyle"
    if leisure in ("park", "garden", "playground", "fitness_centre",
                   "sports_centre", "swimming_pool", "golf_course",
                   "pitch", "track", "nature_reserve"):
        return "lifestyle"
    if tourism in ("gallery", "museum", "artwork", "attraction",
                   "viewpoint", "zoo", "aquarium"):
        return "lifestyle"
    if pt in ("stop_position", "platform") or highway == "bus_stop":
        return "lifestyle"

    return None


# ---------------------------------------------------------------------------
# Haversine distance
# ---------------------------------------------------------------------------

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
# Overpass query with server rotation
# ---------------------------------------------------------------------------

def _query_overpass(lat: float, lon: float) -> dict:
    """
    Fetch amenity nodes within 2200m of (lat, lon).
    Finds the nearest OSM place node as the anchor, then counts
    practical and lifestyle amenities within 800m and 1600m of that anchor.
    Returns dict with practical_800m, practical_1600m, lifestyle_800m,
    lifestyle_1600m, and anchor_lat, anchor_lng.
    """
    query   = AMENITY_QUERY.format(lat=lat, lon=lon)
    servers = list(OVERPASS_SERVERS)

    while servers:
        url = servers[0]
        try:
            resp = requests.post(
                url,
                data={"data": query},
                headers=HEADERS,
                timeout=30,
            )
        except requests.exceptions.Timeout:
            servers.pop(0)
            if servers:
                print(f"    [osm] Timeout on {url.split('/')[2]} — trying next server...")
                time.sleep(2)
                continue
            else:
                print(f"    [osm] All servers timed out — waiting {RETRY_WAIT}s...")
                time.sleep(RETRY_WAIT)
                servers = list(OVERPASS_SERVERS)
                continue

        if resp.status_code in (429, 504):
            label = "rate-limited" if resp.status_code == 429 else "gateway timeout"
            servers.pop(0)
            if servers:
                print(f"    [osm] {resp.status_code} {label} on {url.split('/')[2]} — trying next server...")
                time.sleep(2)
                continue
            else:
                print(f"    [osm] All servers {label} — waiting {RETRY_WAIT}s...")
                time.sleep(RETRY_WAIT)
                servers = list(OVERPASS_SERVERS)
                continue
        else:
            resp.raise_for_status()
            break

    elements = resp.json().get("elements", [])

    # Separate place nodes from amenity nodes
    place_nodes   = []
    amenity_nodes = []
    seen = set()

    for el in elements:
        nid = el.get("id")
        if nid in seen:
            continue
        seen.add(nid)
        nlat = el.get("lat")
        nlon = el.get("lon")
        if nlat is None or nlon is None:
            continue
        tags     = el.get("tags", {})
        category = _classify(tags)
        if category == "place":
            place_nodes.append((nlat, nlon))
        elif category in ("practical", "lifestyle"):
            amenity_nodes.append((nlat, nlon, category))

    # Find the nearest place node to the Census centroid — use as anchor
    if place_nodes:
        anchor_lat, anchor_lng = min(
            place_nodes,
            key=lambda p: _haversine_m(lat, lon, p[0], p[1])
        )
    else:
        # Fall back to Census centroid if no place node found
        anchor_lat, anchor_lng = lat, lon

    # Count amenities at both radii from anchor
    counts = {
        "practical_800m": 0, "practical_1600m": 0,
        "lifestyle_800m": 0, "lifestyle_1600m": 0,
    }
    for nlat, nlon, category in amenity_nodes:
        dist = _haversine_m(anchor_lat, anchor_lng, nlat, nlon)
        if dist <= 1600:
            counts[f"{category}_1600m"] += 1
        if dist <= 800:
            counts[f"{category}_800m"] += 1

    counts["anchor_lat"] = round(anchor_lat, 6)
    counts["anchor_lng"] = round(anchor_lng, 6)
    return counts


# ---------------------------------------------------------------------------
# Public enrich() function
# ---------------------------------------------------------------------------

def enrich(candidates: pd.DataFrame, stop_event=None) -> pd.DataFrame:
    """
    Add practical_800m, practical_1600m, lifestyle_800m, lifestyle_1600m,
    anchor_lat, anchor_lng columns to the candidates DataFrame.

    Skips geoids already in the local cache.
    Only successful results are cached — errors are retried on the next run.
    Cache is flushed to disk every FLUSH_EVERY successful queries.
    """
    cache_cols = ["geoid", "practical_800m", "practical_1600m",
                  "lifestyle_800m", "lifestyle_1600m",
                  "anchor_lat", "anchor_lng"]

    print(f"[osm] Reading cache for {len(candidates):,} candidates...")
    cache = _db.read_cache("osm_cache", CACHE_PATH, cache_cols + ["fetched_at"])
    print(f"[osm] Cache has {len(cache):,} rows")
    for col in cache_cols[1:]:
        if col not in cache.columns:
            cache[col] = None
    if "fetched_at" not in cache.columns:
        cache["fetched_at"] = pd.NaT

    today   = pd.Timestamp(date.today())
    cutoff  = pd.Timestamp(date.today() - timedelta(days=REFRESH_DAYS))

    cache["fetched_at"] = pd.to_datetime(cache["fetched_at"])
    stale_mask   = cache["fetched_at"].isna() | (cache["fetched_at"] < cutoff)
    stale_geoids = set(cache.loc[stale_mask, "geoid"].tolist())
    fresh_geoids = set(cache["geoid"].tolist()) - stale_geoids

    candidate_geoids = set(candidates["geoid"].tolist())
    new_geoids  = candidate_geoids - set(cache["geoid"].tolist())
    todo_geoids = new_geoids | (stale_geoids & candidate_geoids)
    todo = candidates[candidates["geoid"].isin(todo_geoids)].copy()

    print(f"[osm] {len(fresh_geoids & candidate_geoids):,} fresh in cache, "
          f"{len(new_geoids & candidate_geoids):,} new, "
          f"{len(stale_geoids & candidate_geoids):,} stale")

    if todo.empty:
        print("[osm] All candidates already cached — skipping Overpass.")
    else:
        n_new   = len(new_geoids  & candidate_geoids)
        n_stale = len(stale_geoids & candidate_geoids)
        print(f"[osm] Querying Overpass for {len(todo)} places "
              f"({n_new} new, {n_stale} stale) "
              f"~{len(todo) * RATE_LIMIT / 60:.1f} min...")
        new_rows = []

        def _flush(label: str):
            nonlocal cache, new_rows
            if new_rows:
                new_df     = pd.DataFrame(new_rows)
                refreshed  = set(new_df["geoid"].tolist())
                cache      = cache[~cache["geoid"].isin(refreshed)]
                cache      = pd.concat([cache, new_df], ignore_index=True)
                _db.write_cache("osm_cache", CACHE_PATH, cache)
                new_rows = []
                print(f"[osm] {label} — cache updated")

        try:
            for i, row in enumerate(todo.itertuples(), 1):
                if stop_event and stop_event.is_set():
                    print("[osm] Stop signal received — saving progress...")
                    break
                if pd.isna(row.lat) or pd.isna(row.lng):
                    new_rows.append({"geoid": row.geoid,
                                     **{c: None for c in cache_cols[1:]},
                                     "fetched_at": today})
                    continue

                try:
                    counts = _query_overpass(row.lat, row.lng)
                    new_rows.append({"geoid": row.geoid, **counts,
                                     "fetched_at": today})
                    anchored = (counts["anchor_lat"] != row.lat or
                                counts["anchor_lng"] != row.lng)
                    anchor_note = " (OSM anchor)" if anchored else " (census centroid)"
                    print(f"  [osm][{i}/{len(todo)}] {row.place_name}, {row.state_name} "
                          f"— pract: {counts['practical_800m']}/{counts['practical_1600m']}  "
                          f"life: {counts['lifestyle_800m']}/{counts['lifestyle_1600m']}"
                          f"{anchor_note}")
                except Exception as e:
                    print(f"  [osm][{i}/{len(todo)}] {row.place_name} — ERROR: {e} (will retry)")

                if len(new_rows) % FLUSH_EVERY == 0 and new_rows:
                    _flush("Cache flushed")

                # wait() wakes immediately if stop_event is set
                if stop_event:
                    stop_event.wait(RATE_LIMIT)
                else:
                    time.sleep(RATE_LIMIT)

        except KeyboardInterrupt:
            print("\n[osm] Interrupted — saving progress...")
            _flush("Cache saved")
            return candidates.merge(cache[cache_cols], on="geoid", how="left")

        _flush("Cache updated")

    result = candidates.merge(
        cache[[c for c in cache_cols if c in cache.columns]],
        on="geoid",
        how="left",
    )
    return result
