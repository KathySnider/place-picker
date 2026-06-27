"""
place-picker configuration
--------------------------
Edit this file to define your search. Then run:  python search.py

All settings are plain Python — no JSON, no CLI flags needed.
"""

# ---------------------------------------------------------------------------
# Display units
# ---------------------------------------------------------------------------
# "imperial" → temperatures in °F, distances in miles, precip/snow in inches
# "metric"   → temperatures in °C, distances in km, precip/snow in mm
UNITS = "imperial"

# ---------------------------------------------------------------------------
# Step 1: Filter the ~32k Census places down to candidates
# ---------------------------------------------------------------------------

POPULATION = {
    "min": 2_000,      # smallest town you'd consider
    "max": 50_000,     # largest city you'd consider
}

# Limit to named regions. Empty list = no region filter.
# Available regions: Northeast, New England, Mid-Atlantic,
#                    Midwest, Great Lakes, Plains,
#                    South, Southeast, South Central,
#                    West, Mountain, Pacific Coast, Pacific 
#                    (includes AK, and HI)
# Run `python -c "from regions import list_regions; list_regions()"` to see all states per region.
REGION = ["Northeast", "New England", "Midwest", "Great Lakes", "West", "Mountain", "Pacific Coast"]
# Example: REGION = ["Midwest", "New England"]

# Add individual states outside the region, or use instead of REGION.
# REGION and STATES are combined — the union of both is used.
# If both are empty, defaults to the continental US (excludes Alaska and Hawaii).
STATES = []
# Example: STATES = ["Michigan", "Wisconsin", "New York", "Vermont", "Minnesota"]

# Exclude places that are part of a large metro area.
# Set to None to include all places regardless of metro size.
# Example: 500_000 excludes suburbs of any metro over 500k people.
METRO_MAX = 250_000

# Optional hard filters on Census data (set to None to skip)
HOME_VALUE_MAX   = None     # e.g. 350_000
MEDIAN_RENT_MAX  = None     # e.g. 1_500

# Minimum walkability — places below these thresholds are excluded from results.
# Set to 0 or None to disable.
WALK_MIN_800M  = 3    # minimum practical amenities within ~½ mile
WALK_MIN_1600M = 5    # minimum practical amenities within ~1 mile

# Climate hard cutoffs — places outside these bounds are eliminated entirely.
# Set to None to disable.
SNOW_MIN_IN   = 36    # minimum average annual snowfall (inches) — below this, eliminated
SNOW_MAX_IN   = None  # maximum average annual snowfall (inches) — above this, eliminated
SUMMER_MAX_F  = 80    # maximum avg summer daily high (°F) — above this, eliminated
WINTER_MIN_F  = None  # minimum avg winter daily high (°F) — below this, eliminated

# ERA5 warming trend cutoffs — places warming faster than these are eliminated.
# Based on 1980-2024 linear trend in °F per decade. Set to None to disable.
# Example: 0.7 eliminates places warming faster than 0.7°F/decade in summer.
SUMMER_TREND_MAX = 0.7   # max summer warming trend (°F/decade) — above this, eliminated
WINTER_TREND_MAX = None  # max winter warming trend (°F/decade)

# Coastal proximity — set to a mile threshold to only show coastal towns.
# Set to None to include all places regardless of distance to coast.
# Example: 20 shows only places within 20 miles of the ocean/Great Lakes.
COAST_MAX_MILES = None   # e.g. 20 for beach towns

# ---------------------------------------------------------------------------
# Display units — affects output only, not cutoffs or scoring
# ---------------------------------------------------------------------------
UNITS_TEMP = "F"    # "F" for Fahrenheit or "C" for Celsius
UNITS_SNOW = "in"   # "in" for inches or "mm" for millimeters

# ---------------------------------------------------------------------------
# Step 2: How many candidates to enrich with OSM + Daymet
# ---------------------------------------------------------------------------
# After the Census filter, we rank by a rough score and take the top N.
# These N places get the expensive per-point API calls (OSM + Daymet).

CANDIDATES = 2400   # how many to enrich
RESULTS    = 25     # how many to show in the final ranked output

# ---------------------------------------------------------------------------
# Step 3: What you care about — weights drive the final ranking
#
# Scale: 0.0 (ignore) → 1.0 (most important)
# Sign convention is handled internally — just set the weight for things
# that matter to you. Direction (higher/lower is better) is set per-criterion
# in pipeline/score.py.
# ---------------------------------------------------------------------------

WEIGHTS = {
    # Practical walkability — groceries, pharmacy, medical, bank, post office, library
    "practical_800m":  1.0,   # within a ~½-mile walk
    "practical_1600m": 0.7,   # within a ~1-mile walk

    # Lifestyle walkability — restaurants, shops, parks, galleries, transit, etc.
    "lifestyle_800m":  0.3,
    "lifestyle_1600m": 0.2,

    # Climate (Daymet 5-year averages at exact coordinates)
    "winter_temp":     0.0,   # avg daily high Dec–Feb (°F)
    "summer_temp":     0.9,   # avg daily high Jun–Aug (°F) — cooler ranks higher
    "snowfall_swe":    0.0,   # pass/fail via SNOW_MIN_IN cutoff — not scored

    # ERA5 warming trends (lower trend = more stable = better)
    "summer_trend":    0.5,   # summer warming rate (°F/decade) — slower ranks higher
    "winter_trend":    0.2,   # winter warming rate (°F/decade) — slower ranks higher

    # Affordability (Census ACS)
    "home_value":      0.0,   # median home value — lower is better
    "median_rent":     0.0,   # median gross rent — lower is better
}

# ---------------------------------------------------------------------------
# Step 4: Climate preferences
# These only matter if you set weights above for winter_temp / summer_temp /
# snowfall_swe. They define the "ideal" end of each range for scoring.
# ---------------------------------------------------------------------------

CLIMATE = {
    # For winter_temp: set prefer_cold=True if you WANT cold winters,
    # False if you want mild ones.
    "prefer_cold_winters": True,

    # For summer_temp: set prefer_cool=True if you want mild summers.
    "prefer_cool_summers": True,

    # For snowfall: set prefer_snowy=True if you want lots of snow.
    "prefer_snowy": True,
}
