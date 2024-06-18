import io
import os
from datetime import datetime
from enum import Enum
from pathlib import Path

from app.utils.capabilities import CAPABILITIES
from app.utils.cache import getCacheUrl
import ee
import aiohttp
import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse, FileResponse


from app.config import logger, settings
from app.models import Layer
from app.repository import LayerRepository
from app.tile import tile2goehashBBOX
from app.visParam import VISPARAMS


router = APIRouter()


class Period(str, Enum):
    WET = "WET"
    DRY = "DRY"


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
    visparam="tvi-green",
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

    binary_data = request.app.state.valkey.get(file_cache)
    
    if binary_data:
        logger.debug(f"Using cached file: {file_cache}")
        return StreamingResponse(io.BytesIO(binary_data), media_type="image/png")
        

    Path(f"{path_cache}/{z}").mkdir(parents=True, exist_ok=True)

    urlGEElayer = getCacheUrl(request.app.state.valkey.get(path_cache))

    if (urlGEElayer is None
        or (datetime.now() - urlGEElayer['date']).total_seconds() / 3600
        > settings.LIFESPAN_URL
        
    ):
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
        
    else:
        logger.debug("Using existing layer URL")
        layer_url = urlGEElayer['url']

    try:
        binary_data = await fetch_image_from_api(layer_url.format(x=x, y=y,z=z))
        request.app.state.valkey.set(file_cache, binary_data)
    except HTTPException as exc:
        logger.exception(exc)
        raise HTTPException(status_code=500, detail="Erro ao se comunicar com a API externa") 

    return StreamingResponse(io.BytesIO(binary_data), media_type="image/png")
                
