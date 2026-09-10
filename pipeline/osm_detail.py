"""
pipeline/osm_detail.py
----------------------
Per-amenity presence check for top-ranked places.

Rather than aggregate counts (osm.py), this module records whether each
specific amenity TYPE is present within 1600m of the town anchor — giving
a checklist display for the final results.

Uses the anchor lat/lng from the OSM cache so the center point is consistent
with the walkability counts.

Cache: data/processed/osm_detail_cache.parquet
    Keyed by geoid. Rows older than CACHE_DAYS are refreshed.
    Only successful results are cached.

Output columns (all bool: True = at least one found within 1600m):
    Practical:  has_grocery, has_pharmacy, has_medical, has_bank,
                has_atm, has_post_office, has_library
    Lifestyle:  has_restaurant, has_cafe, has_bar, has_shopping,
                has_park, has_arts, has_transit
"""

import math
import time
import requests
import pandas as pd
import numpy as np
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db as _db

CACHE_PATH   = "data/processed/osm_detail_cache.parquet"
RATE_LIMIT        = 2.0
RETRY_WAIT        = 10
MAX_RETRIES       = 2    # fail faster in web context; worker uses more patience
CACHE_DAYS        = 180  # refresh successful rows after 6 months
FAILED_RETRY_DAYS = 3    # worker retries failed places after 3 days

OVERPASS_SERVERS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

RADIUS = 1600  # meters — ~1 mile

DETAIL_COLS = [
    "geoid", "detail_fetched_date", "detail_failed_date",
    # Practical
    "has_grocery", "has_pharmacy", "has_medical", "has_bank",
    "has_atm", "has_post_office", "has_library",
    # Lifestyle
    "has_restaurant", "has_cafe", "has_bar", "has_shopping",
    "has_park", "has_arts", "has_transit",
    # Coastal
    "has_beach",
]

HEADERS = {
    "User-Agent": "place-picker/1.0 (personal location search tool)",
    "Accept":     "application/json",
}

BEACH_RADIUS = 8_047  # ~5 miles for beach detection

QUERY = """
[out:json][timeout:60];
(
  node(around:{radius},{lat},{lon})[amenity~"supermarket|grocery|convenience"];
  node(around:{radius},{lat},{lon})[shop~"supermarket|convenience|greengrocer|butcher|bakery|food|deli|health_food"];
  node(around:{radius},{lat},{lon})[amenity="pharmacy"];
  node(around:{radius},{lat},{lon})[amenity~"doctors|dentist|hospital|clinic"];
  node(around:{radius},{lat},{lon})[amenity="bank"];
  node(around:{radius},{lat},{lon})[amenity="atm"];
  node(around:{radius},{lat},{lon})[amenity="post_office"];
  node(around:{radius},{lat},{lon})[amenity="library"];
  node(around:{radius},{lat},{lon})[amenity~"restaurant|cafe|bar|fast_food|pub|biergarten"];
  node(around:{radius},{lat},{lon})[shop];
  node(around:{radius},{lat},{lon})[leisure~"park|garden|nature_reserve"];
  node(around:{radius},{lat},{lon})[amenity~"theatre|cinema|arts_centre|museum"];
  node(around:{radius},{lat},{lon})[tourism~"gallery|museum"];
  node(around:{radius},{lat},{lon})[public_transport~"stop_position|platform"];
  node(around:{radius},{lat},{lon})[highway="bus_stop"];
  node(around:{beach_r},{lat},{lon})[natural="beach"];
  way(around:{beach_r},{lat},{lon})[natural="beach"];
);
out tags qt;
"""


def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def _classify_node(tags: dict) -> set[str]:
    """Return set of amenity type keys present in this node."""
    found = set()
    amenity = tags.get("amenity", "")
    shop    = tags.get("shop", "")
    leisure = tags.get("leisure", "")
    tourism = tags.get("tourism", "")
    pt      = tags.get("public_transport", "")
    highway = tags.get("highway", "")

    if amenity in ("supermarket", "grocery", "convenience") or \
       shop in ("supermarket", "convenience", "greengrocer", "butcher",
                "bakery", "food", "deli", "health_food", "seafood", "farm"):
        found.add("grocery")

    if amenity == "pharmacy":
        found.add("pharmacy")

    if amenity in ("doctors", "dentist", "hospital", "clinic"):
        found.add("medical")

    if amenity == "bank":
        found.add("bank")

    if amenity == "atm":
        found.add("atm")

    if amenity == "post_office":
        found.add("post_office")

    if amenity == "library":
        found.add("library")

    if amenity in ("restaurant", "fast_food", "pub", "biergarten"):
        found.add("restaurant")

    if amenity == "cafe":
        found.add("cafe")

    if amenity == "bar":
        found.add("bar")

    if shop and amenity not in ("supermarket", "grocery", "convenience") and \
       shop not in ("supermarket", "convenience", "greengrocer", "butcher",
                    "bakery", "food", "deli", "health_food"):
        found.add("shopping")

    if leisure in ("park", "garden", "nature_reserve"):
        found.add("park")

    if amenity in ("theatre", "cinema", "arts_centre", "museum") or \
       tourism in ("gallery", "museum"):
        found.add("arts")

    if pt in ("stop_position", "platform") or highway == "bus_stop":
        found.add("transit")

    if tags.get("natural") == "beach":
        found.add("beach")

    return found


def _fetch_one(lat: float, lon: float) -> dict | None:
    """Query Overpass for one place. Returns amenity presence dict or None on error."""
    query = QUERY.format(radius=RADIUS, beach_r=BEACH_RADIUS, lat=lat, lon=lon)
    elements = None
    for server in OVERPASS_SERVERS:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = requests.post(server, data={"data": query},
                                     headers=HEADERS, timeout=60)
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

    found = set()
    for el in elements:
        tags = el.get("tags", {})
        el_lat = el.get("lat", lat)
        el_lon = el.get("lon", lon)
        if _haversine_m(lat, lon, el_lat, el_lon) <= RADIUS:
            found |= _classify_node(tags)

    return {
        "has_grocery":     "grocery"    in found,
        "has_pharmacy":    "pharmacy"   in found,
        "has_medical":     "medical"    in found,
        "has_bank":        "bank"       in found,
        "has_atm":         "atm"        in found,
        "has_post_office": "post_office" in found,
        "has_library":     "library"    in found,
        "has_restaurant":  "restaurant" in found,
        "has_cafe":        "cafe"       in found,
        "has_bar":         "bar"        in found,
        "has_shopping":    "shopping"   in found,
        "has_park":        "park"       in found,
        "has_arts":        "arts"       in found,
        "has_transit":     "transit"    in found,
        "has_beach":       "beach"      in found,
    }


def enrich(top_results: pd.DataFrame, cache_only: bool = False) -> pd.DataFrame:
    """
    Add per-amenity presence columns to the top results DataFrame.
    Uses anchor_lat/anchor_lng from OSM cache if available, else lat/lng.
    Caches results; refreshes successful rows older than CACHE_DAYS.

    Failed fetches are marked with detail_failed_date and skipped on the web
    path (cache_only=True). The worker retries them after FAILED_RETRY_DAYS.
    """
    cache = _db.read_cache("osm_detail_cache", CACHE_PATH, DETAIL_COLS)
    if "detail_fetched_date" not in cache.columns:
        cache["detail_fetched_date"] = pd.NaT
    if "detail_failed_date" not in cache.columns:
        cache["detail_failed_date"] = pd.NaT

    today      = pd.Timestamp(date.today())
    stale_age  = pd.Timedelta(days=CACHE_DAYS)
    retry_age  = pd.Timedelta(days=FAILED_RETRY_DAYS)

    cached = cache.copy()
    cached["detail_fetched_date"] = pd.to_datetime(cached["detail_fetched_date"])
    cached["detail_failed_date"]  = pd.to_datetime(cached["detail_failed_date"])

    bool_cols_present = [c for c in DETAIL_COLS if c.startswith("has_") and c in cached.columns]

    # A row is a real success only if fetched_date is recent AND at least one
    # bool col is non-null (old failure rows have fetched_date set but all nulls).
    has_any_data = (
        cached[bool_cols_present].notna().any(axis=1)
        if bool_cols_present else pd.Series(False, index=cached.index)
    )
    success_fresh = ((today - cached["detail_fetched_date"]) < stale_age) & has_any_data

    # Failed rows: failed_date is set (new-style) OR fetched_date set but all nulls (old-style).
    # Web path skips all failures. Worker retries after FAILED_RETRY_DAYS.
    new_style_failed = cached["detail_failed_date"].notna()
    old_style_failed = cached["detail_fetched_date"].notna() & ~has_any_data
    failed_mask = new_style_failed | old_style_failed
    if cache_only:
        failed_fresh = failed_mask  # web: skip all failures
    else:
        last_attempt = cached["detail_failed_date"].fillna(cached["detail_fetched_date"])
        failed_fresh = failed_mask & ((today - last_attempt) < retry_age)

    fresh_geoids = set(
        cached.loc[success_fresh | failed_fresh, "geoid"].tolist()
    )
    needed = [
        row for row in top_results.itertuples()
        if row.geoid not in fresh_geoids
    ]

    if not needed:
        print("[osm_detail] All top results already in detail cache.")
    else:
        print(f"[osm_detail] Fetching amenity detail for {len(needed)} places...")
        new_rows = []
        bool_cols = [c for c in DETAIL_COLS if c.startswith("has_")]

        def _flush(current_cache, rows):
            if not rows:
                return
            new_df = pd.DataFrame(rows)
            refreshed = set(new_df["geoid"].tolist())
            up = current_cache[~current_cache["geoid"].isin(refreshed)]
            merged = pd.concat([up, new_df], ignore_index=True)
            _db.write_cache("osm_detail_cache", CACHE_PATH, merged)
            return merged

        for i, row in enumerate(needed, 1):
            lat = getattr(row, "anchor_lat", None) or row.lat
            lon = getattr(row, "anchor_lng", None) or row.lng
            print(f"[osm_detail]   ({i}/{len(needed)}) {row.place_name}...",
                  end=" ", flush=True)
            result = _fetch_one(lat, lon)
            if result is None:
                print("error — marking failed, worker will retry in 3 days")
                new_rows.append({"geoid": row.geoid,
                                 "detail_fetched_date": pd.NaT,
                                 "detail_failed_date": today,
                                 **{col: None for col in bool_cols}})
            else:
                new_rows.append({"geoid": row.geoid,
                                 "detail_fetched_date": today,
                                 "detail_failed_date": pd.NaT,
                                 **result})
                print("done")

            # Save every 5 places so progress isn't lost on connection drop
            if i % 5 == 0:
                cache = _flush(cache, new_rows) or cache
                print(f"[osm_detail] Saved progress ({i}/{len(needed)})")

            if i < len(needed):
                time.sleep(RATE_LIMIT)

        cache = _flush(cache, new_rows) or cache
        if new_rows:
            print(f"[osm_detail] Cache updated: {len(new_rows)} places")

    keep_cols = ["geoid"] + [c for c in DETAIL_COLS if c.startswith("has_")]
    available = [c for c in keep_cols if c in cache.columns]
    return top_results.merge(cache[available], on="geoid", how="left")
