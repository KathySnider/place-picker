"""
pipeline/noaa_normals.py
------------------------
Fetches NOAA NCEI 1991-2020 Climate Normals for snowfall.

Used to fill snow data for places PRISM doesn't cover (Alaska, Hawaii) and
as a sanity-check for CONUS places where ERA5's temperature-threshold approach
fails (e.g. maritime/coastal towns that stay above -2°C even while snowing).

Station matching rules (domain-expert recommendation for sparse AK coverage):
    - Nearest station within 30 miles (48 km)
    - Elevation difference ≤ 1,000 ft (305 m)
    - Beyond those limits → treat as unknown (NaN)

Output column:
    noaa_snow_in  — annual snowfall normal (inches), NaN if no valid station

Data sources:
    Station inventory: GHCN-Daily stations file (lat/lon/elevation)
    Climate normals:   NCEI 1991-2020 per-station monthly CSV files
                       https://www.ncei.noaa.gov/data/normals-monthly/1991-2020/access/
    No API key required.
"""

import math
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db as _db

# ── Paths ──────────────────────────────────────────────────────────────────────
CACHE_PATH    = "data/processed/noaa_normals_cache.parquet"
STATIONS_PATH = "data/raw/ghcnd_stations.txt"

NOAA_COLS = [
    "geoid",
    "noaa_snow_in",
    "noaa_station_id",
    "noaa_station_name",
    "noaa_station_dist_mi",
]

# ── Matching thresholds ────────────────────────────────────────────────────────
MAX_DIST_MI  = 30.0    # candidate must be within 30 miles
MAX_ELEV_FT  = 1000.0  # and within 1,000 ft elevation of the station

# ── Fetch settings ─────────────────────────────────────────────────────────────
RATE_LIMIT    = 1.0    # seconds between station-normals requests
FLUSH_EVERY   = 20     # save cache every N places

STATION_LIST_URL = (
    "https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt"
)
NORMALS_URL = (
    "https://www.ncei.noaa.gov/data/normals-monthly/1991-2020/access/{station_id}.csv"
)

HEADERS = {"User-Agent": "place-picker/1.0 (personal location research)"}

REFRESH_DAYS = 730   # re-fetch rows older than 2 years (normals don't change)


# ── Station inventory ──────────────────────────────────────────────────────────

def _load_stations() -> pd.DataFrame:
    """
    Load GHCN-Daily station list with lat, lon, elevation (metres), state, name.
    Downloads once and caches to STATIONS_PATH.
    """
    os.makedirs("data/raw", exist_ok=True)
    if not os.path.exists(STATIONS_PATH):
        print("[noaa_normals] Downloading GHCN station list...", flush=True)
        r = requests.get(STATION_LIST_URL, headers=HEADERS, timeout=60)
        r.raise_for_status()
        with open(STATIONS_PATH, "w", encoding="utf-8") as f:
            f.write(r.text)
        print(f"[noaa_normals] Station list saved ({len(r.text):,} bytes)")

    # Fixed-width format:
    # cols 1-11   station_id
    # cols 13-20  lat
    # cols 22-30  lon
    # cols 32-37  elevation (m)
    # cols 39-40  state
    # cols 42-71  name
    rows = []
    with open(STATIONS_PATH, encoding="utf-8") as f:
        for line in f:
            if len(line) < 40:
                continue
            sid   = line[0:11].strip()
            try:
                lat  = float(line[12:20])
                lon  = float(line[21:30])
                elev = float(line[31:37])   # metres
            except ValueError:
                continue
            state = line[38:40].strip()
            name  = line[41:71].strip() if len(line) > 41 else ""
            rows.append((sid, lat, lon, elev, state, name))

    df = pd.DataFrame(rows, columns=["station_id", "lat", "lon", "elev_m", "state", "name"])
    # Keep only US stations (IDs starting with US or CA for border stations)
    df = df[df["station_id"].str.startswith("US")]
    df["elev_ft"] = df["elev_m"] * 3.28084
    return df.reset_index(drop=True)


# ── Distance helpers ───────────────────────────────────────────────────────────

def _haversine_mi(lat1, lon1, lat2, lon2):
    R = 3958.8   # Earth radius in miles
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def _find_best_station(
    lat: float, lon: float, elev_ft: float, stations: pd.DataFrame
) -> tuple[str | None, str | None, float | None]:
    """
    Return (station_id, station_name, dist_mi) of the nearest station meeting
    the matching rules, or (None, None, None) if none qualifies.
    """
    # Fast pre-filter: bounding box ~35 miles lat, ~50 miles lon at mid-latitudes
    lat_deg = 35 / 69.0
    lon_deg = 35 / (69.0 * math.cos(math.radians(lat)))
    nearby = stations[
        (stations["lat"].between(lat - lat_deg, lat + lat_deg)) &
        (stations["lon"].between(lon - lon_deg * 1.5, lon + lon_deg * 1.5))
    ].copy()

    if nearby.empty:
        return None, None, None

    nearby["dist_mi"] = nearby.apply(
        lambda r: _haversine_mi(lat, lon, r["lat"], r["lon"]), axis=1
    )
    # Apply distance + elevation filter
    if not np.isnan(elev_ft):
        valid = nearby[
            (nearby["dist_mi"] <= MAX_DIST_MI) &
            ((nearby["elev_ft"] - elev_ft).abs() <= MAX_ELEV_FT)
        ]
    else:
        valid = nearby[nearby["dist_mi"] <= MAX_DIST_MI]

    if valid.empty:
        return None, None, None

    best = valid.loc[valid["dist_mi"].idxmin()]
    return best["station_id"], best["name"], round(best["dist_mi"], 2)


# ── Normals fetch ──────────────────────────────────────────────────────────────

_station_normals_cache: dict[str, float | None] = {}   # in-process memo


def _fetch_annual_snow(station_id: str) -> float | None:
    """
    Fetch 1991-2020 monthly snowfall normals for a station and return the
    annual sum in inches (tenths-of-inches values → inches).
    Returns None if the station has no snowfall data.
    """
    if station_id in _station_normals_cache:
        return _station_normals_cache[station_id]

    url = NORMALS_URL.format(station_id=station_id)
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 404:
            _station_normals_cache[station_id] = None
            return None
        r.raise_for_status()
    except requests.RequestException as e:
        print(f" (fetch error for {station_id}: {e})", end="", flush=True)
        return None   # don't cache — may be transient

    try:
        df = pd.read_csv(
            pd.io.common.StringIO(r.text),
            dtype=str,
            low_memory=False,
        )
    except Exception:
        _station_normals_cache[station_id] = None
        return None

    # Column names vary slightly — look for snowfall rows
    # The normals CSV has a "standard" column and a set of monthly columns
    # Try to find MLY-SNOW-NORMAL rows
    snow_row = None
    if "standard" in df.columns:
        mask = df["standard"].str.upper().str.contains("SNOW", na=False) & \
               df["standard"].str.upper().str.contains("MLY", na=False)
        if mask.any():
            snow_row = df[mask].iloc[0]

    if snow_row is None:
        # Try searching all column values for the right variable name
        for col in df.columns:
            vals = df[col].astype(str).str.upper()
            match = vals.str.contains("MLY-SNOW-NORMAL", na=False)
            if match.any():
                snow_row = df[match].iloc[0]
                break

    if snow_row is None:
        _station_normals_cache[station_id] = None
        return None

    # Monthly columns: Jan=1, Feb=2, ..., Dec=12 (or named JAN, FEB, etc.)
    month_cols = []
    for candidate in (
        [str(m) for m in range(1, 13)],
        ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
         "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"],
    ):
        found = [c for c in candidate if c in snow_row.index]
        if len(found) == 12:
            month_cols = found
            break

    if not month_cols:
        # Fall back: find numeric-ish columns (skip first few metadata columns)
        for start in range(2, min(8, len(snow_row.index))):
            trial = list(snow_row.index[start:start + 12])
            # Verify they all parse as numbers or sentinel values
            parseable = all(
                _parse_tenth_in(str(snow_row[c])) is not None or
                str(snow_row[c]).strip() in ("-9999", "-8888", "")
                for c in trial
            )
            if len(trial) == 12 and parseable:
                month_cols = trial
                break

    if not month_cols:
        _station_normals_cache[station_id] = None
        return None

    total_in = 0.0
    has_data = False
    for col in month_cols:
        v = _parse_tenth_in(str(snow_row[col]))
        if v is not None:
            total_in += v
            has_data = True

    result = round(total_in, 1) if has_data else None
    _station_normals_cache[station_id] = result
    return result


def _parse_tenth_in(s: str) -> float | None:
    """
    NCEI normals values are in tenths of inches (or mm, varies).
    Sentinel: -9999 = missing, -8888 = trace (treat as 0).
    Returns value in whole inches, or None for missing.
    """
    s = s.strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if v == -9999:
        return None
    if v == -8888:
        return 0.0   # trace
    # Values are in tenths of inches
    return v / 10.0


# ── Public API ─────────────────────────────────────────────────────────────────

def enrich(candidates: pd.DataFrame, cache_only: bool = False) -> pd.DataFrame:
    """
    Add noaa_snow_in (annual snowfall inches from 1991-2020 climate normals) to
    the candidates DataFrame.

    Only enriches places that don't already have PRISM snow data — PRISM is
    preferred for CONUS since it's a high-resolution gridded product vs. a
    point-observation station match.

    cache_only=True: skip any network fetches; return what's in cache.
    """
    os.makedirs("data/processed", exist_ok=True)

    cache = _db.read_cache("noaa_normals_cache", CACHE_PATH, NOAA_COLS)
    if "fetched_at" not in cache.columns:
        cache["fetched_at"] = pd.NaT
    else:
        cache["fetched_at"] = pd.to_datetime(cache["fetched_at"], errors="coerce")

    today   = pd.Timestamp(pd.Timestamp.now().date())
    cutoff  = today - pd.Timedelta(days=REFRESH_DAYS)

    cached_geoids = set(cache["geoid"].tolist())

    # AK/HI: PRISM has no coverage — always needs NOAA regardless of what
    # the PRISM cache stored (it may have cached 0.0 for out-of-bounds places).
    # CONUS: skip if PRISM snow data is present and non-null.
    NON_CONUS = {"Alaska", "Hawaii"}
    if "state_name" in candidates.columns:
        is_non_conus = candidates["state_name"].isin(NON_CONUS)
    else:
        is_non_conus = pd.Series(False, index=candidates.index)

    has_prism_snow = (
        candidates["prism_snow_in"].notna()
        if "prism_snow_in" in candidates.columns
        else pd.Series(False, index=candidates.index)
    )
    # Need NOAA if: non-CONUS state, OR no real PRISM snow data
    needs_noaa_mask = is_non_conus | ~has_prism_snow
    needs_noaa   = candidates[needs_noaa_mask].copy()
    prism_geoids = set(candidates.loc[~needs_noaa_mask, "geoid"])

    stale_geoids = set(
        cache.loc[cache["fetched_at"] < cutoff, "geoid"].tolist()
    ) if len(cache) else set()

    todo_geoids = (set(needs_noaa["geoid"]) - cached_geoids) | \
                  (stale_geoids & set(needs_noaa["geoid"]))
    todo = needs_noaa[needs_noaa["geoid"].isin(todo_geoids)].copy()

    n_skip_prism = len(prism_geoids)
    if n_skip_prism:
        print(f"[noaa_normals] Skipping {n_skip_prism} CONUS places with PRISM snow data; "
              f"{len(needs_noaa)} places need NOAA normals")

    if todo.empty:
        print("[noaa_normals] All non-PRISM places already cached.")
    elif cache_only:
        print(f"[noaa_normals] cache_only — skipping {len(todo)} uncached places")
    else:
        stations = _load_stations()
        print(f"[noaa_normals] {len(stations):,} US stations loaded. "
              f"Fetching normals for {len(todo)} places...")

        new_rows = []

        def _flush():
            nonlocal cache, new_rows
            if not new_rows:
                return
            ndf = pd.DataFrame(new_rows)
            updated_geoids = set(ndf["geoid"])
            cache = cache[~cache["geoid"].isin(updated_geoids)]
            cache = pd.concat([cache, ndf], ignore_index=True)
            _db.write_cache("noaa_normals_cache", CACHE_PATH, cache)
            new_rows = []

        for i, row in enumerate(todo.itertuples(), 1):
            lat = getattr(row, "lat", None)
            lon = getattr(row, "lng", None)
            if lat is None or lon is None or pd.isna(lat) or pd.isna(lon):
                new_rows.append({
                    "geoid": row.geoid,
                    "noaa_snow_in": None,
                    "noaa_station_id": None,
                    "noaa_station_name": None,
                    "noaa_station_dist_mi": None,
                    "fetched_at": today,
                })
                continue

            elev_ft = getattr(row, "elevation_ft", float("nan"))
            if pd.isna(elev_ft):
                elev_ft = float("nan")

            name_str = getattr(row, "place_name", row.geoid)
            print(f"[noaa_normals] ({i}/{len(todo)}) {name_str}...",
                  end=" ", flush=True)

            station_id, station_name, dist_mi = _find_best_station(
                lat, lon, elev_ft, stations
            )

            snow_in = None
            if station_id:
                snow_in = _fetch_annual_snow(station_id)
                time.sleep(RATE_LIMIT)

            if station_id and snow_in is not None:
                print(f"→ {station_name} ({dist_mi:.1f}mi): {snow_in:.1f}\"")
            elif station_id:
                print(f"→ {station_name} ({dist_mi:.1f}mi): no snow data")
            else:
                print("→ no station within 30mi/1000ft")

            new_rows.append({
                "geoid": row.geoid,
                "noaa_snow_in": snow_in,
                "noaa_station_id": station_id,
                "noaa_station_name": station_name,
                "noaa_station_dist_mi": dist_mi,
                "fetched_at": today,
            })

            if i % FLUSH_EVERY == 0:
                _flush()
                print(f"[noaa_normals] Saved progress ({i}/{len(todo)})")

        _flush()
        print(f"[noaa_normals] Done.")

    keep = ["geoid", "noaa_snow_in", "noaa_station_id", "noaa_station_name",
            "noaa_station_dist_mi"]
    available = [c for c in keep if c in cache.columns]
    return candidates.merge(cache[available], on="geoid", how="left")
