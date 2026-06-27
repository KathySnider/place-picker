"""
pipeline/state_tax.py
---------------------
State-level tax data as a simple lookup table.
No API calls — this is static data updated manually as laws change.

Two tables:
    STATE_INCOME_TAX  — top marginal rate (or flat rate) for state income tax
    SS_TAXED_BY_STATE — whether the state taxes Social Security benefits

Last verified: June 2026. Update when state laws change.

Social Security taxation notes:
    Most states do NOT tax SS. Those that do often have income thresholds
    above which SS becomes taxable. "partial" = taxable above certain income.
    "yes" = fully taxable (same as federal rules). "no" = fully exempt.
"""

import pandas as pd


# Top marginal state income tax rate (%) as of 2026.
# 0.0 = no state income tax.
STATE_INCOME_TAX: dict[str, float] = {
    "Alabama":              5.0,
    "Alaska":               0.0,   # no income tax
    "Arizona":              2.5,
    "Arkansas":             4.4,
    "California":          13.3,
    "Colorado":             4.4,
    "Connecticut":          6.99,
    "Delaware":             6.6,
    "District of Columbia": 10.75,
    "Florida":              0.0,   # no income tax
    "Georgia":              5.49,
    "Hawaii":              11.0,
    "Idaho":                5.8,
    "Illinois":             4.95,  # flat
    "Indiana":              3.05,  # flat
    "Iowa":                 3.8,
    "Kansas":               5.7,
    "Kentucky":             4.0,   # flat
    "Louisiana":            3.0,
    "Maine":                7.15,
    "Maryland":             5.75,
    "Massachusetts":        9.0,   # 5% standard + 4% surtax on income > $1M
    "Michigan":             4.25,  # flat
    "Minnesota":            9.85,
    "Mississippi":          4.7,
    "Missouri":             4.8,
    "Montana":              5.9,
    "Nebraska":             5.84,
    "Nevada":               0.0,   # no income tax
    "New Hampshire":        0.0,   # no tax on wages (investment income only, phasing out)
    "New Jersey":          10.75,
    "New Mexico":           5.9,
    "New York":             10.9,
    "North Carolina":       4.5,
    "North Dakota":         2.5,
    "Ohio":                 3.99,
    "Oklahoma":             4.75,
    "Oregon":               9.9,
    "Pennsylvania":         3.07,  # flat
    "Rhode Island":         5.99,
    "South Carolina":       6.2,
    "South Dakota":         0.0,   # no income tax
    "Tennessee":            0.0,   # no income tax (Hall tax repealed 2021)
    "Texas":                0.0,   # no income tax
    "Utah":                 4.65,  # flat
    "Vermont":              8.75,
    "Virginia":             5.75,
    "Washington":           0.0,   # no income tax (capital gains tax separate)
    "West Virginia":        5.12,
    "Wisconsin":            7.65,
    "Wyoming":              0.0,   # no income tax
}

# Does the state tax Social Security benefits?
# "no"      — fully exempt (most states)
# "partial" — exempt above certain income thresholds (check state rules)
# "yes"     — taxed same as federal (rare)
SS_TAXED_BY_STATE: dict[str, str] = {
    "Alabama":              "no",
    "Alaska":               "no",
    "Arizona":              "no",
    "Arkansas":             "no",
    "California":           "no",
    "Colorado":             "partial",  # exempt up to $20k-$24k depending on age
    "Connecticut":          "partial",  # exempt if income < $75k single / $100k joint
    "Delaware":             "no",
    "District of Columbia": "no",
    "Florida":              "no",
    "Georgia":              "no",
    "Hawaii":               "no",
    "Idaho":                "no",
    "Illinois":             "no",
    "Indiana":              "no",
    "Iowa":                 "no",       # fully exempted SS as of 2023
    "Kansas":               "no",       # exempted SS as of 2024
    "Kentucky":             "no",
    "Louisiana":            "no",
    "Maine":                "no",
    "Maryland":             "no",
    "Massachusetts":        "no",
    "Michigan":             "no",
    "Minnesota":            "partial",  # exempt below certain income
    "Mississippi":          "no",
    "Missouri":             "no",       # exempted SS as of 2024
    "Montana":              "partial",  # taxed above $25k single / $32k joint
    "Nebraska":             "no",       # fully exempted SS as of 2025
    "Nevada":               "no",
    "New Hampshire":        "no",
    "New Jersey":           "no",
    "New Mexico":           "partial",  # exempt below $100k single / $150k joint
    "New York":             "no",
    "North Carolina":       "no",
    "North Dakota":         "no",
    "Ohio":                 "no",
    "Oklahoma":             "no",
    "Oregon":               "no",
    "Pennsylvania":         "no",
    "Rhode Island":         "partial",  # exempt below certain income
    "South Carolina":       "no",
    "South Dakota":         "no",
    "Tennessee":            "no",
    "Texas":                "no",
    "Utah":                 "partial",  # credit-based exemption
    "Vermont":              "partial",  # exempt below $45k single / $60k joint
    "Virginia":             "no",
    "Washington":           "no",
    "West Virginia":        "partial",  # phasing out — 35% taxable in 2025, 0% by 2026
    "Wisconsin":            "no",
    "Wyoming":              "no",
}


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add state_income_tax_rate and ss_taxed columns to the DataFrame.
    Joins on state_name — places with unrecognized state names get NaN/unknown.
    """
    tax_df = pd.DataFrame([
        {
            "state_name":           state,
            "state_income_tax_rate": rate,
            "ss_taxed":             SS_TAXED_BY_STATE.get(state, "unknown"),
        }
        for state, rate in STATE_INCOME_TAX.items()
    ])
    return df.merge(tax_df, on="state_name", how="left")
