"""
pipeline/coastal.py
-------------------
Computes distance to nearest US coastline for each candidate place.

Data: Census TIGER/Line national coastline shapefile (one-time ~15MB download).
      Cached locally to data/raw/coastln/. All coastline vertices are extracted
      and stored as a numpy array for fast vectorized nearest-neighbor search.

Output columns added to candidates:
    coast_distance_miles  — miles to nearest coastline point
    is_coastal            — True if coast_distance_miles <= COASTAL_THRESHOLD_MI
    beach_coast_ok        — True if within BEACH_MAX_MI (used as a hard filter)
"""

import io
import os
import sys
import zipfile
import struct
import requests
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db as _db

CACHE_PATH  = "data/processed/coastal_cache.parquet"
RAW_DIR     = "data/raw/coastln"
COAST_URL   = "https://www2.census.gov/geo/tiger/TIGER2024/COASTLN/tl_2024_us_coastln.zip"

COASTAL_THRESHOLD_MI = 25   # "coastal town" label
COASTAL_COLS = ["geoid", "coast_distance_miles", "is_coastal"]


# ---------------------------------------------------------------------------
# Shapefile parser — reads .shp PolyLine records (type 3) without extra deps
# ---------------------------------------------------------------------------

def _parse_shp(data: bytes) -> np.ndarray:
    """
    Parse a Shapefile .shp containing PolyLine (type 3) records.
    Returns a flat numpy array of shape (N, 2) with [lon, lat] pairs.
    Lon/lat are stored as doubles in little-endian IEEE 754.
    """
    points = []
    pos = 100  # skip 100-byte file header
    while pos < len(data):
        # Record header: record number (4 big-endian), content length (4 big-endian)
        if pos + 8 > len(data):
            break
        content_len = struct.unpack_from(">i", data, pos + 4)[0] * 2  # in bytes
        pos += 8
        if pos + content_len > len(data):
            break
        shape_type = struct.unpack_from("<i", data, pos)[0]
        if shape_type == 3:  # PolyLine
            # bbox (32 bytes) then num_parts (4) then num_points (4)
            num_parts  = struct.unpack_from("<i", data, pos + 36)[0]
            num_points = struct.unpack_from("<i", data, pos + 40)[0]
            # parts array (num_parts * 4 bytes), then points (num_points * 16 bytes)
            pts_offset = pos + 44 + num_parts * 4
            for i in range(num_points):
                lon, lat = struct.unpack_from("<dd", data, pts_offset + i * 16)
                points.append((lat, lon))
        pos += content_len
    return np.array(points, dtype=np.float64)


def _load_coastline() -> np.ndarray:
    """Download (once) and return all coastline vertices as (lat, lon) pairs."""
    shp_path = os.path.join(RAW_DIR, "coastln.shp")
    if os.path.exists(shp_path):
        with open(shp_path, "rb") as f:
            return _parse_shp(f.read())

    print("[coastal] Downloading Census TIGER coastline (~15 MB)...")
    os.makedirs(RAW_DIR, exist_ok=True)
    resp = requests.get(COAST_URL, timeout=120)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        shp_name = next(n for n in zf.namelist() if n.endswith(".shp"))
        data = zf.read(shp_name)
    with open(shp_path, "wb") as f:
        f.write(data)
    print(f"[coastal] Coastline cached → {shp_path}")
    return _parse_shp(data)


def _min_dist_miles(lat: float, lon: float,
                    coast_lats: np.ndarray, coast_lons: np.ndarray) -> float:
    """Vectorized haversine to nearest coastline vertex."""
    # Narrow candidates to ±3° lat / ±4° lon before computing full haversine
    lat_mask = np.abs(coast_lats - lat) < 3.0
    lon_diff = np.abs(coast_lons - lon)
    lon_diff = np.minimum(lon_diff, 360.0 - lon_diff)
    mask = lat_mask & (lon_diff < 4.0)

    if not mask.any():
        return 9_999.0

    R   = 3_958.8  # Earth radius in miles
    φ1  = np.radians(lat)
    φ2  = np.radians(coast_lats[mask])
    dφ  = φ2 - φ1
    dλ  = np.radians(coast_lons[mask] - lon)
    a   = np.sin(dφ / 2) ** 2 + np.cos(φ1) * np.cos(φ2) * np.sin(dλ / 2) ** 2
    return float((R * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))).min())


def enrich(candidates: pd.DataFrame) -> pd.DataFrame:
    """Add coast_distance_miles and is_coastal columns to candidates."""
    cache = _db.read_cache("coastal_cache", CACHE_PATH, COASTAL_COLS)

    cached_geoids = set(cache["geoid"].tolist())
    needed = candidates[~candidates["geoid"].isin(cached_geoids)].copy()

    if needed.empty:
        print("[coastal] All candidates already in coastal cache.")
    else:
        print(f"[coastal] Computing coast distance for {len(needed):,} candidates...")
        pts = _load_coastline()
        coast_lats = pts[:, 0]
        coast_lons = pts[:, 1]

        rows = []
        for row in needed.itertuples():
            if pd.isna(row.lat) or pd.isna(row.lng):
                rows.append({"geoid": row.geoid,
                             "coast_distance_miles": None,
                             "is_coastal": False})
                continue
            dist = _min_dist_miles(row.lat, row.lng, coast_lats, coast_lons)
            rows.append({
                "geoid":               row.geoid,
                "coast_distance_miles": round(dist, 1),
                "is_coastal":           dist <= COASTAL_THRESHOLD_MI,
            })

        new_df = pd.DataFrame(rows)
        cache  = pd.concat([cache, new_df], ignore_index=True)
        _db.write_cache("coastal_cache", CACHE_PATH, cache)
        print(f"[coastal] Coast distance computed for {len(new_df):,} places")

    return candidates.merge(cache[COASTAL_COLS], on="geoid", how="left")
