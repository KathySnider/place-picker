"""
regions.py
----------
Named US regions → state lists.

Used by config.py REGION setting. You can combine regions and individual
states — the union is used for filtering.

Regions follow common US Census / journalistic convention:

    Northeast   : New England + Mid-Atlantic
    New England : ME VT NH MA RI CT
    Mid-Atlantic: NY NJ PA DE MD DC

    South       : Southeast + South Central
    Southeast   : VA WV NC SC GA FL TN AL MS KY
    South Central: TX OK AR LA

    Midwest     : Great Lakes + Plains
    Great Lakes : OH MI IN IL WI MN
    Plains      : ND SD NE KS IA MO

    West        : Mountain + Pacific
    Mountain    : MT ID WY CO UT NV AZ NM
    Pacific     : WA OR CA AK HI

Broad names (Northeast, South, Midwest, West) include all sub-regions.
"""

REGIONS: dict[str, list[str]] = {
    # --- Sub-regions ---
    "New England": [
        "Maine", "Vermont", "New Hampshire",
        "Massachusetts", "Rhode Island", "Connecticut",
    ],
    "Mid-Atlantic": [
        "New York", "New Jersey", "Pennsylvania",
        "Delaware", "Maryland", "District of Columbia",
    ],
    "Great Lakes": [
        "Ohio", "Michigan", "Indiana", "Illinois", "Wisconsin", "Minnesota",
    ],
    "Plains": [
        "North Dakota", "South Dakota", "Nebraska",
        "Kansas", "Iowa", "Missouri",
    ],
    "Southeast": [
        "Virginia", "West Virginia", "North Carolina", "South Carolina",
        "Georgia", "Florida", "Tennessee", "Alabama", "Mississippi", "Kentucky",
    ],
    "South Central": [
        "Texas", "Oklahoma", "Arkansas", "Louisiana",
    ],
    "Mountain": [
        "Montana", "Idaho", "Wyoming", "Colorado",
        "Utah", "Nevada", "Arizona", "New Mexico",
    ],
    "Pacific Coast": [
        "Washington", "Oregon", "California",
    ],
    "Pacific": [
        "Washington", "Oregon", "California", "Alaska", "Hawaii",
    ],

    # --- Broad regions (unions of sub-regions) ---
    "Northeast": [
        "Maine", "Vermont", "New Hampshire",
        "Massachusetts", "Rhode Island", "Connecticut",
        "New York", "New Jersey", "Pennsylvania",
        "Delaware", "Maryland", "District of Columbia",
    ],
    "Midwest": [
        "Ohio", "Michigan", "Indiana", "Illinois", "Wisconsin", "Minnesota",
        "North Dakota", "South Dakota", "Nebraska",
        "Kansas", "Iowa", "Missouri",
    ],
    "South": [
        "Virginia", "West Virginia", "North Carolina", "South Carolina",
        "Georgia", "Florida", "Tennessee", "Alabama", "Mississippi", "Kentucky",
        "Texas", "Oklahoma", "Arkansas", "Louisiana",
    ],
    "West": [
        "Montana", "Idaho", "Wyoming", "Colorado",
        "Utah", "Nevada", "Arizona", "New Mexico",
        "Washington", "Oregon", "California",
    ],
}


def region_to_states(regions: list[str]) -> list[str]:
    """
    Expand a list of region names to a deduplicated list of state names.
    Unknown names are treated as state names and passed through as-is.
    """
    states: list[str] = []
    for name in regions:
        if name in REGIONS:
            states.extend(REGIONS[name])
        else:
            # Assume it's a state name (lets users mix regions and states here)
            states.append(name)
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for s in states:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


CONUS: list[str] = [
    # Northeast
    "Maine", "Vermont", "New Hampshire", "Massachusetts", "Rhode Island",
    "Connecticut", "New York", "New Jersey", "Pennsylvania",
    "Delaware", "Maryland", "District of Columbia",
    # Midwest
    "Ohio", "Michigan", "Indiana", "Illinois", "Wisconsin", "Minnesota",
    "North Dakota", "South Dakota", "Nebraska", "Kansas", "Iowa", "Missouri",
    # South
    "Virginia", "West Virginia", "North Carolina", "South Carolina",
    "Georgia", "Florida", "Tennessee", "Alabama", "Mississippi", "Kentucky",
    "Texas", "Oklahoma", "Arkansas", "Louisiana",
    # West (minus AK and HI)
    "Montana", "Idaho", "Wyoming", "Colorado", "Utah", "Nevada",
    "Arizona", "New Mexico", "Washington", "Oregon", "California",
]


def list_regions() -> None:
    """Print all available region names and their states."""
    for name, states in REGIONS.items():
        print(f"  {name:<16} : {', '.join(states)}")
