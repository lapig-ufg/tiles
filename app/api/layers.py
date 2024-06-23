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

@router.get("/s2_harmonized/{period}/{year}/{x}/{y}/{z}")
async def get_s2_harmonized(
    request: Request,
    period: Period,
    year: int,
    x: int,
    y: int,
    z: int,
    visparam="tvi-red",
    month: int = 0,
    
):

    CAPABILITIES['collections']
    metadata = list(filter(lambda x: x['name'] == 's2_harmonized',CAPABILITIES['collections']))[0]
    
    if not year in metadata['year']:
        raise HTTPException(404,f'Invalid year, please try valid year {metadata["year"]}')
    if not period in metadata['period']:
        raise HTTPException(404,f'Invalid period, please try valid period {metadata["period"]}')
    if not visparam in metadata['visparam']:
        raise HTTPException(404,f'Invalid visparam, please try valid visparam {metadata["visparam"]}')

    PERIODS = {
        "WET": {"name": "WET", "dtStart": f"{year}-01-01", "dtEnd": f"{year}-04-30"},
        "DRY": {"name": "DRY", "dtStart": f"{year}-06-01", "dtEnd": f"{year}-10-30"},
        #TODO fazer volta o primeo e ultimo dia do mes
        "MONTH": {"name": "MONTH", "dtStart": f"{year}-{month:02}-01","dtEnd": f"{year}-{month:02}-28"}
    }
    
    if not (z > 9 and z < 19):
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

    _geohash, bbox = tile2goehashBBOX(x, y, z)
    path_cache = f's2_harmonized_{period_select["name"]}_{year}_{visparam}/{_geohash}'

    file_cache = f"{path_cache}/{z}/{x}_{y}.png"
    logger.info(file_cache)
    binary_data = request.app.state.valkey.get(file_cache)
    
    if binary_data:
        logger.info(f"Using cached file: {file_cache}")
        return StreamingResponse(io.BytesIO(binary_data), media_type="image/png")
        

    Path(f"{path_cache}/{z}").mkdir(parents=True, exist_ok=True)

    urlGEElayer = getCacheUrl(request.app.state.valkey.get(path_cache))

    if (urlGEElayer is None
        or (datetime.now() - urlGEElayer['date']).total_seconds() / 3600
        > settings.LIFESPAN_URL
        
    ):
        try:
            logger.debug(f"New url: {path_cache}")
            geom = ee.Geometry.BBox(bbox["w"], bbox["s"], bbox["e"], bbox["n"])

            s2 = ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
            s2 = s2.filterDate(
                period_select["dtStart"], period_select["dtEnd"]
            ).filterBounds(geom)
            s2 = s2.sort("CLOUDY_PIXEL_PERCENTAGE", False)
            s2 = s2.select(*_visparam["select"])
            best_image = s2.mosaic()
            
            logger.debug(f'{_visparam["select"]} | {_visparam["visparam"]}')
            
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
    x: int,
    y: int,
    z: int,
    request: Request,
    period: Period = Query(default=Period.MONTH),
    year: int = Query(default=datetime.now().year),
    visparam: str = Query(default="landsat-tvi-red"),
    month: int = Query(default=datetime.now().month),
):
    metadata = next(filter(lambda x: x['name'] == 'landsat', CAPABILITIES['collections']), None)
    if not metadata:
        raise HTTPException(404, 'Landsat capabilities not found.')

    if year not in metadata['year']:
        raise HTTPException(404, f'Invalid year, please try a valid year: {metadata["year"]}')
    if period not in metadata['period']:
        raise HTTPException(404, f'Invalid period, please try a valid period: {metadata["period"]}')
    if visparam not in metadata['visparam']:
        raise HTTPException(404, f'Invalid visparam, please try a valid visparam: {metadata["visparam"]}')

    PERIODS = {
        "WET": {"name": "WET", "dtStart": f"{year}-01-01", "dtEnd": f"{year}-04-30"},
        "DRY": {"name": "DRY", "dtStart": f"{year}-06-01", "dtEnd": f"{year}-10-30"},
    }

    if period == Period.MONTH:
        if month < 1 or month > 12:
            raise HTTPException(400, 'Invalid month, please provide a month between 1 and 12')
        _, last_day = calendar.monthrange(year, month)
        PERIODS["MONTH"] = {
            "name": "MONTH",
            "dtStart": f"{year}-{month:02}-01",
            "dtEnd": f"{year}-{month:02}-{last_day:02}"
        }

    if not (9 < z < 19):
        logger.debug('Invalid zoom level')
        return FileResponse('data/maxminzoom.png', media_type="image/png")

    period_select = PERIODS.get(period.value)
    if not period_select:
        raise HTTPException(
            status_code=404,
            detail=f"Period not found, please try a valid period: {list(PERIODS.keys())}"
        )

    _visparam = VISPARAMS.get(visparam)
    if not _visparam:
        raise HTTPException(
            status_code=404,
            detail=f"Visparam not found, please try a valid visparam: {list(VISPARAMS.keys())}"
        )

    _geohash, bbox = tile2goehashBBOX(x, y, z)
    path_cache = f'landsat_{period_select["name"]}_{year}_{visparam}/{_geohash}'

    file_cache = f"{path_cache}/{z}/{x}_{y}.png"
    logger.info(file_cache)
    binary_data = request.app.state.valkey.get(file_cache)

    if binary_data:
        logger.info(f"Using cached file: {file_cache}")
        return StreamingResponse(io.BytesIO(binary_data), media_type="image/png")

    Path(f"{path_cache}/{z}").mkdir(parents=True, exist_ok=True)

    urlGEElayer_json = request.app.state.valkey.get(path_cache)

    if urlGEElayer_json is None or (datetime.now() - datetime.fromisoformat(json.loads(urlGEElayer_json)['date'])).total_seconds() / 3600 > settings.LIFESPAN_URL:
        try:
            logger.debug(f"New url: {path_cache}")
            geom = ee.Geometry.BBox(bbox["w"], bbox["s"], bbox["e"], bbox["n"])

            l4 = ee.ImageCollection('LANDSAT/LT04/C02/T1_L2').select( ['SR_B4', 'SR_B5', 'SR_B7'], ['NIR', 'SWIR', 'RED'])
            l5 = ee.ImageCollection('LANDSAT/LT05/C02/T1_L2').select( ['SR_B4', 'SR_B5', 'SR_B7'], ['NIR', 'SWIR', 'RED'])
            l7 = ee.ImageCollection('LANDSAT/LE07/C02/T1_L2').select(['SR_B4', 'SR_B5', 'SR_B7'], ['NIR', 'SWIR', 'RED'])
            l8 = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2').select(['SR_B5', 'SR_B6', 'SR_B4'], ['NIR', 'SWIR', 'RED'])
            l9 = ee.ImageCollection('LANDSAT/LC09/C02/T1_L2').select(['SR_B5', 'SR_B6', 'SR_B4'], ['NIR', 'SWIR', 'RED'])

            landsat = l4.merge(l5).merge(l7).merge(l8).merge(l9)
            landsat = landsat.filterDate(
                period_select["dtStart"], period_select["dtEnd"]
            ).filterBounds(geom)
            landsat = landsat.sort("CLOUD_COVER")
            best_image = landsat.first()

            logger.debug(f'{_visparam["visparam"]}')

            map_id = ee.data.getMapId({"image": best_image, **_visparam["visparam"]})
            layer_url = map_id["tile_fetcher"].url_format
            request.app.state.valkey.set(path_cache, json.dumps({'url': layer_url, 'date': datetime.now().isoformat()}))
        except Exception as e:
            logger.exception(f'{file_cache} | {e}')
            return FileResponse('data/blank.png', media_type="image/png")
    else:
        logger.debug("Using existing layer URL")
        layer_url = json.loads(urlGEElayer_json)['url']

    try:
        binary_data = await fetch_image_from_api(layer_url.format(x=x, y=y, z=z))
        request.app.state.valkey.set(file_cache, binary_data)
    except HTTPException as exc:
        logger.exception(f'{file_cache} {exc}')
        raise HTTPException(500, exc)
    logger.info(f"Success not cached {file_cache}")
    return StreamingResponse(io.BytesIO(binary_data), media_type="image/png")