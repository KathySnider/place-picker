-- place-picker Postgres schema
-- Run automatically by db.py on first connection (CREATE TABLE IF NOT EXISTS is idempotent).
-- The pipeline writes directly to these tables when DATABASE_URL is set.
-- Local dev: leave DATABASE_URL unset and parquet files are used instead.

-- ─────────────────────────────────────────────────────────────────────────────
-- Census universe (~32k rows, refreshed yearly)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS census_places (
    geoid                  TEXT PRIMARY KEY,
    place_name             TEXT,
    place_type             TEXT,
    state_name             TEXT,
    lat                    DOUBLE PRECISION,
    lng                    DOUBLE PRECISION,
    population             BIGINT,
    pop_density            DOUBLE PRECISION,
    metro_pop              BIGINT,
    median_home_value      DOUBLE PRECISION,
    median_gross_rent      DOUBLE PRECISION,
    median_re_taxes        DOUBLE PRECISION,
    pct_owner_occupied     DOUBLE PRECISION,
    median_household_income DOUBLE PRECISION,
    poverty_rate           DOUBLE PRECISION,
    unemployment_rate      DOUBLE PRECISION,
    median_age             DOUBLE PRECISION,
    pct_age_65_plus        DOUBLE PRECISION,
    pct_age_under_18       DOUBLE PRECISION,
    pct_foreign_born       DOUBLE PRECISION,
    pct_college_educated   DOUBLE PRECISION,
    broadband_pct          DOUBLE PRECISION,
    commute_time_avg       DOUBLE PRECISION,
    pct_no_vehicle         DOUBLE PRECISION,
    pct_vacant_housing     DOUBLE PRECISION,
    median_rooms           DOUBLE PRECISION
);

-- ─────────────────────────────────────────────────────────────────────────────
-- OSM walkability scores (per-place, refreshed every 180 days)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS osm_cache (
    geoid           TEXT PRIMARY KEY,
    practical_800m  DOUBLE PRECISION,
    practical_1600m DOUBLE PRECISION,
    lifestyle_800m  DOUBLE PRECISION,
    lifestyle_1600m DOUBLE PRECISION,
    anchor_lat      DOUBLE PRECISION,
    anchor_lng      DOUBLE PRECISION,
    fetched_at      TEXT
);

-- ─────────────────────────────────────────────────────────────────────────────
-- OSM amenity presence (boolean per category, checked via Overpass)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS osm_detail_cache (
    geoid               TEXT PRIMARY KEY,
    detail_fetched_date TEXT,
    has_grocery         BOOLEAN,
    has_pharmacy        BOOLEAN,
    has_medical         BOOLEAN,
    has_bank            BOOLEAN,
    has_atm             BOOLEAN,
    has_post_office     BOOLEAN,
    has_library         BOOLEAN,
    has_restaurant      BOOLEAN,
    has_cafe            BOOLEAN,
    has_bar             BOOLEAN,
    has_shopping        BOOLEAN,
    has_park            BOOLEAN,
    has_arts            BOOLEAN,
    has_transit         BOOLEAN,
    has_beach           BOOLEAN
);

-- ─────────────────────────────────────────────────────────────────────────────
-- OSM trail and footway mileage
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS osm_trails_cache (
    geoid               TEXT PRIMARY KEY,
    trails_fetched_date TEXT,
    trail_miles_10mi    DOUBLE PRECISION,
    footway_miles_1mi   DOUBLE PRECISION
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Daymet climate normals (5-year rolling averages, refreshed every 180 days)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daymet_cache (
    geoid               TEXT PRIMARY KEY,
    winter_temp_f       DOUBLE PRECISION,
    summer_temp_f       DOUBLE PRECISION,
    annual_precip_mm    DOUBLE PRECISION,
    snowfall_swe_mm     DOUBLE PRECISION,
    snowfall_in_approx  DOUBLE PRECISION,
    fetched_at          TEXT
);

-- ─────────────────────────────────────────────────────────────────────────────
-- PRISM terrain-adjusted 30-year climate normals
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS prism_cache (
    geoid         TEXT PRIMARY KEY,
    prism_snow_in DOUBLE PRECISION,
    prism_summer_f DOUBLE PRECISION,
    prism_winter_f DOUBLE PRECISION
);

-- ─────────────────────────────────────────────────────────────────────────────
-- ERA5 reanalysis warming trends (1980–present, computed once per place)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS era5_cache (
    geoid              TEXT PRIMARY KEY,
    summer_f_1980s     DOUBLE PRECISION,
    summer_f_recent    DOUBLE PRECISION,
    summer_trend_f_dec DOUBLE PRECISION,
    winter_f_1980s     DOUBLE PRECISION,
    winter_f_recent    DOUBLE PRECISION,
    winter_trend_f_dec DOUBLE PRECISION,
    snow_mm_1980s      DOUBLE PRECISION,
    snow_mm_recent     DOUBLE PRECISION,
    snow_trend_dec     DOUBLE PRECISION
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Facility proximity (hospitals, colleges, libraries)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS facilities_cache (
    geoid                  TEXT PRIMARY KEY,
    hospital_distance_miles DOUBLE PRECISION,
    hospitals_within_30mi  BIGINT,
    college_distance_miles DOUBLE PRECISION,
    colleges_within_30mi   BIGINT,
    library_distance_miles DOUBLE PRECISION,
    libraries_within_10mi  BIGINT
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Coastal proximity (Census TIGER/Line coastline, computed once per place)
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS coastal_cache (
    geoid                TEXT PRIMARY KEY,
    coast_distance_miles DOUBLE PRECISION,
    is_coastal           BOOLEAN
);
