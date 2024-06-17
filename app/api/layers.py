from enum import Enum
from fastapi import APIRouter, HTTPException, Response, Depends
from app.database import get_db
from app.models import Layer
from app.models import FilterRequest, RegionRequest, LayerName
from app.config import LAYERS, logger, settings
from requests import get
import ee
from datetime import datetime
import os
from app.repository import LayerRepository
from sqlalchemy.orm import Session
import geohash
from app.tile import is_within_boundsbox, is_within_brazil, latlon_to_tile

from pathlib import Path
from fastapi.responses import StreamingResponse
import io

router = APIRouter()

@router.get("/get-layer-url/{layer}/{x}/{y}/{z}")
def get_layer_url(layer: LayerName, x: int, y: int, z: int, db: Session = Depends(get_db)):
    try:
        
        if not is_within_brazil(x,y,z):
            logger.warning(f'{x}, {y}, {z} not brasil')
            with open('cache/blank.png', 'rb') as f:
                return StreamingResponse(io.BytesIO(f.read()), media_type="image/png")
        
        file_cache = f'cache/{layer}/{z}/{x}_{y}.png'
        
        if os.path.isfile(file_cache):
            logger.info(f"Using cached file: {file_cache}")
            with open(file_cache, 'rb') as f:
                return StreamingResponse(io.BytesIO(f.read()), media_type="image/png")

        Path(f"cache/{layer}/{z}/").mkdir(parents=True, exist_ok=True)
        
        urlGEElayer = LayerRepository.find_by_layer(db, layer)
        
        LIFESPAN_URL = 1
        
        if not urlGEElayer or (datetime.now() - urlGEElayer.date).total_seconds() / 3600 > LIFESPAN_URL:
            logger.info('Fetching new layer URL')
            _layer = LAYERS.get(layer)
            asset_id = _layer.get('assets', 'Error')
            if asset_id == 'Error':
                raise HTTPException(status_code=500, detail="Failed to get layer URL")
            
            collection = ee.FeatureCollection(asset_id)
            image = ee.Image().paint(collection, 1, 2)
            map_id = ee.data.getMapId({
                'image': image, 
                'palette': _layer.get('palette', ['#cecece']), 
                'opacity': _layer.get('opacity', 0.5)
            })
            layer_url = map_id['tile_fetcher'].url_format
            
            urlGEElayer = LayerRepository.save(db, Layer(
                layer=layer,
                url=layer_url,
                date=datetime.now()
            ))
        else:
            logger.info('Using existing layer URL')
            layer_url = urlGEElayer.url
        
        request = get(layer_url.format(x=x, y=y, z=z), stream=True)
        
        if request.status_code == 200:
            # Salva a imagem no cache
            logger.inf('creating image from layer url')
            with open(file_cache, 'wb') as f:
                for chunk in request.iter_content(chunk_size=8192):
                    f.write(chunk)

            # Reabre o cache para fazer o streaming
            with open(file_cache, 'rb') as f:
                return StreamingResponse(io.BytesIO(f.read()), media_type="image/png")
        else:
            raise HTTPException(status_code=request.status_code, detail="Failed to fetch image from remote server")
    
    except Exception as e:
        logger.exception(f"Failed to get layer URL: {e}")
        raise HTTPException(status_code=500, detail="Failed to get layer URL")
    
    
class Period(str, Enum):
    WET = 'WET'
    DRY = 'DRY'
       
    
@router.get('/sentinel/{period}/{year}/{x}/{y}/{z}')
def get_sentinel(
    period:Period ,
    year: int, 
    x: int,
    y: int, 
    z: int,
    latitude:float =  -16.6019787,
    longitude:float = -49.2649445,
    db: Session = Depends(get_db)):
    
    PERIODS_BR = {
	'WET':{
		"name": 'WET',
		"dtStart": f'{year}-01-01',
		"dtEnd": f'{year}-04-30'
	},
	'DRY':{
		"name": 'DRY',
		"dtStart": f'{year}-06-01',
		"dtEnd": f'{year}-10-30'
	}
    }
    
    period_select = PERIODS_BR.get(period, 'Error')
    
    
    if not is_within_boundsbox(latitude,longitude ,x, y, z):
        logger.warning(f'{x}, {y}, {z} not brasil')
        with open('cache/blank.png', 'rb') as f:
            return StreamingResponse(io.BytesIO(f.read()), media_type="image/png")
        
    point_hash = geohash.encode(latitude, longitude, precision=9)
    path_cache = f'cache/sentinel/{period_select["name"]}/{point_hash}/'
    
    file_cache = f'{path_cache}/{z}/{x}_{y}.png'
        
    if os.path.isfile(file_cache):
        logger.info(f"Using cached file: {file_cache}")
        with open(file_cache, 'rb') as f:
            return StreamingResponse(io.BytesIO(f.read()), media_type="image/png")

    Path(f"{path_cache}/{z}").mkdir(parents=True, exist_ok=True)

    urlGEElayer = LayerRepository.find_by_layer(db, path_cache)
    LIFESPAN_URL = 1
        
    if not urlGEElayer or (datetime.now() - urlGEElayer.date).total_seconds() / 3600  > LIFESPAN_URL:
        logger.info(f"New url: {path_cache}")
        geom = ee.Geometry.Point([longitude, latitude])
        buffered_geom = geom.buffer(150)
        s2 = ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
        s2 = s2.filterDate(period_select['dtStart'], period_select['dtEnd']).filterBounds(geom)
        s2 = s2.sort('CLOUDY_PIXEL_PERCENTAGE', False)
        s2 = s2.select(['B4', 'B8A', 'B11'], ['RED', 'REDEDGE4', 'SWIR1'])

        # Get the best image, based on the cloud cover.
        best_image = s2.mosaic()
        map_id = ee.data.getMapId({
                'image': best_image, 
                'gamma': '1.1',
                'max': '4300, 5400, 2800',
                'min': '600, 700, 400',
                'bands': ['SWIR1', 'REDEDGE4', 'RED']
                
                
            })
        layer_url = map_id['tile_fetcher'].url_format
        urlGEElayer = LayerRepository.save(db, Layer(
                layer=path_cache,
                url=layer_url,
                date=datetime.now()
            ))
    else:
        logger.info('Using existing layer URL')
        layer_url = urlGEElayer.url
        
    request = get(layer_url.format(x=x, y=y, z=z), stream=True)
        
    if request.status_code == 200:
        # Salva a imagem no cache
        with open(file_cache, 'wb') as f:
            for chunk in request.iter_content(chunk_size=8192):
                f.write(chunk)

        # Reabre o cache para fazer o streaming
        with open(file_cache, 'rb') as f:
            return StreamingResponse(io.BytesIO(f.read()), media_type="image/png")
    else:
        raise HTTPException(status_code=request.status_code, detail="Failed to fetch image from remote server")
    
    
    