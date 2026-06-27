"""
pipeline/facilities.py
----------------------
Adds hospital, college, and library proximity data to candidates.

Data sources (all free, no API key required):
    Hospitals  — CMS Hospital General Information API (Medicare-certified)
    Colleges   — IPEDS HD file (NCES directory of all US colleges)
    Libraries  — IMLS Public Libraries Survey outlet file

Strategy:
    1. Try to merge from place-scorer's pre-computed parquets (fast, no computation)
    2. For any candidates not covered, download raw facility data and compute
       haversine distances on the fly
    3. Cache results in data/processed/facilities_cache.parquet

Output columns added to candidates DataFrame:
    hospital_distance_miles  — miles to nearest Acute Care / Critical Access hospital
    hospitals_within_30mi    — count of hospitals within 30 miles
    college_distance_miles   — miles to nearest active 2-year or 4-year college
    colleges_within_30mi     — count of colleges within 30 miles
    library_distance_miles   — miles to nearest public library branch
    libraries_within_10mi    — count of library outlets within 10 miles
"""

import io
import os
import zipfile
import requests
import numpy as np
import pandas as pd

CACHE_PATH   = "data/processed/facilities_cache.parquet"

# place-scorer pre-computed parquets — used as primary source when available
SCORER_DIR   = r"C:\Users\Owner\place-scorer\data\processed"
SCORER_HOSP  = os.path.join(SCORER_DIR, "hospitals.parquet")
SCORER_COLL  = os.path.join(SCORER_DIR, "colleges_libraries.parquet")

# Raw source URLs (fallback)
CMS_URL   = "https://data.cms.gov/provider-data/api/1/datastore/query/xubh-q36u/0"
IPEDS_URL = "https://nces.ed.gov/ipeds/datacenter/data/HD2023.zip"
IMLS_URL  = "https://www.imls.gov/sites/default/files/2024-06/pls_fy2022_csv.zip"
ZCTA_URL  = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_zcta_national.zip"

FACILITY_COLS = [
    "geoid",
    "hospital_distance_miles", "hospitals_within_30mi",
    "college_distance_miles",  "colleges_within_30mi",
    "library_distance_miles",  "libraries_within_10mi",
]


# ---------------------------------------------------------------------------
# Haversine helpers
# ---------------------------------------------------------------------------

def _haversine_matrix(lat1, lng1, lat2, lng2):
    """Distance in miles: every (lat1,lng1) to every (lat2,lng2)."""
    R = 3958.8
    lat1 = np.radians(np.asarray(lat1, float))[:, np.newaxis]
    lng1 = np.radians(np.asarray(lng1, float))[:, np.newaxis]
    lat2 = np.radians(np.asarray(lat2, float))[np.newaxis, :]
    lng2 = np.radians(np.asarray(lng2, float))[np.newaxis, :]
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = np.sin(dlat / 2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlng / 2)**2
    return R * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def _proximity_join(df_places, df_points, radius_mi, chunk=500):
    """Return (nearest_dist_array, count_within_radius_array) for each place."""
    p_lats = df_places["lat"].values
    p_lngs = df_places["lng"].values
    f_lats = df_points["lat"].values
    f_lngs = df_points["lng"].values

    nearest, counts = [], []
    for i in range(0, len(df_places), chunk):
        dist = _haversine_matrix(p_lats[i:i+chunk], p_lngs[i:i+chunk], f_lats, f_lngs)
        nearest.extend(dist.min(axis=1).tolist())
        counts.extend((dist <= radius_mi).sum(axis=1).tolist())
    return np.round(nearest, 2), counts


# ---------------------------------------------------------------------------
# Raw data downloaders (used only when place-scorer cache misses)
# ---------------------------------------------------------------------------

def _fetch_hospitals() -> pd.DataFrame:
    """Download CMS hospital data and geocode via ZCTA centroids."""
    print("[facilities] Downloading hospital data from CMS...")
    all_rows, offset = [], 0
    while True:
        resp = requests.get(CMS_URL, params={"limit": 500, "offset": offset,
                                              "results": "true", "keys": "true"}, timeout=60)
        resp.raise_for_status()
        rows = resp.json().get("results", [])
        all_rows.extend(rows)
        if len(rows) < 500:
            break
        offset += 500
    df = pd.DataFrame(all_rows)
    df = df[df["hospital_type"].isin({"Acute Care Hospitals", "Critical Access Hospitals"})].copy()
    df["zip5"] = df["zip_code"].str.strip().str[:5]

    print("[facilities] Downloading ZCTA Gazetteer for hospital coordinates...")
    resp = requests.get(ZCTA_URL, timeout=60)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        with z.open(z.namelist()[0]) as f:
            gaz = pd.read_csv(f, sep="\t", dtype={"GEOID": str})
    gaz.columns = gaz.columns.str.strip()
    gaz = gaz[["GEOID", "INTPTLAT", "INTPTLONG"]].rename(
        columns={"GEOID": "zip5", "INTPTLAT": "lat", "INTPTLONG": "lng"})
    df = df.merge(gaz, on="zip5", how="left").dropna(subset=["lat", "lng"])
    print(f"[facilities] {len(df):,} hospitals with coordinates")
    return df[["lat", "lng"]]


def _fetch_colleges() -> pd.DataFrame:
    """Download IPEDS HD file and return active 2-year + 4-year institutions."""
    print("[facilities] Downloading IPEDS college data...")
    resp = requests.get(IPEDS_URL, timeout=120)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        fname = [f for f in z.namelist() if not f.endswith("_rv.csv")][0]
        with z.open(fname) as f:
            df = pd.read_csv(f, encoding="latin1", low_memory=False)
    df = df[(df["CYACTIVE"] == 1) & (df["ICLEVEL"].isin([1, 2]))].copy()
    df = df.rename(columns={"LATITUDE": "lat", "LONGITUD": "lng"})
    df = df.dropna(subset=["lat", "lng"])
    df = df[df["lat"] != 0]
    print(f"[facilities] {len(df):,} colleges with coordinates")
    return df[["lat", "lng"]]


def _fetch_libraries() -> pd.DataFrame:
    """Download IMLS library outlet file and return physical branches."""
    print("[facilities] Downloading IMLS library data...")
    resp = requests.get(IMLS_URL, timeout=120)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        fname = [f for f in z.namelist() if "outlet" in f.lower()][0]
        with z.open(fname) as f:
            df = pd.read_csv(f, encoding="latin1", low_memory=False)
    df = df[df["C_OUT_TY"].isin(["CE", "BR"])].copy()
    df = df.rename(columns={"LATITUDE": "lat", "LONGITUD": "lng"})
    df = df.dropna(subset=["lat", "lng"])
    df = df[df["lat"] != 0]
    print(f"[facilities] {len(df):,} library outlets with coordinates")
    return df[["lat", "lng"]]


# ---------------------------------------------------------------------------
# Public enrich() function
# ---------------------------------------------------------------------------

def enrich(candidates: pd.DataFrame) -> pd.DataFrame:
    """
    Add hospital, college, and library proximity columns to candidates.
    Merges from place-scorer pre-computed data first; only runs haversine
    calculations for candidates not already covered.
    """
    # Load or initialize local cache
    if os.path.exists(CACHE_PATH):
        cache = pd.read_parquet(CACHE_PATH)
        for col in FACILITY_COLS[1:]:
            if col not in cache.columns:
                cache[col] = np.nan
    else:
        cache = pd.DataFrame(columns=FACILITY_COLS)

    cached_geoids = set(cache["geoid"].tolist())
    needed = set(candidates["geoid"].tolist()) - cached_geoids

    if not needed:
        print("[facilities] All candidates already in facilities cache.")
    else:
        new_rows = []

        # --- Step 1: merge from place-scorer pre-computed parquets ---
        scorer_geoids = needed.copy()
        if os.path.exists(SCORER_HOSP) and os.path.exists(SCORER_COLL):
            try:
                hosp_df = pd.read_parquet(SCORER_HOSP)
                coll_df = pd.read_parquet(SCORER_COLL)
                merged  = hosp_df.merge(coll_df, on="geoid", how="inner")
                matched = merged[merged["geoid"].isin(needed)]
                if not matched.empty:
                    new_rows.extend(matched[FACILITY_COLS].to_dict("records"))
                    scorer_geoids -= set(matched["geoid"].tolist())
                    print(f"[facilities] {len(matched):,} candidates matched from place-scorer cache")
            except Exception as e:
                print(f"[facilities] Could not read place-scorer cache ({e}) — will download")
                scorer_geoids = needed.copy()
        else:
            scorer_geoids = needed.copy()

        # --- Step 2: compute haversine for any remaining geoids ---
        if scorer_geoids:
            todo = candidates[candidates["geoid"].isin(scorer_geoids)].dropna(subset=["lat", "lng"])
            print(f"[facilities] Computing distances for {len(todo):,} uncached candidates...")

            hosp_pts = _fetch_hospitals()
            coll_pts = _fetch_colleges()
            lib_pts  = _fetch_libraries()

            h_dist, h_cnt = _proximity_join(todo, hosp_pts, radius_mi=30)
            c_dist, c_cnt = _proximity_join(todo, coll_pts, radius_mi=30)
            l_dist, l_cnt = _proximity_join(todo, lib_pts,  radius_mi=10)

            for i, row in enumerate(todo.itertuples()):
                new_rows.append({
                    "geoid":                    row.geoid,
                    "hospital_distance_miles":  h_dist[i],
                    "hospitals_within_30mi":    int(h_cnt[i]),
                    "college_distance_miles":   c_dist[i],
                    "colleges_within_30mi":     int(c_cnt[i]),
                    "library_distance_miles":   l_dist[i],
                    "libraries_within_10mi":    int(l_cnt[i]),
                })

        if new_rows:
            new_df = pd.DataFrame(new_rows)
            cache  = pd.concat([cache, new_df], ignore_index=True)
            os.makedirs("data/processed", exist_ok=True)
            cache.to_parquet(CACHE_PATH, index=False)
            print(f"[facilities] Cache updated → {CACHE_PATH}")

    return candidates.merge(cache[FACILITY_COLS], on="geoid", how="left")
