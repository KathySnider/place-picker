"""
search.py
---------
Main entry point for place-picker.

Pipeline:
    1. Load (or download) the full ~32k Census place universe
    2. Apply hard filters from config.py  (population, state, home value, rent)
    3. Rough-rank the filtered places by a quick Census-only score
    4. Take the top CANDIDATES places
    5. Enrich with OSM walkability + Daymet climate data in parallel
    6. Final score using config.py WEIGHTS
    7. Print the top RESULTS places

Run:
    python search.py

Edit config.py to change what you're looking for.
"""

import sys
import time
import threading
import concurrent.futures
import pandas as pd

from pipeline import census, osm, daymet, era5, prism, score, state_tax, facilities, osm_detail, osm_trails, coastal
from regions import region_to_states, CONUS
import config


# ---------------------------------------------------------------------------
# Rough Census-only scoring for candidate selection
# These weights don't have to match config.py — they're just for picking
# which 40 places get the expensive API calls.
# ---------------------------------------------------------------------------

def _rough_rank(df: pd.DataFrame) -> pd.DataFrame:
    """
    Assign a rough_score based on Census data only.
    Purpose: narrow down to CANDIDATES before making API calls.
    """
    scored = df.copy()
    rough = pd.Series(0.0, index=df.index)
    n = 0

    def _norm(col: str, higher_is_better: bool):
        s = pd.to_numeric(scored[col], errors="coerce")
        if s.isna().all():
            return pd.Series(0.0, index=s.index)
        lo, hi = s.min(), s.max()
        if hi == lo:
            return pd.Series(0.5, index=s.index)
        raw = (s - lo) / (hi - lo)
        return raw if higher_is_better else (1 - raw)

    # If walkability is weighted, use pct_no_vehicle as proxy —
    # households without cars strongly signal a walkable town.
    # Fall back to pop_density, then population if column not available.
    walk_weight = (config.WEIGHTS.get("practical_800m",  0) +
                   config.WEIGHTS.get("practical_1600m", 0) +
                   config.WEIGHTS.get("lifestyle_800m",  0) +
                   config.WEIGHTS.get("lifestyle_1600m", 0))
    if walk_weight > 0:
        if "pct_no_vehicle" in scored.columns and scored["pct_no_vehicle"].notna().any():
            rough += _norm("pct_no_vehicle", True) * walk_weight
        elif "pop_density" in scored.columns:
            rough += _norm("pop_density", True) * walk_weight
        else:
            rough += _norm("population", True) * walk_weight
        n += walk_weight

    if config.WEIGHTS.get("home_value", 0) > 0:
        rough += _norm("median_home_value", False) * config.WEIGHTS["home_value"]
        n += config.WEIGHTS["home_value"]

    if config.WEIGHTS.get("median_rent", 0) > 0:
        rough += _norm("median_gross_rent", False) * config.WEIGHTS["median_rent"]
        n += config.WEIGHTS["median_rent"]

    # If still no scoreable weights, fall back through proxies
    if n == 0:
        if "pct_no_vehicle" in scored.columns and scored["pct_no_vehicle"].notna().any():
            rough = _norm("pct_no_vehicle", True)
        elif "pop_density" in scored.columns:
            rough = _norm("pop_density", True)
        else:
            rough = _norm("population", True)

    scored["rough_score"] = rough / max(n, 1)
    return scored.sort_values("rough_score", ascending=False)


def _rough_score(df: pd.DataFrame, cfg) -> pd.DataFrame:
    """Parameterized version of _rough_rank for use by the API (takes cfg, not global config)."""
    weights = cfg.WEIGHTS
    scored = df.copy()
    rough = pd.Series(0.0, index=df.index)
    n = 0

    def _norm(col: str, higher_is_better: bool):
        s = pd.to_numeric(scored[col], errors="coerce")
        if s.isna().all():
            return pd.Series(0.0, index=s.index)
        lo, hi = s.min(), s.max()
        if hi == lo:
            return pd.Series(0.5, index=s.index)
        raw = (s - lo) / (hi - lo)
        return raw if higher_is_better else (1 - raw)

    walk_weight = (weights.get("practical_800m", 0) + weights.get("practical_1600m", 0) +
                   weights.get("lifestyle_800m", 0) + weights.get("lifestyle_1600m", 0))
    if walk_weight > 0:
        if "pct_no_vehicle" in scored.columns and scored["pct_no_vehicle"].notna().any():
            rough += _norm("pct_no_vehicle", True) * walk_weight
        elif "pop_density" in scored.columns:
            rough += _norm("pop_density", True) * walk_weight
        else:
            rough += _norm("population", True) * walk_weight
        n += walk_weight

    if weights.get("home_value", 0) > 0:
        rough += _norm("median_home_value", False) * weights["home_value"]
        n += weights["home_value"]

    if weights.get("median_rent", 0) > 0:
        rough += _norm("median_gross_rent", False) * weights["median_rent"]
        n += weights["median_rent"]

    if n == 0:
        if "pct_no_vehicle" in scored.columns and scored["pct_no_vehicle"].notna().any():
            rough = _norm("pct_no_vehicle", True)
        elif "pop_density" in scored.columns:
            rough = _norm("pop_density", True)
        else:
            rough = _norm("population", True)

    scored["rough_score"] = rough / max(n, 1)
    return scored.sort_values("rough_score", ascending=False)


def _apply_climate_chain(candidates: pd.DataFrame, cfg) -> pd.DataFrame:
    """
    Build best-available climate columns (PRISM > ERA5 > Daymet) and apply
    hard cutoffs from cfg. Used by both search.py and the API route.
    """
    def _col(col):
        return candidates[col] if col in candidates.columns else pd.Series(dtype=float, index=candidates.index)

    out = candidates.copy()
    out["snow_era5_in"]      = _col("snow_mm_recent") * 10 / 25.4
    out["snow_best"]         = _col("prism_snow_in").fillna(out["snow_era5_in"]).fillna(_col("snowfall_in_approx"))
    out["winter_temp_best"]  = _col("prism_winter_f").fillna(_col("winter_f_recent")).fillna(_col("winter_temp_f"))
    out["summer_temp_f"]     = _col("prism_summer_f").fillna(_col("summer_f_recent")).fillna(_col("summer_temp_f"))

    if getattr(cfg, "SNOW_MIN_IN", None):
        out = out[out["snow_best"].isna() | (out["snow_best"] >= cfg.SNOW_MIN_IN)]
    if getattr(cfg, "SNOW_MAX_IN", None):
        out = out[out["snow_best"].isna() | (out["snow_best"] <= cfg.SNOW_MAX_IN)]
    if getattr(cfg, "SUMMER_MAX_F", None):
        out = out[out["summer_temp_f"].isna() | (out["summer_temp_f"] <= cfg.SUMMER_MAX_F)]
    if getattr(cfg, "WINTER_MIN_F", None):
        out = out[out["winter_temp_best"].isna() | (out["winter_temp_best"] >= cfg.WINTER_MIN_F)]
    if getattr(cfg, "SUMMER_TREND_MAX", None):
        out = out[out["summer_trend_f_dec"].isna() | (out["summer_trend_f_dec"] <= cfg.SUMMER_TREND_MAX)]
    if getattr(cfg, "WINTER_TREND_MAX", None):
        out = out[out["winter_trend_f_dec"].isna() | (out["winter_trend_f_dec"] <= cfg.WINTER_TREND_MAX)]

    return out


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _fmt_temp(f_val, unit: str) -> str:
    """Format a temperature value in the chosen unit."""
    if unit == "C":
        return f"{(f_val - 32) * 5 / 9:.1f}°C"
    return f"{f_val:.1f}°F"

def _fmt_trend(f_per_dec: float, unit: str) -> str:
    """Format a warming trend (°F/decade) in the chosen unit."""
    sign = "+" if f_per_dec >= 0 else ""
    if unit == "C":
        val = f_per_dec * 5 / 9
        return f"{sign}{val:.2f}°C/dec"
    return f"{sign}{f_per_dec:.2f}°F/dec"

def _fmt_snow(in_val: float, unit: str) -> str:
    """Format a snowfall value in the chosen unit."""
    if unit == "mm":
        return f"~{in_val * 25.4:.0f} mm/yr"
    return f"~{in_val:.0f} in/yr"

def _display_results(ranked: pd.DataFrame, n: int):
    """Pretty-print the top n results."""
    top = ranked.head(n).reset_index(drop=True)

    imperial = getattr(config, "UNITS", "imperial") == "imperial"
    tu = "F"  if imperial else "C"
    su = "in" if imperial else "mm"

    score_cols = [c for c in top.columns if c.startswith("score_")]

    print(f"\n{'='*72}")
    print(f"  TOP {n} PLACES")
    print(f"{'='*72}\n")

    for i, row in top.iterrows():
        ptype = f" ({row['place_type']})" if row.get("place_type") else ""
        print(f"  {i+1:>2}. {row['place_name']}{ptype}, {row['state_name']}")
        print(f"      Composite score: {row['composite_score']:.3f}")

        # Population and affordability
        pop   = f"{int(row['population']):,}"          if pd.notna(row.get('population'))         else "N/A"
        hv    = f"${int(row['median_home_value']):,}"  if pd.notna(row.get('median_home_value'))   else "N/A"
        rent  = f"${int(row['median_gross_rent']):,}/mo" if pd.notna(row.get('median_gross_rent')) else "N/A"
        taxes = f"${int(row['median_re_taxes']):,}/yr" if pd.notna(row.get('median_re_taxes'))     else "N/A"
        print(f"      Pop: {pop}  |  Home value: {hv}  |  Rent: {rent}  |  Property tax: {taxes}")

        # State taxes
        sit = row.get('state_income_tax_rate')
        ss  = row.get('ss_taxed', 'unknown')
        sit_str = f"{sit:.2f}%" if pd.notna(sit) else "N/A"
        ss_str  = {"no": "SS exempt", "partial": "SS partial", "yes": "SS taxed", "unknown": ""}.get(ss, "")
        print(f"      State income tax: {sit_str}  |  {ss_str}")

        # Walkability
        p8   = f"{int(row['practical_800m'])}"  if pd.notna(row.get('practical_800m'))  else "N/A"
        p16  = f"{int(row['practical_1600m'])}" if pd.notna(row.get('practical_1600m')) else "N/A"
        l8   = f"{int(row['lifestyle_800m'])}"  if pd.notna(row.get('lifestyle_800m'))  else "N/A"
        l16  = f"{int(row['lifestyle_1600m'])}" if pd.notna(row.get('lifestyle_1600m')) else "N/A"
        radii = "½mi / 1mi" if imperial else "800m / 1600m"
        print(f"      Practical ({radii}): {p8} / {p16}  |  Lifestyle ({radii}): {l8} / {l16}")

        # Climate
        snow_prism  = row.get("prism_snow_in")
        snow_dm     = row.get("snowfall_in_approx")
        wi_prism    = row.get("prism_winter_f")
        wi_dm       = row.get("winter_temp_f")
        precip      = row.get("annual_precip_mm")

        # Show best snow source with label (PRISM > Daymet)
        if pd.notna(snow_prism):
            snow_str = f"{_fmt_snow(snow_prism, su)} (PRISM)"
        elif pd.notna(snow_dm):
            snow_str = f"{_fmt_snow(snow_dm, su)} (Daymet)"
        else:
            snow_str = None

        if pd.notna(wi_prism):
            wi_str = f"{_fmt_temp(wi_prism, tu)} (PRISM)"
        elif pd.notna(wi_dm):
            wi_str = f"{_fmt_temp(wi_dm, tu)} (Daymet)"
        else:
            wi_str = None

        # PRISM = daily mean (tmean); Daymet = daily max (tmax)
        prism_src  = pd.notna(snow_prism)
        wi_label   = "Winter mean" if prism_src else "Winter high"
        su_label   = "Summer mean" if prism_src else "Summer high"
        su_src_tag = "PRISM" if prism_src else "Daymet"

        climate_parts = []
        if wi_str:
            climate_parts.append(f"{wi_label}: {wi_str}")
        if pd.notna(row.get("summer_temp_f")):
            climate_parts.append(f"{su_label}: {_fmt_temp(row['summer_temp_f'], tu)} ({su_src_tag})")
        if snow_str:
            climate_parts.append(f"Snowfall: {snow_str}")
        if pd.notna(precip):
            if imperial:
                climate_parts.append(f"Precip: {precip / 25.4:.1f} in/yr")
            else:
                climate_parts.append(f"Precip: {precip:.0f} mm/yr")
        if climate_parts:
            print(f"      {' | '.join(climate_parts)}")

        # ERA5 warming trends (daily mean temps)
        s_trend = row.get("summer_trend_f_dec")
        w_trend = row.get("winter_trend_f_dec")
        s_old   = row.get("summer_f_1980s")
        s_now   = row.get("summer_f_recent")
        w_old   = row.get("winter_f_1980s")
        w_now   = row.get("winter_f_recent")
        if pd.notna(s_trend) and pd.notna(w_trend):
            print(f"      ERA5 trends (daily mean) — "
                  f"Summer: {_fmt_temp(s_old, tu)} → {_fmt_temp(s_now, tu)} ({_fmt_trend(s_trend, tu)})  |  "
                  f"Winter: {_fmt_temp(w_old, tu)} → {_fmt_temp(w_now, tu)} ({_fmt_trend(w_trend, tu)})")

        # Amenity checklist (from osm_detail)
        CHECKLIST = [
            ("has_grocery",     "Grocery"),
            ("has_pharmacy",    "Pharmacy"),
            ("has_medical",     "Medical"),
            ("has_bank",        "Bank"),
            ("has_post_office", "Post office"),
            ("has_library",     "Library"),
            ("has_restaurant",  "Restaurant"),
            ("has_cafe",        "Cafe"),
            ("has_bar",         "Bar"),
            ("has_shopping",    "Shopping"),
            ("has_park",        "Park"),
            ("has_arts",        "Arts"),
            ("has_transit",     "Transit"),
        ]
        detail_items = []
        for col, label in CHECKLIST:
            val = row.get(col)
            if pd.notna(val):
                mark = "+" if val else "-"
                detail_items.append(f"{mark}{label}")
        if detail_items:
            print(f"      Amenities (1mi): {' | '.join(detail_items)}")

        # Trails
        trail_mi  = row.get("trail_miles_10mi")
        foot_mi   = row.get("footway_miles_1mi")
        trail_parts = []
        if pd.notna(trail_mi):
            trail_parts.append(f"Trails (10mi): {trail_mi:.1f} mi")
        if pd.notna(foot_mi):
            trail_parts.append(f"Footways (1mi): {foot_mi:.1f} mi")
        if trail_parts:
            print(f"      {' | '.join(trail_parts)}")

        # Facilities
        hosp_d  = f"{row['hospital_distance_miles']:.1f} mi" if pd.notna(row.get('hospital_distance_miles')) else "N/A"
        hosp_n  = f"{int(row['hospitals_within_30mi'])}"     if pd.notna(row.get('hospitals_within_30mi'))    else "N/A"
        coll_d  = f"{row['college_distance_miles']:.1f} mi"  if pd.notna(row.get('college_distance_miles'))   else "N/A"
        coll_n  = f"{int(row['colleges_within_30mi'])}"      if pd.notna(row.get('colleges_within_30mi'))     else "N/A"
        lib_d   = f"{row['library_distance_miles']:.1f} mi"  if pd.notna(row.get('library_distance_miles'))   else "N/A"
        lib_n   = f"{int(row['libraries_within_10mi'])}"     if pd.notna(row.get('libraries_within_10mi'))    else "N/A"
        print(f"      Hospital: {hosp_d} ({hosp_n} within 30mi)  |  "
              f"College: {coll_d} ({coll_n} within 30mi)  |  "
              f"Library: {lib_d} ({lib_n} within 10mi)")

        # Per-criterion scores
        if score_cols:
            parts = []
            for c in score_cols:
                v = row.get(c)
                if pd.notna(v):
                    label = c.replace("score_", "")
                    parts.append(f"{label}: {v:.2f}")
            if parts:
                print(f"      Scores: {', '.join(parts)}")

        print()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run():
    print("\n" + "="*72)
    print("  PLACE-PICKER")
    print("="*72)
    # Resolve region names → state names, then merge with explicit STATES list
    region_states = region_to_states(getattr(config, "REGION", []))
    explicit_states = getattr(config, "STATES", [])
    # Union, preserving order: region states first, then any extra explicit states
    all_states: list[str] = list(region_states)
    for s in explicit_states:
        if s not in all_states:
            all_states.append(s)
    # Default: continental US (excludes Alaska and Hawaii)
    if not all_states:
        all_states = CONUS

    print(f"\n  Population range : {config.POPULATION['min']:,} – {config.POPULATION['max']:,}")
    region_cfg = getattr(config, "REGION", [])
    if region_cfg or explicit_states:
        if region_cfg:
            print(f"  Region filter    : {', '.join(region_cfg)}")
        if explicit_states:
            print(f"  + States         : {', '.join(explicit_states)}")
        print(f"  → {len(all_states)} states in scope")
    else:
        print(f"  States           : continental US (48 states + DC)")
    metro_max = getattr(config, "METRO_MAX", None)
    metro_label = f"≤ {metro_max:,}" if metro_max else "no filter"
    print(f"  Metro max        : {metro_label}")
    print(f"  Candidates       : {config.CANDIDATES}")
    print(f"  Final results    : {config.RESULTS}")
    print()

    # ------------------------------------------------------------------
    # 1. Load Census universe
    # ------------------------------------------------------------------
    df = census.fetch()
    df = state_tax.enrich(df)
    print(f"\n[search] Universe: {len(df):,} Census places loaded")

    # ------------------------------------------------------------------
    # 2. Hard filters
    # ------------------------------------------------------------------
    before = len(df)

    # Population
    df = df[
        df["population"].between(
            config.POPULATION["min"],
            config.POPULATION["max"]
        )
    ]
    print(f"[search] After population filter ({config.POPULATION['min']:,}–"
          f"{config.POPULATION['max']:,}): {len(df):,} places "
          f"(dropped {before - len(df):,})")

    # State / region filter
    if all_states:
        before = len(df)
        df = df[df["state_name"].isin(all_states)]
        print(f"[search] After state/region filter: {len(df):,} places "
              f"(dropped {before - len(df):,})")

    # Home value ceiling
    if config.HOME_VALUE_MAX is not None:
        before = len(df)
        df = df[
            df["median_home_value"].isna() |
            (df["median_home_value"] <= config.HOME_VALUE_MAX)
        ]
        print(f"[search] After home value filter (≤ ${config.HOME_VALUE_MAX:,}): "
              f"{len(df):,} places (dropped {before - len(df):,})")

    # Median rent ceiling
    if config.MEDIAN_RENT_MAX is not None:
        before = len(df)
        df = df[
            df["median_gross_rent"].isna() |
            (df["median_gross_rent"] <= config.MEDIAN_RENT_MAX)
        ]
        print(f"[search] After rent filter (≤ ${config.MEDIAN_RENT_MAX:,}/mo): "
              f"{len(df):,} places (dropped {before - len(df):,})")

    if df.empty:
        print("\n[search] No places match your filters. Try loosening the constraints in config.py.")
        sys.exit(1)

    # Metro area filter
    metro_max = getattr(config, "METRO_MAX", None)
    if metro_max is not None and "metro_pop" in df.columns:
        before = len(df)
        df = df[df["metro_pop"].isna() | (df["metro_pop"] <= metro_max)]
        dropped = before - len(df)
        if dropped:
            print(f"[search] Dropped {dropped:,} places in metros > {metro_max:,} — {len(df):,} remaining")

    # Drop places with extremely low density — these tend to be remote/rural
    # with high pct_no_vehicle for the wrong reasons (no cars AND no amenities).
    # Threshold: at least 100 people per sq km.
    if "pop_density" in df.columns:
        before = len(df)
        df = df[df["pop_density"].isna() | (df["pop_density"] >= 100)]
        dropped = before - len(df)
        if dropped:
            print(f"[search] Dropped {dropped:,} very low-density places (< 100/km²) — {len(df):,} remaining")

    # ------------------------------------------------------------------
    # 3. Rough rank — take top CANDIDATES
    # ------------------------------------------------------------------
    rough = _rough_rank(df)
    candidates = rough.head(config.CANDIDATES).copy()
    print(f"\n[search] Top {len(candidates)} candidates selected for enrichment:")
    for _, row in candidates.head(5).iterrows():
        ptype = f" ({row['place_type']})" if row.get("place_type") else ""
        print(f"  {row['place_name']}{ptype}, {row['state_name']} "
              f"(pop {int(row['population']):,})")
    if len(candidates) > 5:
        print(f"  ... and {len(candidates) - 5} more")

    # ------------------------------------------------------------------
    # 4. Enrich with coastal proximity (one-time shapefile download)
    # ------------------------------------------------------------------
    print()
    candidates = coastal.enrich(candidates)

    coast_max = getattr(config, "COAST_MAX_MILES", None)
    if coast_max:
        before = len(candidates)
        candidates = candidates[
            candidates["coast_distance_miles"].isna() |
            (candidates["coast_distance_miles"] <= coast_max)
        ]
        dropped = before - len(candidates)
        if dropped:
            print(f"[search] Dropped {dropped:,} places > {coast_max} miles from coast "
                  f"— {len(candidates):,} remaining")

    # ------------------------------------------------------------------
    # 4b. Enrich with facility proximity (hospitals, colleges, libraries)
    # ------------------------------------------------------------------
    print()
    candidates = facilities.enrich(candidates)

    # ------------------------------------------------------------------
    # 5. Enrich with ERA5 climate trends (one-time grid download)
    # ------------------------------------------------------------------
    print()
    candidates = era5.enrich(candidates)

    # ------------------------------------------------------------------
    # 5b. Enrich with PRISM terrain-adjusted climate normals
    # ------------------------------------------------------------------
    print()
    candidates = prism.enrich(candidates)

    # ------------------------------------------------------------------
    # 6+7. Enrich with OSM and Daymet in parallel
    # ------------------------------------------------------------------
    print()
    stop_event = threading.Event()
    executor   = concurrent.futures.ThreadPoolExecutor(max_workers=2)
    osm_future    = executor.submit(osm.enrich,    candidates, stop_event)
    daymet_future = executor.submit(daymet.enrich, candidates, stop_event)
    try:
        while not (osm_future.done() and daymet_future.done()):
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("\n[search] Ctrl+C received — signalling threads to stop...")
        stop_event.set()
        osm_future.result()
        daymet_future.result()
        print("[search] Progress saved. Exiting.")
        executor.shutdown(wait=False)
        sys.exit(0)

    executor.shutdown(wait=False)
    osm_result    = osm_future.result()
    daymet_result = daymet_future.result()

    # Merge OSM and Daymet back onto candidates
    daymet_cols = ["geoid", "winter_temp_f", "summer_temp_f",
                   "annual_precip_mm", "snowfall_swe_mm", "snowfall_in_approx"]
    candidates = osm_result.merge(
        daymet_result[[c for c in daymet_cols if c in daymet_result.columns]],
        on="geoid", how="left",
    )

    # Climate priority: PRISM (terrain-adjusted 4km) > ERA5 (28km) > Daymet (fallback)
    candidates["snow_era5_in"] = candidates["snow_mm_recent"] * 10 / 25.4
    candidates["snow_best"] = (
        candidates["prism_snow_in"]
        .fillna(candidates["snow_era5_in"])
        .fillna(candidates["snowfall_in_approx"])
    )
    candidates["winter_temp_best"] = (
        candidates["prism_winter_f"]
        .fillna(candidates["winter_f_recent"])
        .fillna(candidates["winter_temp_f"])
    )
    candidates["summer_temp_f"] = (
        candidates["prism_summer_f"]
        .fillna(candidates["summer_f_recent"])
        .fillna(candidates["summer_temp_f"])
    )

    # ------------------------------------------------------------------
    # 6. Climate hard cutoffs (applied before scoring)
    # ------------------------------------------------------------------
    snow_min = getattr(config, "SNOW_MIN_IN", None)
    if snow_min:
        before = len(candidates)
        candidates = candidates[
            candidates["snow_best"].isna() |
            (candidates["snow_best"] >= snow_min)
        ]
        dropped = before - len(candidates)
        if dropped:
            print(f"[search] Dropped {dropped} places with < {snow_min}\" avg snowfall "
                  f"— {len(candidates)} remaining")

    snow_max = getattr(config, "SNOW_MAX_IN", None)
    if snow_max:
        before = len(candidates)
        candidates = candidates[
            candidates["snow_best"].isna() |
            (candidates["snow_best"] <= snow_max)
        ]
        dropped = before - len(candidates)
        if dropped:
            print(f"[search] Dropped {dropped} places with > {snow_max}\" avg snowfall "
                  f"— {len(candidates)} remaining")

    summer_max = getattr(config, "SUMMER_MAX_F", None)
    if summer_max:
        before = len(candidates)
        candidates = candidates[
            candidates["summer_temp_f"].isna() |
            (candidates["summer_temp_f"] <= summer_max)
        ]
        dropped = before - len(candidates)
        if dropped:
            print(f"[search] Dropped {dropped} places with summer highs > {summer_max}°F "
                  f"— {len(candidates)} remaining")

    winter_min = getattr(config, "WINTER_MIN_F", None)
    if winter_min:
        before = len(candidates)
        candidates = candidates[
            candidates["winter_temp_best"].isna() |
            (candidates["winter_temp_best"] >= winter_min)
        ]
        dropped = before - len(candidates)
        if dropped:
            print(f"[search] Dropped {dropped} places with winter highs < {winter_min}°F "
                  f"— {len(candidates)} remaining")

    summer_trend_max = getattr(config, "SUMMER_TREND_MAX", None)
    if summer_trend_max:
        before = len(candidates)
        candidates = candidates[
            candidates["summer_trend_f_dec"].isna() |
            (candidates["summer_trend_f_dec"] <= summer_trend_max)
        ]
        dropped = before - len(candidates)
        if dropped:
            print(f"[search] Dropped {dropped} places warming faster than "
                  f"{summer_trend_max}°F/decade in summer — {len(candidates)} remaining")

    winter_trend_max = getattr(config, "WINTER_TREND_MAX", None)
    if winter_trend_max:
        before = len(candidates)
        candidates = candidates[
            candidates["winter_trend_f_dec"].isna() |
            (candidates["winter_trend_f_dec"] <= winter_trend_max)
        ]
        dropped = before - len(candidates)
        if dropped:
            print(f"[search] Dropped {dropped} places warming faster than "
                  f"{winter_trend_max}°F/decade in winter — {len(candidates)} remaining")

    # ------------------------------------------------------------------
    # 7. Final scoring
    # ------------------------------------------------------------------
    print("\n[search] Scoring candidates...")
    ranked = score.rank(candidates, config.WEIGHTS, config.CLIMATE)

    # ------------------------------------------------------------------
    # 8. Apply walkability minimums
    # ------------------------------------------------------------------
    walk_min_800m  = getattr(config, "WALK_MIN_800M",  None) or 0
    walk_min_1600m = getattr(config, "WALK_MIN_1600M", None) or 0
    if walk_min_800m > 0 or walk_min_1600m > 0:
        before = len(ranked)
        if walk_min_800m > 0:
            ranked = ranked[ranked["practical_800m"].fillna(0) >= walk_min_800m]
        if walk_min_1600m > 0:
            ranked = ranked[ranked["practical_1600m"].fillna(0) >= walk_min_1600m]
        dropped = before - len(ranked)
        if dropped:
            print(f"[search] Dropped {dropped} places below practical walk minimums "
                  f"(½mi≥{walk_min_800m}, 1mi≥{walk_min_1600m})")

    if ranked.empty:
        print("\n[search] No places met the walkability minimums. "
              "Try lowering WALK_MIN_800M / WALK_MIN_1600M in config.py.")
        return

    # ------------------------------------------------------------------
    # 9. Deduplicate (same geoid can appear via multiple Census place records)
    # ------------------------------------------------------------------
    ranked = ranked.drop_duplicates(subset="geoid", keep="first")

    # ------------------------------------------------------------------
    # 10. OSM amenity detail + trails for top results
    # ------------------------------------------------------------------
    top_n = ranked.head(config.RESULTS).copy()
    top_n = osm_detail.enrich(top_n)
    top_n = osm_trails.enrich(top_n)

    # ------------------------------------------------------------------
    # 11. Display results
    # ------------------------------------------------------------------
    _display_results(top_n, config.RESULTS)

    # Save to CSV — rename columns to spell out data source
    CSV_RENAME = {
        # ERA5 reanalysis (28km grid, daily mean temps)
        "summer_f_1980s":    "era5_summer_mean_f_1980s",
        "summer_f_recent":   "era5_summer_mean_f_recent",
        "summer_trend_f_dec":"era5_summer_trend_f_per_decade",
        "winter_f_1980s":    "era5_winter_mean_f_1980s",
        "winter_f_recent":   "era5_winter_mean_f_recent",
        "winter_trend_f_dec":"era5_winter_trend_f_per_decade",
        "snow_mm_1980s":     "era5_snow_swe_mm_1980s",
        "snow_mm_recent":    "era5_snow_swe_mm_recent",
        "snow_trend_dec":    "era5_snow_trend_mm_per_decade",
        "snow_era5_in":      "era5_snow_depth_in",
        # PRISM 30-yr normals (4km grid, daily mean temps)
        "prism_snow_in":     "prism_snow_depth_in",
        "prism_summer_f":    "prism_summer_mean_f",
        "prism_winter_f":    "prism_winter_mean_f",
        # Daymet (1km grid, daily max temps)
        "winter_temp_f":     "daymet_winter_high_f",
        "annual_precip_mm":  "daymet_annual_precip_mm",
        "snowfall_swe_mm":   "daymet_snowfall_swe_mm",
        "snowfall_in_approx":"daymet_snowfall_depth_in",
        # Blended best-available columns
        "snow_best":         "snow_best_in_prism_era5_daymet",
        "winter_temp_best":  "winter_temp_best_f",
        "summer_temp_f":     "summer_temp_best_f",
        # OSM trails (ways, not nodes)
        "trail_miles_10mi":  "osm_trail_miles_within_10mi",
        "footway_miles_1mi": "osm_footway_miles_within_1mi",
    }
    out_path = "data/processed/results.csv"
    top_n.rename(columns=CSV_RENAME).to_csv(out_path, index=False)
    print(f"  Results saved to {out_path}\n")


if __name__ == "__main__":
    run()
