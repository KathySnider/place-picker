"""
api/routes/search.py
--------------------
POST /api/search — runs the pipeline and streams progress via SSE.
"""

import asyncio
import json
import math
import types
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.models import SearchRequest

router = APIRouter()

_executor = ThreadPoolExecutor(max_workers=4)


def _run(fn, *args):
    """
    Schedule a blocking pipeline function in the thread pool and return
    (future, heartbeat_generator). Iterate the generator in the async pipeline
    to keep the HTTP/2 connection alive; await the future to get the result.

    Usage:
        fut, hbs = _run(some_fn, arg1, arg2)
        async for hb in hbs:
            yield hb
        result = await fut
    """
    loop = asyncio.get_event_loop()
    fut = loop.run_in_executor(_executor, fn, *args)

    async def _heartbeats():
        while True:
            done, _ = await asyncio.wait({fut}, timeout=15)
            if done:
                return
            yield ": heartbeat\n\n"

    return fut, _heartbeats()


def _nan_to_none(val):
    """Convert NaN/inf to None for JSON serialization."""
    if val is None:
        return None
    try:
        if math.isnan(val) or math.isinf(val):
            return None
    except (TypeError, ValueError):
        pass
    return val


def _row_to_place(row: pd.Series) -> dict:
    def g(col):
        v = row.get(col)
        if pd.isna(v) if not isinstance(v, str) else False:
            return None
        return v

    def gf(col):
        return _nan_to_none(row.get(col))

    # Determine climate sources
    snow_prism = gf("prism_snow_in")
    snow_src   = "PRISM" if snow_prism is not None else ("ERA5" if gf("snow_era5_in") is not None else "Daymet")
    wi_prism   = gf("prism_winter_f")
    wi_src     = "PRISM" if wi_prism is not None else "Daymet"
    su_prism   = gf("prism_summer_f")
    su_src     = "PRISM" if su_prism is not None else "Daymet"

    return {
        "geoid":              g("geoid"),
        "placeName":          g("place_name"),
        "placeType":          g("place_type") or "",
        "stateName":          g("state_name"),
        "compositeScore":     gf("composite_score"),
        "population":         int(row["population"]) if pd.notna(row.get("population")) else None,
        "medianHomeValue":    gf("median_home_value"),
        "medianGrossRent":    gf("median_gross_rent"),
        "medianReTaxes":      gf("median_re_taxes"),
        "stateIncomeTaxRate": gf("state_income_tax_rate"),
        "ssTaxed":            g("ss_taxed"),

        "practical800m":  gf("practical_800m"),
        "practical1600m": gf("practical_1600m"),
        "lifestyle800m":  gf("lifestyle_800m"),
        "lifestyle1600m": gf("lifestyle_1600m"),

        "snowBestIn":      gf("snow_best"),
        "snowSource":      snow_src,
        "summerTempF":     gf("summer_temp_f"),
        "summerSource":    su_src,
        "winterTempBestF": gf("winter_temp_best"),
        "winterSource":    wi_src,
        "annualPrecipMm":  gf("annual_precip_mm"),

        "summerTrendFDec": gf("summer_trend_f_dec"),
        "winterTrendFDec": gf("winter_trend_f_dec"),
        "summerF1980s":    gf("summer_f_1980s"),
        "summerFRecent":   gf("summer_f_recent"),
        "winterF1980s":    gf("winter_f_1980s"),
        "winterFRecent":   gf("winter_f_recent"),

        "trailMiles10mi":  gf("trail_miles_10mi"),
        "footwayMiles1mi": gf("footway_miles_1mi"),

        "amenities": {
            "grocery":    g("has_grocery"),
            "pharmacy":   g("has_pharmacy"),
            "medical":    g("has_medical"),
            "bank":       g("has_bank"),
            "postOffice": g("has_post_office"),
            "library":    g("has_library"),
            "restaurant": g("has_restaurant"),
            "cafe":       g("has_cafe"),
            "bar":        g("has_bar"),
            "shopping":   g("has_shopping"),
            "park":       g("has_park"),
            "arts":       g("has_arts"),
            "transit":    g("has_transit"),
        },

        "hospitalDistanceMiles": gf("hospital_distance_miles"),
        "hospitalsWithin30mi":   int(row["hospitals_within_30mi"]) if pd.notna(row.get("hospitals_within_30mi")) else None,
        "collegeDistanceMiles":  gf("college_distance_miles"),
        "collegesWithin30mi":    int(row["colleges_within_30mi"])  if pd.notna(row.get("colleges_within_30mi"))  else None,
        "libraryDistanceMiles":  gf("library_distance_miles"),
        "librariesWithin10mi":   int(row["libraries_within_10mi"]) if pd.notna(row.get("libraries_within_10mi")) else None,

        "scores": {
            k.replace("score_", ""): _nan_to_none(v)
            for k, v in row.items()
            if k.startswith("score_")
        },
    }


def _build_config(req: SearchRequest):
    """Build a config-like namespace from the API request."""
    cfg = types.SimpleNamespace()
    cfg.POPULATION      = {"min": req.popMin, "max": req.popMax}
    cfg.REGION          = req.regions
    cfg.STATES          = req.states
    cfg.METRO_MAX       = req.metroMax
    cfg.HOME_VALUE_MAX  = req.homeValueMax
    cfg.MEDIAN_RENT_MAX = req.rentMax
    cfg.WALK_MIN_800M   = req.walkMin800m
    cfg.WALK_MIN_1600M  = req.walkMin1600m
    cfg.SNOW_MIN_IN     = req.snowMin
    cfg.SNOW_MAX_IN     = req.snowMax
    cfg.SUMMER_MAX_F    = req.summerMaxF
    cfg.WINTER_MIN_F    = req.winterMinF
    cfg.SUMMER_TREND_MAX = req.summerTrendMax
    cfg.WINTER_TREND_MAX = None
    cfg.CANDIDATES      = 2400
    cfg.RESULTS         = req.resultCount
    cfg.UNITS           = req.units
    cfg.WEIGHTS = {
        "practical_800m":  req.weights.practical800m,
        "practical_1600m": req.weights.practical1600m,
        "lifestyle_800m":  req.weights.lifestyle800m,
        "lifestyle_1600m": req.weights.lifestyle1600m,
        "winter_temp":     0.0,
        "summer_temp":     req.weights.summerTemp,
        "snowfall_swe":    req.weights.snowfall,
        "home_value":      req.weights.homeValue,
        "median_rent":     0.0,
        "summer_trend":    req.weights.summerTrend,
        "winter_trend":    req.weights.winterTrend,
    }
    cfg.CLIMATE = {
        "prefer_cold_winters": req.preferColdWinters,
        "prefer_cool_summers": req.preferCoolSummers,
        "prefer_snowy":        req.preferSnowy,
    }
    return cfg


async def _run_pipeline(req: SearchRequest) -> AsyncGenerator[str, None]:
    """Run the search pipeline, yielding SSE events as steps complete."""

    def event(step: str, message: str, **kwargs) -> str:
        data = {"type": "progress", "step": step, "message": message, **kwargs}
        return f"data: {json.dumps(data)}\n\n"

    yield event("start", "Starting search pipeline...")
    await asyncio.sleep(0)

    # Import pipeline modules lazily (they're heavy)
    from pipeline import census, osm, daymet, era5, prism, score, state_tax, facilities, osm_detail, osm_trails
    import search as search_module

    cfg = _build_config(req)

    # Step 1: Census — SQL-filtered query (only rows we need)
    yield event("census", "Loading Census data...")
    await asyncio.sleep(0)
    candidates = census.load(
        population=cfg.POPULATION,
        regions=cfg.REGION,
        states=cfg.STATES,
        metro_max=cfg.METRO_MAX,
        home_value_max=cfg.HOME_VALUE_MAX,
        rent_max=cfg.MEDIAN_RENT_MAX,
    )
    candidates = state_tax.enrich(candidates)

    if candidates.empty:
        yield event("error", "No places matched your filters — try loosening population or region.")
        return

    yield event("census", f"Found {len(candidates):,} candidate places")
    await asyncio.sleep(0)

    # Step 2: OSM walkability on full filtered set (cache only — fast DB read)
    # Uncached places get null scores and rank lower; cached places float to the top
    yield event("osm", "Loading walkability data...")
    fut, hbs = _run(lambda df: osm.enrich(df, cache_only=True), candidates)
    async for hb in hbs:
        yield hb
    candidates = await fut
    cached_walk = candidates["practical_800m"].notna().sum()
    yield event("osm", f"Walkability data ready ({cached_walk:,} of {len(candidates):,} places have cached data)")

    # Step 3: Rough score & trim — now uses real walkability where available
    yield event("filter", "Applying filters and rough scoring...")
    await asyncio.sleep(0)
    rough = search_module._rough_score(candidates, cfg)
    candidates = rough.head(cfg.CANDIDATES).copy()
    yield event("filter", f"{len(candidates):,} candidates selected for enrichment")
    await asyncio.sleep(0)

    # Step 4: Daymet climate (cache only)
    yield event("daymet", "Loading climate data...")
    fut, hbs = _run(lambda df: daymet.enrich(df, cache_only=True), candidates)
    async for hb in hbs:
        yield hb
    candidates = await fut
    yield event("daymet", "Climate data ready")

    # Step 5: PRISM (cache only — rasterio/GDAL not available in container)
    yield event("prism", "Applying PRISM climate normals...")
    fut, hbs = _run(lambda df: prism.enrich(df, cache_only=True), candidates)
    async for hb in hbs:
        yield hb
    candidates = await fut
    yield event("prism", "PRISM data ready")

    # Step 6: ERA5 (cache only — NetCDF download not suitable for web context)
    yield event("era5", "Applying ERA5 warming trends...")
    fut, hbs = _run(lambda df: era5.enrich(df, cache_only=True), candidates)
    async for hb in hbs:
        yield hb
    candidates = await fut
    yield event("era5", "ERA5 data ready")

    # Step 7: State tax (fast — static lookup, no I/O)
    yield event("tax", "Loading state tax data...")
    candidates = state_tax.enrich(candidates)

    # Step 8: Facilities
    yield event("facilities", "Loading hospital, college, library data...")
    fut, hbs = _run(facilities.enrich, candidates)
    async for hb in hbs:
        yield hb
    candidates = await fut

    # Step 9: Climate priority chain + filters
    yield event("score", "Applying climate filters and scoring...")
    await asyncio.sleep(0)
    candidates = search_module._apply_climate_chain(candidates, cfg)
    ranked = score.rank(candidates, cfg.WEIGHTS, cfg.CLIMATE)
    ranked = ranked.drop_duplicates(subset="geoid", keep="first")

    # Walkability filter
    if cfg.WALK_MIN_800M > 0:
        ranked = ranked[ranked["practical_800m"] >= cfg.WALK_MIN_800M]
    if cfg.WALK_MIN_1600M > 0:
        ranked = ranked[ranked["practical_1600m"] >= cfg.WALK_MIN_1600M]

    if ranked.empty:
        yield event("error", "No places met all filters. Try relaxing your criteria.")
        return

    yield event("score", f"{len(ranked):,} places passed all filters")
    await asyncio.sleep(0)

    # Step 10: OSM detail + trails for top N
    top_n = ranked.head(cfg.RESULTS).copy()
    yield event("detail", f"Fetching amenity detail for top {len(top_n)} places...")
    fut, hbs = _run(osm_detail.enrich, top_n)
    async for hb in hbs:
        yield hb
    top_n = await fut
    fut, hbs = _run(osm_trails.enrich, top_n)
    async for hb in hbs:
        yield hb
    top_n = await fut
    yield event("detail", "Amenity and trail data ready")

    # Serialize results
    places = [_row_to_place(row) for _, row in top_n.iterrows()]

    yield f"data: {json.dumps({'type': 'complete', 'step': 'done', 'message': f'Found {len(places)} places', 'results': places})}\n\n"


@router.post("/search")
async def search(req: SearchRequest):
    return StreamingResponse(
        _run_pipeline(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
