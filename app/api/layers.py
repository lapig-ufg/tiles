import io
import os
from datetime import datetime
from enum import Enum
from pathlib import Path

from app.capabilities import CAPABILITIES
import ee
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from requests import get
from sqlalchemy.orm import Session

from app.config import logger, settings
from app.database import get_db
from app.models import Layer
from app.repository import LayerRepository
from app.tile import tile2goehashBBOX
from app.visParam import VISPARAMS

router = APIRouter()


class Period(str, Enum):
    WET = "WET"
    DRY = "DRY"



@router.get("/s2_harmonized/{period}/{year}/{x}/{y}/{z}")
def get_s2_harmonized(
    period: Period,
    year: int,
    x: int,
    y: int,
    z: int,
    visparam="tvi-green",
    month: int = 0,
    db: Session = Depends(get_db),
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
        with open('data/maxminzoom.png', "rb") as f:
            return StreamingResponse(io.BytesIO(f.read()), media_type="image/png")

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
    path_cache = f'/cache/sentinel/{period_select["name"]}_{year}_{visparam}/{_geohash}'

    file_cache = f"{path_cache}/{z}/{x}_{y}.png"

    if os.path.isfile(file_cache):
        logger.info(f"Using cached file: {file_cache}")
        with open(file_cache, "rb") as f:
            return StreamingResponse(io.BytesIO(f.read()), media_type="image/png")

    Path(f"{path_cache}/{z}").mkdir(parents=True, exist_ok=True)

    urlGEElayer = LayerRepository.find_by_layer(db, path_cache)

    if (
        not urlGEElayer
        or (datetime.now() - urlGEElayer.date).total_seconds() / 3600
        > settings.LIFESPAN_URL
        
    ):
        logger.info(f"New url: {path_cache}")
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
        urlGEElayer = LayerRepository.save(
            db, Layer(layer=path_cache, url=layer_url, date=datetime.now())
        )
    else:
        logger.info("Using existing layer URL")
        layer_url = urlGEElayer.url

    request = get(layer_url.format(x=x, y=y, z=z), stream=True)

    if request.status_code == 200:
        # Salva a imagem no cache
        with open(file_cache, "wb") as f:
            for chunk in request.iter_content(chunk_size=8192):
                f.write(chunk)

        # Reabre o cache para fazer o streaming
        with open(file_cache, "rb") as f:
            return StreamingResponse(io.BytesIO(f.read()), media_type="image/png")
    else:
        raise HTTPException(
            status_code=request.status_code,
            detail="Failed to fetch image from remote server",
        )
