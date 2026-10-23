"""
pipeline/wrcc.py
----------------
WRCC / ACIS fallback for places that are still missing snow or temperature
data after the NOAA 1991-2020 normals step.

Uses the Applied Climate Information System (ACIS) REST API at
data.rcc-acis.org, which aggregates COOP, GHCN-Daily, and other station
networks — the same underlying data exposed on wrcc-archive.dri.edu but via a
documented JSON API (no HTML scraping required).

Chain position: NOAA 1991-2020 normals → **WRCC/ACIS (this module)** → ERA5

Station selection rules (mirrors noaa_normals.py):
    - Nearest station within 30 miles
    - Elevation difference ≤ 1,000 ft (skipped when elevation is unknown)
    - Fills each metric independently; different stations can supply
      different metrics if the closest station has only partial data.

Metrics (each needs ≥ MIN_YEARS (10) valid years, checked independently):
    wrcc_winter_snow_in  Average winter snowfall (Nov–Apr) — NOT annual
                         snowfall; kept distinct from NOAA annual snow.
                         A winter is valid only when all six Nov–Apr months
                         are valid.
    wrcc_july_tmax_f     Mean July maximum temperature; one value per valid July.
    wrcc_winter_tavg_f   Mean Dec–Feb average temperature; a winter is valid
                         only when Dec, Jan and Feb are all valid.
    A month is valid only if ACIS reports it with ≤ MAX_MISSING_DAYS missing
    days; months with more come back as "M".

Data period (two passes over the candidate stations, nearest first):
    Pass 1 uses only 1991-2020 data; each metric takes the nearest station
    with ≥ MIN_YEARS valid years in that window.  Pass 2 uses each station's
    full period of record, only for metrics still empty after pass 1 — it
    never replaces a pass-1 value, even from a closer station.  Stations
    ACIS reports as having no data are remembered for the run and skipped.
    Each metric's stored period spans the years actually used in its
    calculation, not the station's operating period.

Output cache table: wrcc_cache
Columns (20 total):
    geoid
    wrcc_winter_snow_in, wrcc_winter_snow_station_id, wrcc_winter_snow_station_name,
      wrcc_winter_snow_station_dist_mi, wrcc_winter_snow_station_elev_diff_ft,
      wrcc_winter_snow_period
    wrcc_july_tmax_f, wrcc_july_tmax_station_id, wrcc_july_tmax_station_name,
      wrcc_july_tmax_station_dist_mi, wrcc_july_tmax_station_elev_diff_ft, wrcc_july_tmax_period
    wrcc_winter_tavg_f, wrcc_winter_tavg_station_id, wrcc_winter_tavg_station_name,
      wrcc_winter_tavg_station_dist_mi, wrcc_winter_tavg_station_elev_diff_ft, wrcc_winter_tavg_period
    fetched_at
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

# ── Cache ───────────────────────────────────────────────────────────────────────
CACHE_PATH = "data/processed/wrcc_cache.parquet"

WRCC_COLS = [
    "geoid",
    "wrcc_winter_snow_in",
    "wrcc_winter_snow_station_id", "wrcc_winter_snow_station_name",
    "wrcc_winter_snow_station_dist_mi", "wrcc_winter_snow_station_elev_diff_ft",
    "wrcc_winter_snow_period",
    "wrcc_july_tmax_f",
    "wrcc_july_tmax_station_id", "wrcc_july_tmax_station_name",
    "wrcc_july_tmax_station_dist_mi", "wrcc_july_tmax_station_elev_diff_ft", "wrcc_july_tmax_period",
    "wrcc_winter_tavg_f",
    "wrcc_winter_tavg_station_id", "wrcc_winter_tavg_station_name",
    "wrcc_winter_tavg_station_dist_mi", "wrcc_winter_tavg_station_elev_diff_ft", "wrcc_winter_tavg_period",
    "fetched_at",
]

# ── Thresholds ─────────────────────────────────────────────────────────────────
MAX_DIST_MI  = 30.0
MAX_ELEV_FT  = 1000.0
MIN_YEARS    = 10      # minimum valid years/winters required, per metric
MAX_MISSING_DAYS = 5   # ACIS maxmissing: months with more missing days are "M"
WINTER_SNOW_MONTHS = {11, 12, 1, 2, 3, 4}  # all must be valid for a snow winter
WINTER_TAVG_MONTHS = {12, 1, 2}            # all must be valid for a tavg winter
REFRESH_DAYS = 730

# ── ACIS API ───────────────────────────────────────────────────────────────────
ACIS_BASE   = "https://data.rcc-acis.org"
RATE_LIMIT  = 0.5   # seconds between StnData calls
FLUSH_EVERY = 20
HEADERS     = {"User-Agent": "place-picker/1.0 (personal location research)"}

PREFERRED_SDATE = "1991-01"
PREFERRED_EDATE = "2020-12"


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def _bbox_for(lat: float, lon: float, radius_mi: float = 35.0) -> str:
    """Return 'west,south,east,north' bounding box string slightly larger than radius."""
    lat_deg = radius_mi / 69.0
    lon_deg = radius_mi / (69.0 * math.cos(math.radians(lat)))
    return f"{lon - lon_deg:.4f},{lat - lat_deg:.4f},{lon + lon_deg:.4f},{lat + lat_deg:.4f}"


def _parse_value(s) -> float | None:
    """Parse one ACIS data value: 'M'=missing, 'T'=trace (→0 for snow), numeric."""
    if s is None:
        return None
    s = str(s).strip()
    if not s or s in ("M", "-9999", "-9999.0", ""):
        return None
    if s.upper() == "T":
        return 0.0
    try:
        v = float(s)
        return None if v <= -9000 else v
    except ValueError:
        return None


def _stn_meta(lat: float, lon: float) -> list[dict]:
    """
    Return ACIS station records within a bounding box around (lat, lon),
    enriched with computed dist_mi.  Each record has keys:
        uid, name, state, ll [lon, lat], elev (feet), sids, dist_mi
    """
    bbox = _bbox_for(lat, lon)
    payload = {
        "bbox": bbox,
        "meta": "name,state,sids,ll,elev,uid",
    }
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
        print(f"[wrcc] StnMeta error: {e}", flush=True)
        return []

    stations = []
    for s in data.get("meta", []):
        ll = s.get("ll")  # [lon, lat]
        if not ll or len(ll) < 2:
            continue
        slon, slat = float(ll[0]), float(ll[1])
        dist = _haversine_mi(lat, lon, slat, slon)
        if dist > MAX_DIST_MI:
            continue
        stations.append({
            "uid":    s.get("uid"),
            "name":   s.get("name", ""),
            "state":  s.get("state", ""),
            "ll":     ll,
            "elev":   s.get("elev"),   # feet per ACIS docs
            "sids":   s.get("sids", []),
            "dist_mi": dist,
        })

    stations.sort(key=lambda s: s["dist_mi"])
    return stations


def _preferred_sid(sids: list) -> str | None:
    """Pick the best station ID for display: prefer GHCND (type 6), then COOP (type 2)."""
    preferred = None
    for sid_entry in (sids or []):
        if not isinstance(sid_entry, (list, tuple)) or len(sid_entry) < 2:
            continue
        sid_val, sid_type = str(sid_entry[0]), int(sid_entry[1])
        if sid_type == 6:
            return sid_val
        if sid_type == 2 and preferred is None:
            preferred = sid_val
    if preferred:
        return preferred
    if sids and isinstance(sids[0], (list, tuple)):
        return str(sids[0][0])
    return None


def _stn_data(uid, sdate: str, edate: str) -> list[list] | None:
    """
    Fetch monthly maxt, snow, avgt for a station from ACIS.
    Returns list of [date_str, maxt_str, snow_str, avgt_str] rows, [] when ACIS
    has no data for the station, or None on error.
    """
    payload = {
        "uid": uid,
        "sdate": sdate,
        "edate": edate,
        "elems": [
            # reduce is required: without it ACIS returns each month as a
            # list of daily values instead of a single monthly value.
            # maxmissing makes ACIS return "M" for poorly covered months;
            # without it a "monthly" value can come from one or two days.
            {"name": "maxt", "interval": "mly", "duration": "mly", "reduce": "mean",
             "maxmissing": MAX_MISSING_DAYS},
            {"name": "snow", "interval": "mly", "duration": "mly", "reduce": "sum",
             "maxmissing": MAX_MISSING_DAYS},
            {"name": "avgt", "interval": "mly", "duration": "mly", "reduce": "mean",
             "maxmissing": MAX_MISSING_DAYS},
        ],
        "meta": "uid",
    }
    try:
        resp = requests.post(
            f"{ACIS_BASE}/StnData",
            json=payload,
            headers=HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            # ACIS reports errors as HTTP 200 with an "error" body.  "No data
            # available" means the station has none of these elements in any
            # period (a station that merely closed early returns all-"M" rows),
            # so treat it as an empty result rather than a failure.
            if str(data["error"]).strip().lower() == "no data available":
                return []
            print(f"[wrcc] StnData ACIS error (uid={uid}): {data['error']}", flush=True)
            return None
        return data.get("data") or []
    except Exception as e:
        print(f"[wrcc] StnData error (uid={uid}): {e}", flush=True)
        return None


# short need names used in enrich() → metric value key
_METRIC_KEY = {"snow": "winter_snow_in", "tmax": "july_tmax_f", "tavg": "winter_tavg_f"}


def _summarize(values: dict[int, float], spans_new_year: bool) -> tuple[float | None, str | None]:
    """
    Average per-year values and describe the years used.  values maps year
    (for winters, the year the winter ends) → that year's value.  Returns
    (None, None) when there are fewer than MIN_YEARS valid years.
    """
    if len(values) < MIN_YEARS:
        return None, None
    first, last = min(values), max(values)
    if spans_new_year:
        first -= 1  # winter ending in Jan–Apr of `first` began the year before
    return round(sum(values.values()) / len(values), 1), f"{first}-{last}"


def _compute_metrics(rows: list[list]) -> dict:
    """
    Compute winter_snow_in, july_tmax_f, winter_tavg_f from a list of monthly
    data rows.  Each row: [date_str, maxt_str, snow_str, avgt_str] where
    date_str is "YYYY-MM" and poorly covered months are already "M" (maxmissing).

    Each metric is checked independently and is None unless it has
    ≥ MIN_YEARS valid years:
        winter_snow_in — mean Nov–Apr total over winters with all six months valid
        july_tmax_f    — mean of valid July monthly max temps
        winter_tavg_f  — mean Dec–Feb avg temp over winters with all three months valid

    Returns those keys plus winter_snow_period, july_tmax_period and
    winter_tavg_period, each spanning only the years used for that metric.
    """
    july_tmax   = {}   # year → July mean max temp
    winter_snow = {}   # winter end year → {month: snow}
    winter_tavg = {}   # winter end year → {month: avg temp}

    for row in rows:
        if not row or len(row) < 4:
            continue
        date_str = str(row[0])
        try:
            year = int(date_str[:4])
            month = int(date_str[5:7])
        except (ValueError, IndexError):
            continue

        maxt  = _parse_value(row[1])
        snow  = _parse_value(row[2])
        avgt  = _parse_value(row[3])

        # Winters are keyed by the year they end (Nov/Dec belong to the next year)
        winter = year + 1 if month >= 7 else year

        if month == 7 and maxt is not None:
            july_tmax[year] = maxt

        if month in WINTER_SNOW_MONTHS and snow is not None:
            winter_snow.setdefault(winter, {})[month] = snow

        if month in WINTER_TAVG_MONTHS and avgt is not None:
            winter_tavg.setdefault(winter, {})[month] = avgt

    # Only winters with every required month valid count; a winter missing a
    # month would undercount snow or skew the temperature average.
    snow_totals = {w: sum(m.values()) for w, m in winter_snow.items()
                   if m.keys() == WINTER_SNOW_MONTHS}
    tavg_means  = {w: sum(m.values()) / len(m) for w, m in winter_tavg.items()
                   if m.keys() == WINTER_TAVG_MONTHS}

    winter_snow_in, winter_snow_period = _summarize(snow_totals, spans_new_year=True)
    july_tmax_f, july_tmax_period      = _summarize(july_tmax, spans_new_year=False)
    winter_tavg_f, winter_tavg_period  = _summarize(tavg_means, spans_new_year=True)

    return {
        "winter_snow_in":     winter_snow_in,
        "winter_snow_period": winter_snow_period,
        "july_tmax_f":        july_tmax_f,
        "july_tmax_period":   july_tmax_period,
        "winter_tavg_f":      winter_tavg_f,
        "winter_tavg_period": winter_tavg_period,
    }


def _fetch_station_metrics(uid, sdate: str, edate: str) -> dict | None:
    """
    Fetch and compute a station's metrics for one date window.
    Returns None on request error, {} when ACIS has no data for the station
    at all, otherwise the _compute_metrics dict (individual metrics may be None).
    """
    rows = _stn_data(uid, sdate, edate)
    if rows is None:
        return None
    if not rows:
        return {}
    return _compute_metrics(rows)


# ── Public API ─────────────────────────────────────────────────────────────────

def enrich(candidates: pd.DataFrame, cache_only: bool = False) -> pd.DataFrame:
    """
    Fill wrcc_* columns for places that are still missing at least one of
    (noaa_snow_in, noaa_summer_tmax_f, noaa_winter_tavg_f) after the NOAA step.

    Writes results to wrcc_cache.  Returns candidates with wrcc_* columns merged in.
    """
    os.makedirs("data/processed", exist_ok=True)

    cache = _db.read_cache("wrcc_cache", CACHE_PATH, WRCC_COLS)
    if "fetched_at" not in cache.columns:
        cache["fetched_at"] = pd.NaT
    else:
        cache["fetched_at"] = pd.to_datetime(cache["fetched_at"], errors="coerce")

    today  = pd.Timestamp(pd.Timestamp.now().date())
    cutoff = today - pd.Timedelta(days=REFRESH_DAYS)

    cached_geoids = set(cache["geoid"].tolist())
    stale_geoids  = set(
        cache.loc[cache["fetched_at"] < cutoff, "geoid"].tolist()
    ) if len(cache) else set()

    # Only process places that are missing at least one metric from NOAA
    need_snow  = set()
    need_tmax  = set()
    need_tavg  = set()

    for col, target in [
        ("noaa_snow_in",       need_snow),
        ("noaa_summer_tmax_f", need_tmax),
        ("noaa_winter_tavg_f", need_tavg),
    ]:
        if col in candidates.columns:
            target.update(
                candidates.loc[candidates[col].isna(), "geoid"].tolist()
            )
        else:
            # If the NOAA column doesn't exist at all, every place needs this metric
            target.update(candidates["geoid"].tolist())

    needs_wrcc = need_snow | need_tmax | need_tavg

    # Determine which to fetch: uncached + stale within the needing-wrcc set
    todo_geoids = (needs_wrcc - cached_geoids) | (stale_geoids & needs_wrcc)
    todo = candidates[candidates["geoid"].isin(todo_geoids)].copy()

    n_skip = len(candidates) - len(todo)
    if n_skip > 0:
        print(f"[wrcc] Skipping {n_skip} places (NOAA complete or already cached)")

    if todo.empty:
        print("[wrcc] All eligible places already cached.")
    elif cache_only:
        print(f"[wrcc] cache_only — skipping {len(todo)} places")
    else:
        print(f"[wrcc] Fetching ACIS data for {len(todo)} places "
              f"(snow={len(need_snow & todo_geoids)}, "
              f"tmax={len(need_tmax & todo_geoids)}, "
              f"tavg={len(need_tavg & todo_geoids)} missing)...")

        new_rows = []
        no_data_uids = set()   # stations ACIS has no data for; skipped for the rest of the run

        def _flush():
            nonlocal cache, new_rows
            if not new_rows:
                return
            ndf = pd.DataFrame(new_rows)
            updated = set(ndf["geoid"])
            cache = cache[~cache["geoid"].isin(updated)]
            cache = pd.concat([cache, ndf], ignore_index=True)
            _db.write_cache("wrcc_cache", CACHE_PATH, cache)
            new_rows = []

        for i, row in enumerate(todo.itertuples(), 1):
            lat = getattr(row, "lat", None)
            lon = getattr(row, "lng", None)
            if lat is None or lon is None or pd.isna(lat) or pd.isna(lon):
                new_rows.append({"geoid": row.geoid, "fetched_at": today})
                continue

            elev_ft = getattr(row, "elevation_ft", float("nan"))
            if pd.isna(elev_ft):
                elev_ft = float("nan")

            need_here = set()
            g = row.geoid
            if g in need_snow:
                need_here.add("snow")
            if g in need_tmax:
                need_here.add("tmax")
            if g in need_tavg:
                need_here.add("tavg")

            name_str = getattr(row, "place_name", row.geoid)
            print(f"[wrcc] ({i}/{len(todo)}) {name_str} "
                  f"(need: {','.join(sorted(need_here))})...", flush=True)

            stations = _stn_meta(lat, lon)

            # Filter by elevation constraint
            if not np.isnan(elev_ft):
                stations = [
                    s for s in stations
                    if s["elev"] is None or abs(float(s["elev"]) - elev_ft) <= MAX_ELEV_FT
                ]

            result: dict[str, object] = {"geoid": g, "fetched_at": today}

            if not stations:
                print(f"  → no station within {MAX_DIST_MI}mi/{MAX_ELEV_FT:.0f}ft")
            else:
                done = set()   # need names already filled; later passes never revisit them

                # Pass 1 searches every station for modern (1991-2020) data
                # before pass 2 falls back to historical records, so a nearby
                # historical station can't win over a farther modern one.
                for pass_label, sdate, edate in (
                    ("1991-2020",   PREFERRED_SDATE, PREFERRED_EDATE),
                    ("full record", "por",           "por"),
                ):
                    n_checked = 0
                    for stn in stations:
                        still_need = [m for m in ("snow", "tmax", "tavg")
                                      if m in need_here and m not in done]
                        if not still_need:
                            break
                        if stn["uid"] in no_data_uids:
                            continue

                        metrics = _fetch_station_metrics(stn["uid"], sdate, edate)
                        time.sleep(RATE_LIMIT)
                        n_checked += 1

                        if metrics == {}:
                            no_data_uids.add(stn["uid"])
                            continue
                        if metrics is None:
                            continue

                        stn_elev = float(stn["elev"]) if stn["elev"] is not None else float("nan")
                        elev_diff = abs(stn_elev - elev_ft) if not np.isnan(elev_ft) and not np.isnan(stn_elev) else float("nan")
                        elev_str  = f"{elev_diff:.0f}ft Δelev" if not np.isnan(elev_diff) else "?ft Δelev"

                        sid       = _preferred_sid(stn["sids"])
                        dist_mi   = round(stn["dist_mi"], 2)
                        elev_diff_r = round(elev_diff, 0) if not np.isnan(elev_diff) else None
                        gained    = []

                        if "snow" in still_need and metrics["winter_snow_in"] is not None:
                            period = metrics["winter_snow_period"]
                            result["wrcc_winter_snow_in"]                = metrics["winter_snow_in"]
                            result["wrcc_winter_snow_station_id"]        = sid
                            result["wrcc_winter_snow_station_name"]      = stn["name"]
                            result["wrcc_winter_snow_station_dist_mi"]   = dist_mi
                            result["wrcc_winter_snow_station_elev_diff_ft"] = elev_diff_r
                            result["wrcc_winter_snow_period"]            = period
                            done.add("snow")
                            gained.append(f"Nov–Apr snow {metrics['winter_snow_in']:.1f}\" ({period})")

                        if "tmax" in still_need and metrics["july_tmax_f"] is not None:
                            period = metrics["july_tmax_period"]
                            result["wrcc_july_tmax_f"]               = metrics["july_tmax_f"]
                            result["wrcc_july_tmax_station_id"]      = sid
                            result["wrcc_july_tmax_station_name"]    = stn["name"]
                            result["wrcc_july_tmax_station_dist_mi"] = dist_mi
                            result["wrcc_july_tmax_station_elev_diff_ft"] = elev_diff_r
                            result["wrcc_july_tmax_period"]          = period
                            done.add("tmax")
                            gained.append(f"Jul hi {metrics['july_tmax_f']:.0f}°F ({period})")

                        if "tavg" in still_need and metrics["winter_tavg_f"] is not None:
                            period = metrics["winter_tavg_period"]
                            result["wrcc_winter_tavg_f"]               = metrics["winter_tavg_f"]
                            result["wrcc_winter_tavg_station_id"]      = sid
                            result["wrcc_winter_tavg_station_name"]    = stn["name"]
                            result["wrcc_winter_tavg_station_dist_mi"] = dist_mi
                            result["wrcc_winter_tavg_station_elev_diff_ft"] = elev_diff_r
                            result["wrcc_winter_tavg_period"]          = period
                            done.add("tavg")
                            gained.append(f"Dec–Feb avg {metrics['winter_tavg_f']:.0f}°F ({period})")

                        if gained:
                            print(f"  [{pass_label}] {stn['name']} ({stn['dist_mi']:.1f}mi, "
                                  f"{elev_str}): {', '.join(gained)}", flush=True)

                    left = sorted(need_here - done)
                    if not left:
                        break
                    print(f"  [{pass_label}] checked {n_checked} stations; none with "
                          f"{MIN_YEARS}+ valid years for: {', '.join(left)}", flush=True)

                _metric_col = {m: f"wrcc_{k}" for m, k in _METRIC_KEY.items()}
                filled  = [m for m in need_here if result.get(_metric_col[m]) is not None]
                missing = sorted(need_here - set(filled))
                if missing:
                    print(f"  → partial ({', '.join(missing)} still missing)")
                else:
                    print(f"  → complete")

            new_rows.append(result)

            if i % FLUSH_EVERY == 0:
                _flush()
                print(f"[wrcc] Saved progress ({i}/{len(todo)})")

        _flush()
        print("[wrcc] Done.")

    available = [c for c in WRCC_COLS if c in cache.columns]
    return candidates.merge(cache[available], on="geoid", how="left")
