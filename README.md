# place-picker

A data-driven tool for finding your ideal retirement location. Combines Census demographics, OpenStreetMap walkability, PRISM climate normals, ERA5 warming trends, and Daymet weather data to rank small-to-mid-sized US towns against your personal priorities.

## What it does

- Filters ~32,000 Census places by population, region, metro size, and climate cutoffs
- Scores each place on walkability, summer/winter temperature, snowfall, affordability, and climate stability (warming trends)
- Displays top results with amenity checklists, trail mileage, hospital/college/library proximity, and state tax info

## Quickstart

```bash
pip install -r requirements.txt
# Edit config.py to set your preferences
python search.py
```

First run downloads PRISM rasters (~72 MB, one-time) and queries the Overpass API for walkability data (~2,400 places at 10s each — runs in background, progress saved).

## Configuration

All preferences live in `config.py`:

- **POPULATION** — min/max town size
- **REGION / STATES** — geographic scope
- **SNOW_MIN_IN / SUMMER_MAX_F** — hard climate cutoffs
- **SUMMER_TREND_MAX / WINTER_TREND_MAX** — warming trend cutoffs (°F/decade)
- **WEIGHTS** — relative importance of walkability, climate, affordability, trails
- **UNITS** — `"imperial"` (°F, inches) or `"metric"` (°C, mm)

## Data Sources & Attributions

### US Census Bureau
- **American Community Survey (ACS) 5-Year Estimates** — population, housing, income, demographics
  - Source: https://www.census.gov/data/developers/data-sets/acs-5year.html
  - License: Public domain (US government work)
- **Census Gazetteer Files** — place centroids and land area
  - Source: https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html
  - License: Public domain
- **CBSA Delineation Files** — metro area county membership
  - Source: https://www.census.gov/programs-surveys/metro-micro/geographies/reference-files.html
  - License: Public domain

### OpenStreetMap
- **Overpass API** — walkable amenity counts, per-amenity presence checklist, trail and footway mileage, town center anchor points
  - Source: https://overpass-api.de / https://overpass.kumi.systems
  - License: © OpenStreetMap contributors, ODbL — https://www.openstreetmap.org/copyright

### PRISM Climate Group, Oregon State University
- **30-Year Climate Normals (1991–2020)** — terrain-adjusted monthly precipitation and temperature at 4km resolution; used to derive snowfall estimates and seasonal temperatures
  - Source: https://prism.oregonstate.edu
  - Citation: PRISM Climate Group, Oregon State University, https://prism.oregonstate.edu, data created 4 Feb 2014
  - License: Free for non-commercial use with attribution

### Copernicus Climate Change Service (C3S) / ECMWF
- **ERA5 Reanalysis** — historical climate data (1980–2024) for computing summer/winter warming trends (°F/decade)
  - Source: https://cds.climate.copernicus.eu
  - Citation: Hersbach, H., et al. (2020). The ERA5 global reanalysis. *Quarterly Journal of the Royal Meteorological Society*, 146(730), 1999–2049. https://doi.org/10.1002/qj.3803
  - License: Copernicus License (free for any purpose with attribution). Contains modified Copernicus Climate Change Service information 2024. Neither the European Commission nor ECMWF is responsible for any use that may be made of the Copernicus information or data it contains.

### Daymet
- **Single-Pixel API** — daily surface weather at 1km resolution; used as fallback for temperature and precipitation
  - Source: https://daymet.ornl.gov
  - Citation: Thornton, M.M., et al. (2022). Daymet: Daily Surface Weather Data on a 1-km Grid for North America, Version 4 R1. ORNL DAAC, Oak Ridge, Tennessee, USA. https://doi.org/10.3334/ORNLDAAC/2129
  - License: CC0 1.0 (public domain)

### Centers for Medicare & Medicaid Services (CMS)
- **Hospital General Information** — Medicare-certified hospital locations
  - Source: https://data.cms.gov/provider-data/dataset/xubh-q36u
  - License: Public domain

### National Center for Education Statistics (NCES)
- **IPEDS Institutional Directory** — college and university locations
  - Source: https://nces.ed.gov/ipeds/datacenter/DataFiles.aspx
  - License: Public domain

### Institute of Museum and Library Services (IMLS)
- **Public Libraries Survey** — public library outlet locations
  - Source: https://www.imls.gov/research-evaluation/data-collection/public-libraries-survey
  - License: Public domain

## Pipeline architecture

```
Census API → filter & score candidates
OSM Overpass → walkability counts + amenity detail + trails
PRISM rasters → snowfall, summer/winter mean temps (primary)
ERA5 NetCDF → warming trends (primary) + climate fallback
Daymet API → temperature & precip fallback
CMS / NCES / IMLS → hospital, college, library proximity
```

All data is cached locally (parquet files) with dated refresh cycles so repeat runs are fast.

## License

MIT — see LICENSE file.
