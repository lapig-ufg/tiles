import io
import json
import calendar
from datetime import datetime
from enum import Enum
from pathlib import Path

from app.utils.capabilities import CAPABILITIES
from app.utils.cache import getCacheUrl
import ee
import aiohttp
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, FileResponse


from app.config import logger, settings
from app.tile import tile2goehashBBOX
from app.visParam import VISPARAMS
from app.visParam import get_landsat_vis_params
from app.errors import generate_error_image
router = APIRouter()


class Period(str, Enum):
    WET = "WET"
    DRY = "DRY"
    MONTH = "MONTH"


async def fetch_image_from_api(image_url: str):
    """Busca uma imagem de uma API externa."""
    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as response:
            if response.status != 200:
                raise HTTPException(status_code=response.status, detail="Imagem não encontrada na API externa")
            return await response.read()

@router.get("/s2_harmonized/{x}/{y}/{z}")
async def get_s2_harmonized(
    request: Request,
    x: int,
    y: int,
    z: int,
    period: Period = Period.WET,
    year: int = datetime.now().year,
    visparam="tvi-red",
    month: int = int(datetime.now().month),
    
):
    metadata = list(filter(lambda x: x['name'] == 's2_harmonized', CAPABILITIES['collections']))[0]
    
    if not year in metadata['year']:
        raise HTTPException(404,f'Invalid year, please try valid year {metadata["year"]}')
    if not period in metadata['period']:
        raise HTTPException(404,f'Invalid period, please try valid period {metadata["period"]}')
    if not visparam in metadata['visparam']:
        raise HTTPException(404,f'Invalid visparam, please try valid visparam {metadata["visparam"]}')

    PERIODS = {
        "WET": {"name": "WET", "dtStart": f"{year}-01-01", "dtEnd": f"{year}-04-30"},
        "DRY": {"name": "DRY", "dtStart": f"{year}-06-01", "dtEnd": f"{year}-10-30"},
    }

    if period == "MONTH":
        if month < 1 or month > 12:
            raise HTTPException(400, 'Invalid month, please provide a month between 1 and 12')
        _, last_day = calendar.monthrange(year, month)
        PERIODS["MONTH"] = {
            "name": "MONTH",
            "dtStart": f"{year}-{month:02}-01",
            "dtEnd": f"{year}-{month:02}-{last_day:02}"
        }
    logger.info(f'month: {month}')
    if not (9 < z < 19):
        logger.debug('zoom ')
        return FileResponse('data/maxminzoom.png', media_type="image/png")

    period_select = PERIODS.get(period, "Error")
    if period_select == "Error":
        raise HTTPException(
            status_code=404,
            detail=f"period not found, please try valid period {list(PERIODS.keys())}",
        )

    _visparam = VISPARAMS.get(visparam, "Error")
    if _visparam == "Error":
        raise HTTPException(
            status_code=404,
            detail=f"visparam not found, please try valid vis parameter {list(VISPARAMS.keys())}",
        )

    logger.info(f'period {period}, year {year}, month {month}, period_select {period_select}')
    _geohash, bbox = tile2goehashBBOX(x, y, z)
    path_cache = f's2_harmonized_{period_select["name"]}_{year}_{month}_{visparam}/{_geohash}'
    file_cache = f"{path_cache}/{z}/{x}_{y}.png"

    binary_data = request.app.state.valkey.get(file_cache)
    
    if binary_data:
        # logger.info(f"Using cached file: {file_cache}")
        return StreamingResponse(io.BytesIO(binary_data), media_type="image/png")

    urlGEElayer = getCacheUrl(request.app.state.valkey.get(path_cache))

    if (urlGEElayer is None
        or (datetime.now() - urlGEElayer['date']).total_seconds() / 3600
        > settings.LIFESPAN_URL
        
    ):
        try:
            # logger.debug(f"New url: {path_cache}")
            geom = ee.Geometry.BBox(bbox["w"], bbox["s"], bbox["e"], bbox["n"])
            logger.info(f'range date: {period_select["dtStart"]}, {period_select["dtEnd"]}')
            s2 = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
                  .filterDate(period_select["dtStart"], period_select["dtEnd"])
                  .filterBounds(geom)
                  .sort("CLOUDY_PIXEL_PERCENTAGE", False)
                  .select(*_visparam["select"]))
            best_image = s2.mosaic()

            # logger.debug(f'{_visparam["select"]} | {_visparam["visparam"]}')
            
            map_id = ee.data.getMapId({"image": best_image, **_visparam["visparam"]})
            layer_url = map_id["tile_fetcher"].url_format
            request.app.state.valkey.set(path_cache,f'{layer_url}, {datetime.now()}')
        except Exception as e:
            logger.exception(f'{file_cache} | {e}')
            return FileResponse('data/blank.png', media_type="image/png")
            
    else:
        logger.debug("Using existing layer URL")
        layer_url = urlGEElayer['url']

    try:
        binary_data = await fetch_image_from_api(layer_url.format(x=x, y=y,z=z))
        request.app.state.valkey.set(file_cache, binary_data)
    except HTTPException as exc:
        logger.exception(f'{file_cache} {exc}')
        raise HTTPException(500, exc)
    logger.info(f"Success not cached {file_cache}")
    return StreamingResponse(io.BytesIO(binary_data), media_type="image/png")

@router.get("/landsat/{x}/{y}/{z}")
async def get_landsat(
        request: Request,
        x: int,
        y: int,
        z: int,
        period: str = "MONTH",
        year: int = int(datetime.now().year),
        visparam: str = "landsat-tvi-false",
        month: int = int(datetime.now().month),
):
    logger.info(f'period {period}, year {year}, month {month}')
    metadata = next(filter(lambda x: x['name'] == 'landsat', CAPABILITIES['collections']), None)
    if not metadata:
        raise HTTPException(404, 'Landsat capabilities not found.')

    if year not in metadata['year']:
        logger.debug(f'Invalid year, please try a valid year: {metadata["year"]}')
        return FileResponse('data/notfound.png', media_type="image/png")
    if period not in metadata['period']:
        logger.debug(f'Invalid period, please try a valid period: {metadata["period"]}')
        return FileResponse('data/notfound.png', media_type="image/png")
    if visparam not in metadata['visparam']:
        logger.debug(f'Invalid visparam, please try a valid visparam: {metadata["visparam"]}')
        return FileResponse('data/notfound.png', media_type="image/png")

    PERIODS = {
        "WET": {"name": "WET", "dtStart": f"{year}-01-01", "dtEnd": f"{year}-04-30"},
        "DRY": {"name": "DRY", "dtStart": f"{year}-06-01", "dtEnd": f"{year}-10-30"},
    }

    if period == "MONTH":
        if month < 1 or month > 12:
            logger.debug('Invalid month, please provide a month between 1 and 12')
            return FileResponse('data/notfound.png', media_type="image/png")

        _, last_day = calendar.monthrange(year, month)
        PERIODS["MONTH"] = {
            "name": "MONTH",
            "dtStart": f"{year}-{month:02}-01",
            "dtEnd": f"{year}-{month:02}-{last_day:02}"
        }

    if not (9 < z < 19):
        logger.debug('Invalid zoom level')
        return FileResponse('data/maxminzoom.png', media_type="image/png")

    period_select = PERIODS.get(period)
    if not period_select:
        logger.debug(f"Period not found, please try a valid period: {list(PERIODS.keys())}")
        return FileResponse('data/notfound.png', media_type="image/png")

    vis_type = VISPARAMS.get(visparam)
    if not vis_type:
        logger.debug(f"Visparam not found, please try a valid visparam: {list(VISPARAMS.keys())}")
        return FileResponse('data/notfound.png', media_type="image/png")

    _geohash, bbox = tile2goehashBBOX(x, y, z)

    path_cache = f'landsat_{period_select["name"]}_{year}_{month}_{visparam}/{_geohash}'

    file_cache = f"{path_cache}/{z}/{x}_{y}.png"
    logger.info(file_cache)
    binary_data = request.app.state.valkey.get(file_cache)

    if binary_data:
        # logger.info(f"Using cached file: {file_cache}")
        return StreamingResponse(io.BytesIO(binary_data), media_type="image/png")

    urlGEElayer_json = request.app.state.valkey.get(path_cache)

    if urlGEElayer_json is None or (datetime.now() - datetime.fromisoformat(
            json.loads(urlGEElayer_json)['date'])).total_seconds() / 3600 > settings.LIFESPAN_URL:
        try:
            logger.debug(f"New url: {path_cache}")
            geom = ee.Geometry.BBox(bbox["w"], bbox["s"], bbox["e"], bbox["n"])

            period_year = datetime.strptime(period_select["dtStart"], "%Y-%m-%d").year
            if 1984 <= period_year <= 2012:
                collection_name = 'LANDSAT/LT05/C02/T1_L2'  # Landsat 5
            elif 1999 <= period_year <= 2022:
                collection_name = 'LANDSAT/LE07/C02/T1_L2'  # Landsat 7
            elif 2013 <= period_year <= 2022:
                collection_name = 'LANDSAT/LC08/C02/T1_L2'  # Landsat 8
            elif period_year >= 2022:
                collection_name = 'LANDSAT/LC09/C02/T1_L2'  # Landsat 9
            else:
                raise ValueError("No valid Landsat collection (Landsat 5 onwards) for the provided date range")

            vis_params = get_landsat_vis_params(visparam, collection_name)

            if isinstance(vis_params.get('min'), list):
                vis_params['min'] = ','.join(map(str, vis_params['min']))
            if isinstance(vis_params.get('max'), list):
                vis_params['max'] = ','.join(map(str, vis_params['max']))
            if isinstance(vis_params.get('gamma'), list):
                vis_params['gamma'] = ','.join(map(str, vis_params['gamma']))

            def apply_scale_factors(image):
                opticalBands = image.select('SR_B.').multiply(0.0000275).add(-0.2)
                return image.addBands(opticalBands, None, True)

            landsat_collection = ee.ImageCollection(collection_name) \
                .filterDate(period_select["dtStart"], period_select["dtEnd"]) \
                .filterBounds(geom) \
                .map(apply_scale_factors) \
                .select(vis_params['bands'])

            landsat = landsat_collection.sort("CLOUD_COVER", False)
            best_image = landsat.mosaic()

            map_id = ee.data.getMapId({"image": best_image, **vis_params})

            layer_url = map_id["tile_fetcher"].url_format
            request.app.state.valkey.set(path_cache, json.dumps({'url': layer_url, 'date': datetime.now().isoformat()}))

        except Exception as e:
            logger.exception(f'{file_cache} | {e}')
            error_image = generate_error_image(f"Error: {str(e)}")
            return StreamingResponse(error_image, media_type="image/png")
    else:
        layer_url = json.loads(urlGEElayer_json)['url']

    try:
        binary_data = await fetch_image_from_api(layer_url.format(x=x, y=y, z=z))
        request.app.state.valkey.set(file_cache, binary_data)
    except HTTPException as exc:
        logger.exception(f'{file_cache} | {exc}')
        error_image = generate_error_image(f"Error: {str(e)}")
        return StreamingResponse(error_image, media_type="image/png")

    logger.info(f"Success not cached {file_cache}")
    return StreamingResponse(io.BytesIO(binary_data), media_type="image/png")

