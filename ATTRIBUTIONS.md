# Data Sources & Attributions

## US Census Bureau
- **American Community Survey (ACS) 5-Year Estimates**
  - Population, housing, income, demographics, education, transportation
  - Source: https://www.census.gov/data/developers/data-sets/acs-5year.html
  - License: Public domain (US government work)

- **Census Gazetteer Files**
  - Place and county centroids (lat/lng) and land area
  - Source: https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html
  - License: Public domain (US government work)

- **Core Based Statistical Area (CBSA) Delineation Files**
  - Metro/micro area county membership for metro population calculation
  - Source: https://www.census.gov/programs-surveys/metro-micro/geographies/reference-files.html
  - License: Public domain (US government work)

## OpenStreetMap
- **Overpass API**
  - Walkable amenity counts (practical and lifestyle) near town centers
  - Per-amenity presence checklist for top results (grocery, pharmacy, etc.)
  - Trail and footway mileage within 10 miles / 1 mile of town center
  - Place node coordinates used as town center anchor points
  - Source: https://overpass-api.de / https://overpass.kumi.systems
  - License: OpenStreetMap data © OpenStreetMap contributors, ODbL
  - https://www.openstreetmap.org/copyright

## Daymet
- **Daymet Single-Pixel API**
  - Daily climate data (temperature, precipitation, snow water equivalent)
  - 1km resolution, recent years (5-year average)
  - Source: https://daymet.ornl.gov
  - Citation: Thornton, M.M., et al. (2022). Daymet: Daily Surface Weather Data
    on a 1-km Grid for North America, Version 4 R1. ORNL DAAC, Oak Ridge, Tennessee, USA.
    https://doi.org/10.3334/ORNLDAAC/2129
  - License: Creative Commons CC0 1.0 (public domain dedication)

## Copernicus Climate Change Service (C3S) / ECMWF
- **ERA5 Reanalysis**
  - Historical climate data (1940–present) for warming trend analysis
  - Used to compute summer/winter temperature trends (°F/decade)
  - Source: https://cds.climate.copernicus.eu
  - Citation: Hersbach, H., et al. (2020). The ERA5 global reanalysis.
    Quarterly Journal of the Royal Meteorological Society, 146(730), 1999-2049.
    https://doi.org/10.1002/qj.3803
  - License: Copernicus License (free for any purpose with attribution)
  - Contains modified Copernicus Climate Change Service information 2024.
    Neither the European Commission nor ECMWF is responsible for any use
    that may be made of the Copernicus information or data it contains.

## PRISM Climate Group, Oregon State University
- **PRISM 30-Year Climate Normals (1991-2020)**
  - Terrain-adjusted monthly precipitation and temperature at 4km resolution
  - Used to derive annual snowfall estimates and seasonal temperatures
  - Accounts for lake-effect snow, elevation gradients, and coastal influences
  - Source: https://prism.oregonstate.edu
  - Citation: PRISM Climate Group, Oregon State University,
    https://prism.oregonstate.edu, data created 4 Feb 2014.
  - License: Free for non-commercial use with attribution

## Centers for Medicare & Medicaid Services (CMS)
- **Hospital General Information**
  - Medicare-certified hospitals (Acute Care and Critical Access)
  - Used for hospital proximity calculation
  - Source: https://data.cms.gov/provider-data/dataset/xubh-q36u
  - License: Public domain (US government work)

## National Center for Education Statistics (NCES)
- **IPEDS Institutional Directory (HD file)**
  - Directory of all US colleges and universities with coordinates
  - Filtered to active 2-year and 4-year degree-granting institutions
  - Source: https://nces.ed.gov/ipeds/datacenter/DataFiles.aspx
  - License: Public domain (US government work)

## Institute of Museum and Library Services (IMLS)
- **Public Libraries Survey (PLS)**
  - Public library outlet locations (central libraries and branches)
  - Source: https://www.imls.gov/research-evaluation/data-collection/public-libraries-survey
  - License: Public domain (US government work)
