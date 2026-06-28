"""
pipeline/census.py
------------------
Fetches all US Census places from the ACS 5-year API and caches them locally.

Columns returned:
    geoid, place_name, state_name, lat, lng,
    population, pop_density,
    median_home_value, median_gross_rent, median_re_taxes,
    median_household_income, median_age,
    broadband_pct,
    pct_age_65_plus, pct_age_under_18, pct_college_educated, pct_owner_occupied,
    poverty_rate, unemployment_rate, pct_foreign_born,
    commute_time_avg, pct_no_vehicle,
    pct_vacant_housing, median_rooms

No population minimum is applied here — the full ~32k place universe is cached.
Filtering by population range happens in search.py based on config.py settings.
"""

import os
import io
import json
import sys
import zipfile
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import db as _db

load_dotenv()

CACHE_PATH = "data/processed/census_places.parquet"
META_PATH  = "data/processed/census_places_meta.json"


CACHE_MAX_DAYS = 365   # re-download census data after this many days


def load(
    population: dict,
    regions: list[str],
    states: list[str],
    metro_max: int | None,
    home_value_max: int | None = None,
    rent_max: int | None = None,
) -> pd.DataFrame:
    """
    Return filtered candidates using a SQL WHERE clause when Postgres is
    available, otherwise fall back to fetch() + in-memory filtering.
    """
    from regions import region_to_states
    all_states = region_to_states(regions) if regions else []
    if states:
        all_states = list(dict.fromkeys(all_states + states))

    engine = _db.engine()
    if engine is not None:
        try:
            df = _db.read_cache("census_places", CACHE_PATH, [])
            if not df.empty:
                before = len(df)
                df = df[df["population"].between(population["min"], population["max"])]
                if all_states:
                    df = df[df["state_name"].isin(all_states)]
                if metro_max is not None and "cbsa_pop" in df.columns:
                    df = df[df["cbsa_pop"].isna() | (df["cbsa_pop"] <= metro_max)]
                if home_value_max is not None:
                    df = df[df["median_home_value"].isna() | (df["median_home_value"] <= home_value_max)]
                if rent_max is not None:
                    df = df[df["median_gross_rent"].isna() | (df["median_gross_rent"] <= rent_max)]
                print(f"[census] Filtered {before:,} → {len(df):,} places from Postgres cache")
                return df
        except Exception as e:
            print(f"[census] Postgres read failed ({e}), falling back to full fetch")

    # Fallback: read full table and filter in Python
    df = fetch()
    df = df[df["population"].between(population["min"], population["max"])]
    if all_states:
        df = df[df["state_name"].isin(all_states)]
    if metro_max is not None and "cbsa_pop" in df.columns:
        df = df[df["cbsa_pop"].isna() | (df["cbsa_pop"] <= metro_max)]
    if home_value_max is not None:
        df = df[df["median_home_value"].isna() | (df["median_home_value"] <= home_value_max)]
    if rent_max is not None:
        df = df[df["median_gross_rent"].isna() | (df["median_gross_rent"] <= rent_max)]
    return df


def fetch(force_refresh: bool = False) -> pd.DataFrame:
    """
    Return a DataFrame of all US Census places with coordinates and key ACS fields.
    Uses a local cache; automatically refreshes if the cache is older than
    CACHE_MAX_DAYS. Pass force_refresh=True to force an immediate re-download.
    """
    if not force_refresh:
        cached = _db.read_cache("census_places", CACHE_PATH, [])
        if not cached.empty:
            age_days = _cache_age_days()
            if age_days is not None and age_days > CACHE_MAX_DAYS:
                print(f"[census] Cache is {age_days} days old (> {CACHE_MAX_DAYS}) — refreshing...")
            else:
                msg = f" ({age_days} days old)" if age_days is not None else ""
                print(f"[census] Loaded from cache{msg}")
                return cached

    return _download_and_cache()


def _cache_age_days() -> int | None:
    """Return age of the census cache in days, or None if no metadata exists."""
    if not os.path.exists(META_PATH):
        return None
    try:
        with open(META_PATH) as f:
            meta = json.load(f)
        downloaded_at = datetime.strptime(
            meta["downloaded_at"], "%Y-%m-%d %H:%M UTC"
        ).replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - downloaded_at
        return delta.days
    except Exception:
        return None


def _download_and_cache() -> pd.DataFrame:
    key = os.getenv("CENSUS_API_KEY")
    if not key:
        raise EnvironmentError("CENSUS_API_KEY not set in .env")

    # Auto-detect latest ACS 5-year vintage
    print("[census] Checking latest ACS vintage...")
    catalog = requests.get("https://api.census.gov/data.json").json()
    vintage = str(max(
        int(d["c_vintage"])
        for d in catalog["dataset"]
        if "acs5" in d.get("c_dataset", []) and d.get("c_vintage")
    ))
    print(f"[census] Using ACS {vintage}")

    # Fetch ACS variables
    variables = ",".join([
        "NAME",
        # Population & density base
        "B01003_001E",   # total population
        # Housing
        "B25077_001E",   # median home value
        "B25058_001E",   # median gross rent
        "B25103_001E",   # median real estate taxes paid
        "B25003_001E",   # total occupied housing units
        "B25003_002E",   # owner-occupied units
        # Income & economy
        "B19013_001E",   # median household income
        "B17001_001E",   # poverty status total
        "B17001_002E",   # below poverty level
        "B23025_003E",   # civilian labor force
        "B23025_005E",   # unemployed
        # Demographics
        "B01002_001E",   # median age
        "B01001_020E",   # male 65-66
        "B01001_021E",   # male 67-69
        "B01001_022E",   # male 70-74
        "B01001_023E",   # male 75-79
        "B01001_024E",   # male 80-84
        "B01001_025E",   # male 85+
        "B01001_044E",   # female 65-66
        "B01001_045E",   # female 67-69
        "B01001_046E",   # female 70-74
        "B01001_047E",   # female 75-79
        "B01001_048E",   # female 80-84
        "B01001_049E",   # female 85+
        # Education
        "B15003_001E",   # education total (25+)
        "B15003_022E",   # bachelor's degree
        "B15003_023E",   # master's degree
        "B15003_024E",   # professional degree
        "B15003_025E",   # doctorate
        # Broadband
        "B28002_001E",   # total households
        "B28002_004E",   # households with broadband
        # Commute & transportation
        "B08013_001E",   # aggregate travel time to work
        "B08101_001E",   # total workers (commute base)
        "B08201_001E",   # total households (vehicle base)
        "B08201_002E",   # households with no vehicle
        # Foreign born
        "B05002_001E",   # nativity total
        "B05002_013E",   # foreign born
        # Age — under 18
        "B01001_003E",   # male under 5
        "B01001_004E",   # male 5-9
        "B01001_005E",   # male 10-14
        "B01001_006E",   # male 15-17
        "B01001_027E",   # female under 5
        "B01001_028E",   # female 5-9
        "B01001_029E",   # female 10-14
        "B01001_030E",   # female 15-17
        # Housing stock
        "B25002_001E",   # total housing units
        "B25002_003E",   # vacant housing units
        "B25018_001E",   # median number of rooms
    ])

    print("[census] Fetching ACS data for all places...")
    resp = requests.get(
        f"https://api.census.gov/data/{vintage}/acs/acs5",
        params={"get": variables, "for": "place:*", "in": "state:*", "key": key},
    )
    resp.raise_for_status()
    data = resp.json()
    df = pd.DataFrame(data[1:], columns=data[0])
    print(f"[census] Got {len(df):,} places from ACS")

    # Rename and cast
    df = df.rename(columns={
        "NAME":        "place_name_raw",
        "B01003_001E": "population",
        "B25077_001E": "median_home_value",
        "B25058_001E": "median_gross_rent",
        "B25103_001E": "median_re_taxes",
        "B25003_001E": "housing_total",
        "B25003_002E": "housing_owner_occ",
        "B19013_001E": "median_household_income",
        "B17001_001E": "poverty_total",
        "B17001_002E": "poverty_below",
        "B23025_003E": "labor_force",
        "B23025_005E": "unemployed",
        "B01002_001E": "median_age",
        "B01001_020E": "m65_66", "B01001_021E": "m67_69",
        "B01001_022E": "m70_74", "B01001_023E": "m75_79",
        "B01001_024E": "m80_84", "B01001_025E": "m85plus",
        "B01001_044E": "f65_66", "B01001_045E": "f67_69",
        "B01001_046E": "f70_74", "B01001_047E": "f75_79",
        "B01001_048E": "f80_84", "B01001_049E": "f85plus",
        "B15003_001E": "edu_total",
        "B15003_022E": "edu_bachelors",
        "B15003_023E": "edu_masters",
        "B15003_024E": "edu_professional",
        "B15003_025E": "edu_doctorate",
        "B28002_001E": "bb_total_households",
        "B28002_004E": "bb_broadband_households",
        "B08013_001E": "agg_commute_time",
        "B08101_001E": "commute_workers",
        "B08201_001E": "vehicle_households",
        "B08201_002E": "no_vehicle_households",
        "B05002_001E": "nativity_total",
        "B05002_013E": "foreign_born",
        "B01001_003E": "m_under5",  "B01001_004E": "m5_9",
        "B01001_005E": "m10_14",    "B01001_006E": "m15_17",
        "B01001_027E": "f_under5",  "B01001_028E": "f5_9",
        "B01001_029E": "f10_14",    "B01001_030E": "f15_17",
        "B25002_001E": "housing_units_total",
        "B25002_003E": "housing_units_vacant",
        "B25018_001E": "median_rooms",
        "state":       "state_fips",
        "place":       "place_fips",
    })

    # Cast everything numeric
    raw_cols = [c for c in df.columns if c not in
                ("place_name_raw", "state_fips", "place_fips")]
    df[raw_cols] = df[raw_cols].apply(pd.to_numeric, errors="coerce").replace(-666666666, np.nan)

    # --- Derived columns ---
    df["broadband_pct"] = (
        df["bb_broadband_households"] / df["bb_total_households"] * 100
    ).round(1)

    age_65_cols = ["m65_66","m67_69","m70_74","m75_79","m80_84","m85plus",
                   "f65_66","f67_69","f70_74","f75_79","f80_84","f85plus"]
    df["pct_age_65_plus"] = (
        df[age_65_cols].sum(axis=1) / df["population"] * 100
    ).round(1)

    age_under18_cols = ["m_under5","m5_9","m10_14","m15_17",
                        "f_under5","f5_9","f10_14","f15_17"]
    df["pct_age_under_18"] = (
        df[age_under18_cols].sum(axis=1) / df["population"] * 100
    ).round(1)

    df["pct_vacant_housing"] = (
        df["housing_units_vacant"] / df["housing_units_total"] * 100
    ).round(1)

    df["median_rooms"] = df["median_rooms"].round(1)

    df["pct_college_educated"] = (
        (df["edu_bachelors"] + df["edu_masters"] +
         df["edu_professional"] + df["edu_doctorate"])
        / df["edu_total"] * 100
    ).round(1)

    df["pct_owner_occupied"] = (
        df["housing_owner_occ"] / df["housing_total"] * 100
    ).round(1)

    df["poverty_rate"] = (
        df["poverty_below"] / df["poverty_total"] * 100
    ).round(1)

    df["unemployment_rate"] = (
        df["unemployed"] / df["labor_force"] * 100
    ).round(1)

    df["pct_foreign_born"] = (
        df["foreign_born"] / df["nativity_total"] * 100
    ).round(1)

    df["commute_time_avg"] = (
        df["agg_commute_time"] / df["commute_workers"]
    ).round(1)

    df["pct_no_vehicle"] = (
        df["no_vehicle_households"] / df["vehicle_households"] * 100
    ).round(1)

    split = df["place_name_raw"].str.rsplit(",", n=1, expand=True)
    df["place_name_full"] = split[0].str.strip()
    df["state_name"]      = split[1].str.strip()

    # Split "Pittsfield city" → place_name="Pittsfield", place_type="city"
    PLACE_TYPES = (
        "city", "town", "village", "CDP", "borough", "township",
        "municipality", "plantation", "gore", "grant", "location",
        "comunidad", "zona urbana",
    )
    type_pattern = r"\s+(" + "|".join(PLACE_TYPES) + r")$"
    extracted = df["place_name_full"].str.extract(type_pattern, expand=False)
    df["place_type"] = extracted.fillna("")
    df["place_name"] = df["place_name_full"].str.replace(
        type_pattern, "", regex=True
    ).str.strip()
    df["geoid"]      = df["state_fips"] + df["place_fips"]

    # Attach lat/lng from Gazetteer
    df = _attach_gazetteer(df, vintage)

    # Attach metro area population from CBSA crosswalk
    df = _attach_cbsa(df)

    # Population density (people per sq km)
    df["pop_density"] = (
        df["population"] / (df["land_sqm"] / 1_000_000)
    ).replace([float("inf"), float("-inf")], pd.NA).round(1)

    final = [
        # Identity & geography
        "geoid", "place_name", "place_type", "state_name", "lat", "lng",
        # Population
        "population", "pop_density", "metro_pop",
        # Housing & affordability
        "median_home_value", "median_gross_rent", "median_re_taxes",
        "pct_owner_occupied",
        # Income & economy
        "median_household_income", "poverty_rate", "unemployment_rate",
        # Demographics
        "median_age", "pct_age_65_plus", "pct_age_under_18", "pct_foreign_born",
        # Education
        "pct_college_educated",
        # Connectivity
        "broadband_pct",
        # Transportation
        "commute_time_avg", "pct_no_vehicle",
        # Housing stock
        "pct_vacant_housing", "median_rooms",
    ]
    df = df[final].reset_index(drop=True)

    _db.write_cache("census_places", CACHE_PATH, df)

    meta = {
        "acs_vintage":   vintage,
        "downloaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "rows":          len(df),
    }
    with open(META_PATH, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"[census] Cached {len(df):,} places → {CACHE_PATH}")
    return df


_VINTAGE_CACHE: str | None = None

def _latest_acs_vintage() -> str:
    """Return the latest available ACS 5-year vintage year as a string.
    Cached in memory so the slow data.json call only happens once per run."""
    global _VINTAGE_CACHE
    if _VINTAGE_CACHE:
        return _VINTAGE_CACHE
    catalog = requests.get("https://api.census.gov/data.json").json()
    _VINTAGE_CACHE = str(max(
        int(d["c_vintage"])
        for d in catalog["dataset"]
        if "acs5" in d.get("c_dataset", []) and d.get("c_vintage")
    ))
    return _VINTAGE_CACHE


def _attach_gazetteer(df: pd.DataFrame, year: str) -> pd.DataFrame:
    """Download the Census Gazetteer and join lat/lng to the places DataFrame."""
    print(f"[census] Downloading Gazetteer ({year})...")
    url = (
        f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
        f"{year}_Gazetteer/{year}_Gaz_place_national.zip"
    )
    resp = requests.get(url)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        with z.open(z.namelist()[0]) as f:
            gaz = pd.read_csv(f, sep="\t", dtype={"GEOID": str})

    gaz.columns = gaz.columns.str.strip()
    gaz = gaz[["GEOID", "INTPTLAT", "INTPTLONG", "ALAND"]].rename(columns={
        "GEOID":     "geoid",
        "INTPTLAT":  "lat",
        "INTPTLONG": "lng",
        "ALAND":     "land_sqm",
    })
    gaz["geoid"] = gaz["geoid"].str.zfill(7)

    before = len(df)
    df = df.merge(gaz, on="geoid", how="left")
    missing = df["lat"].isna().sum()
    print(f"[census] Gazetteer joined — {missing} places missing lat/lng ({missing/before*100:.1f}%)")
    return df


def _attach_cbsa(df: pd.DataFrame) -> pd.DataFrame:
    """
    Join metro area population to each place via the Census CBSA crosswalk.

    Places not in any metro area get metro_pop = 0 (they are truly standalone).
    Places in a metro get the total CBSA population — so East Pittsburgh gets
    the full Pittsburgh metro population (~2.3M), not just its own 1,800.
    """
    print("[census] Downloading CBSA crosswalk...")

    # Try multiple known URLs — Census occasionally moves/renames these files
    CBSA_URLS = [
        # County-to-CBSA delineation file (has CBSA populations)
        "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/delineation-files/list1_2023.xls",
        "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2023/delineation-files/list1_2023.xlsx",
        "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2020/delineation-files/list1_2020.xls",
        "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files/2020/delineation-files/list1_2020.xlsx",
    ]
    os.makedirs("data/raw", exist_ok=True)

    def _try_urls(urls: list[str], cache_name: str) -> pd.DataFrame | None:
        # Check local cache first
        for ext in (".xlsx", ".xls"):
            local = f"data/raw/{cache_name}{ext}"
            if os.path.exists(local):
                try:
                    df_out = pd.read_excel(local, sheet_name=0, header=2, dtype=str)
                    print(f"[census]   Loaded {local} ({len(df_out):,} rows, cols: {list(df_out.columns)[:6]})")
                    return df_out
                except Exception as e:
                    print(f"[census]   Local file corrupt ({e}), re-downloading...")
                    os.remove(local)

        for url in urls:
            try:
                resp = requests.get(url, timeout=30)
                if resp.status_code != 200:
                    continue
                ext = ".xlsx" if url.endswith(".xlsx") else ".xls"
                local = f"data/raw/{cache_name}{ext}"
                with open(local, "wb") as f:
                    f.write(resp.content)
                df_out = pd.read_excel(local, sheet_name=0, header=2, dtype=str)
                print(f"[census]   Got {url.split('/')[-1]} ({len(df_out):,} rows, cols: {list(df_out.columns)[:6]})")
                return df_out
            except Exception as e:
                print(f"[census]   Failed {url.split('/')[-1]}: {e}")
                continue
        return None

    crosswalk = _try_urls(CBSA_URLS, "cbsa_county_crosswalk")
    if crosswalk is None:
        print("[census] CBSA delineation file unavailable — metro_pop will be NaN")
        df["metro_pop"] = np.nan
        return df

    crosswalk.columns = crosswalk.columns.str.strip()
    cbsa_col = next((c for c in crosswalk.columns if "cbsa code" in c.lower()), None)
    if not cbsa_col:
        print(f"[census] Unexpected CBSA columns: {list(crosswalk.columns)[:8]} — metro_pop will be NaN")
        df["metro_pop"] = np.nan
        return df

    # Get CBSA populations from the Census API (separate from the delineation file)
    key = os.getenv("CENSUS_API_KEY")
    try:
        print("[census] Fetching CBSA populations from Census API...")
        cbsa_resp = requests.get(
            f"https://api.census.gov/data/{_latest_acs_vintage()}/acs/acs5",
            params={
                "get": "NAME,B01003_001E",
                "for": "metropolitan statistical area/micropolitan statistical area:*",
                "key": key,
            },
            timeout=30,
        )
        cbsa_resp.raise_for_status()
        cbsa_data = cbsa_resp.json()
        cbsa_pop = pd.DataFrame(cbsa_data[1:], columns=cbsa_data[0])
        cbsa_pop = cbsa_pop.rename(columns={
            "metropolitan statistical area/micropolitan statistical area": "cbsa_code",
            "B01003_001E": "metro_pop",
        })
        cbsa_pop["metro_pop"] = pd.to_numeric(cbsa_pop["metro_pop"], errors="coerce")
        cbsa_pop = cbsa_pop[["cbsa_code", "metro_pop"]].dropna()
        print(f"[census] Got populations for {len(cbsa_pop):,} CBSAs")
    except Exception as e:
        print(f"[census] CBSA population API call failed ({e}) — metro_pop will be NaN")
        df["metro_pop"] = np.nan
        return df

    # Step 2: place → county via nearest county centroid (using county Gazetteer)
    # This avoids fragile Census relationship file URLs entirely.
    local_cgaz = "data/raw/county_gazetteer.txt"
    try:
        if not os.path.exists(local_cgaz):
            print(f"[census] Downloading county Gazetteer...")
            vintage = _latest_acs_vintage()
            # Try multiple URL patterns — Census is inconsistent with naming
            candidates = []
            # Non-versioned file (most stable)
            candidates.append(("current", "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/Gaz_counties_national.zip"))
            candidates.append(("current", "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/Gaz_counties_national.txt"))
            # Versioned files
            for yr in [vintage, str(int(vintage)-1), str(int(vintage)-2)]:
                for name in ["counties", "county"]:
                    for ext in [".zip", ".txt"]:
                        candidates.append((yr, (
                            f"https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
                            f"{yr}_Gazetteer/{yr}_Gaz_{name}_national{ext}"
                        )))

            downloaded = False
            for yr, url in candidates:
                resp = requests.get(url, timeout=60)
                if resp.status_code == 200:
                    print(f"[census]   Got county Gazetteer ({yr}, {url.split('/')[-1]})")
                    if url.endswith(".zip"):
                        import zipfile as zf
                        with zf.ZipFile(io.BytesIO(resp.content)) as z:
                            with z.open(z.namelist()[0]) as zfp:
                                content = zfp.read()
                    else:
                        content = resp.content
                    with open(local_cgaz, "wb") as f:
                        f.write(content)
                    downloaded = True
                    break
            if not downloaded:
                raise Exception("County Gazetteer not found for any recent vintage")
        else:
            print("[census] Loaded county Gazetteer from cache")

        cgaz = pd.read_csv(local_cgaz, sep="\t", dtype={"GEOID": str}, encoding="latin-1")
        cgaz.columns = cgaz.columns.str.strip()
        cgaz["county_fips"] = cgaz["GEOID"].str.zfill(5)
        cgaz = cgaz[["county_fips", "INTPTLAT", "INTPTLONG"]].dropna()
        cgaz = cgaz.rename(columns={"INTPTLAT": "clat", "INTPTLONG": "clng"})
        cgaz[["clat", "clng"]] = cgaz[["clat", "clng"]].apply(pd.to_numeric, errors="coerce")
        cgaz = cgaz.dropna()

        # Nearest-county assignment using vectorised Haversine
        print(f"[census] Assigning {len(df):,} places to nearest county centroid...")
        place_lats = np.radians(df["lat"].values)
        place_lons = np.radians(df["lng"].values)
        c_lats     = np.radians(cgaz["clat"].values)
        c_lons     = np.radians(cgaz["clng"].values)

        # Chunked to avoid huge memory spike (32k × 3k float64 = ~750MB)
        chunk = 500
        county_idx = np.empty(len(df), dtype=int)
        for start in range(0, len(df), chunk):
            end = min(start + chunk, len(df))
            dlat = c_lats[None, :] - place_lats[start:end, None]
            dlon = c_lons[None, :] - place_lons[start:end, None]
            a = (np.sin(dlat / 2) ** 2
                 + np.cos(place_lats[start:end, None])
                 * np.cos(c_lats[None, :])
                 * np.sin(dlon / 2) ** 2)
            county_idx[start:end] = np.argmin(a, axis=1)

        county_fips_arr = cgaz["county_fips"].values
        df["county_fips"] = county_fips_arr[county_idx]

    except Exception as e:
        print(f"[census] County Gazetteer unavailable ({e}) — metro_pop will be NaN")
        df["metro_pop"] = np.nan
        return df

    place_county = df[["geoid", "county_fips"]].copy()

    # Step 3: county → CBSA from list1
    crosswalk.columns = crosswalk.columns.str.strip()
    cbsa_col   = next((c for c in crosswalk.columns if c.strip() == "CBSA Code"), None)
    st_col     = next((c for c in crosswalk.columns if "fips state" in c.lower()), None)
    county_col = next((c for c in crosswalk.columns if "fips county" in c.lower()), None)

    if not all([cbsa_col, st_col, county_col]):
        print(f"[census] list1 columns: {list(crosswalk.columns)} — metro_pop will be NaN")
        df["metro_pop"] = np.nan
        return df

    crosswalk["county_fips"] = (
        crosswalk[st_col].str.zfill(2) + crosswalk[county_col].str.zfill(3)
    )
    county_cbsa = (
        crosswalk[["county_fips", cbsa_col]]
        .rename(columns={cbsa_col: "cbsa_code"})
        .drop_duplicates("county_fips")
    )

    # Chain: place → county → CBSA → metro_pop
    place_metro = (
        place_county
        .merge(county_cbsa, on="county_fips", how="left")
        .merge(cbsa_pop,    on="cbsa_code",   how="left")
    )

    df = df.merge(place_metro[["geoid", "metro_pop"]], on="geoid", how="left")
    df["metro_pop"] = df["metro_pop"].fillna(0).astype(int)
    df = df.drop(columns=["county_fips"], errors="ignore")

    n_metro = (df["metro_pop"] > 0).sum()
    print(f"[census] CBSA joined — {n_metro:,} places in a metro area")
    return df
