"""
pipeline/score.py
-----------------
Scores and ranks enriched candidates based on config.py weights.

Scoring approach:
    Each criterion is normalized to [0, 1] across the candidate pool,
    then multiplied by its weight. The composite score is the weighted
    average of all non-NaN criteria.

Direction (higher/lower is better) is defined here per criterion,
not in config.py — the user only sets weights.
"""

import pandas as pd
import numpy as np


# For each criterion: True = higher raw value is better, False = lower is better
DIRECTIONS = {
    "practical_800m":    True,
    "practical_1600m":   True,
    "lifestyle_800m":    True,
    "lifestyle_1600m":   True,
    "winter_temp_best":  None,   # direction comes from CLIMATE config
    "summer_temp_f":     None,
    "snow_best":         None,
    "home_value":        False,
    "median_rent":       False,
    "summer_trend_f_dec": False,  # lower warming rate is better
    "winter_trend_f_dec": False,  # lower warming rate is better
}

# Map config WEIGHTS keys → DataFrame column names
COLUMN_MAP = {
    "practical_800m":  "practical_800m",
    "practical_1600m": "practical_1600m",
    "lifestyle_800m":  "lifestyle_800m",
    "lifestyle_1600m": "lifestyle_1600m",
    "winter_temp":     "winter_temp_best",
    "summer_temp":     "summer_temp_f",
    "snowfall_swe":    "snow_best",
    "home_value":      "median_home_value",
    "median_rent":     "median_gross_rent",
    "summer_trend":    "summer_trend_f_dec",
    "winter_trend":    "winter_trend_f_dec",
}


def _normalize(series: pd.Series, higher_is_better: bool) -> pd.Series:
    """Min-max normalize to [0, 1]. NaN stays NaN."""
    lo, hi = series.min(), series.max()
    if hi == lo:
        return pd.Series(0.5, index=series.index)
    raw = (series - lo) / (hi - lo)
    return raw if higher_is_better else (1 - raw)


def rank(df: pd.DataFrame, weights: dict, climate_config: dict) -> pd.DataFrame:
    """
    Score and rank the enriched candidates DataFrame.

    Parameters
    ----------
    df           : enriched DataFrame (Census + OSM + Daymet)
    weights      : WEIGHTS dict from config.py
    climate_config: CLIMATE dict from config.py

    Returns
    -------
    DataFrame sorted by composite_score descending, with per-criterion
    score columns added (score_<criterion>).
    """
    result = df.copy()
    weighted_sum  = pd.Series(0.0, index=df.index)
    weight_totals = pd.Series(0.0, index=df.index)

    for cfg_key, weight in weights.items():
        if weight == 0.0:
            continue

        col = COLUMN_MAP.get(cfg_key)
        if col is None or col not in df.columns:
            continue

        series = pd.to_numeric(df[col], errors="coerce")
        if series.isna().all():
            print(f"[score] Skipping {cfg_key} — all values are NaN")
            continue

        # Determine direction
        direction = DIRECTIONS.get(cfg_key)
        if direction is None:
            if cfg_key == "winter_temp":
                direction = not climate_config.get("prefer_cold_winters", False)
            elif cfg_key == "summer_temp":
                direction = not climate_config.get("prefer_cool_summers", True)
            elif cfg_key == "snowfall_swe":
                direction = climate_config.get("prefer_snowy", False)
            else:
                direction = True

        normalized = _normalize(series, higher_is_better=direction)
        score_col  = f"score_{cfg_key}"
        result[score_col] = normalized.round(4)

        # Only add to weighted sum where we have data
        has_data = normalized.notna()
        weighted_sum[has_data]  += normalized[has_data] * weight
        weight_totals[has_data] += weight

    # Composite score: weighted average over available criteria
    result["composite_score"] = np.where(
        weight_totals > 0,
        (weighted_sum / weight_totals).round(4),
        np.nan,
    )

    result = result.sort_values("composite_score", ascending=False)
    return result
