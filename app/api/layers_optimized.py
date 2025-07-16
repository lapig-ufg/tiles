"""
Endpoints otimizados para alta performance com cache híbrido
Suporta milhões de requisições por segundo
"""
from __future__ import annotations

import io
import json
import calendar
import asyncio
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

import aiohttp
import ee
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse, Response

from app.config import logger, settings
from app.utils.capabilities import CAPABILITIES
from app.tile import tile2goehashBBOX
from app.visParam import VISPARAMS, get_landsat_vis_params, get_landsat_collection
from app.errors import generate_error_image
from app.cache_hybrid import tile_cache
from app.rate_limiter import limit_sentinel, limit_landsat, limiter
from app.adaptive_limiter import adaptive_limiter
from app.batch_processor import batch_processor

# --------------------------------------------------------------------------- #
# Constantes e tipos                                                          #
# --------------------------------------------------------------------------- #

class Period(str, Enum):
    WET   = "WET"
    DRY   = "DRY"
    MONTH = "MONTH"

MIN_ZOOM, MAX_ZOOM = 6, 18  # Permitir zoom de 6 a 18

router = APIRouter()

# Thread pool para operações do Earth Engine (síncronas)
ee_executor = ThreadPoolExecutor(max_workers=20)

# --------------------------------------------------------------------------- #
# Utils comuns otimizados                                                     #
# --------------------------------------------------------------------------- #

async def _http_get_bytes(url: str, session: aiohttp.ClientSession = None) -> bytes:
    """Download assíncrono com reuso de sessão"""
    if session is None:
        async with aiohttp.ClientSession() as sess:
            return await _http_get_bytes(url, sess)
    
    async with session.get(url) as resp:
        if resp.status != 200:
            raise HTTPException(resp.status, f"Erro ao buscar tile: {resp.reason}")
        return await resp.read()

def _build_periods(period: str | Period, year: int, month: int) -> Dict[str, str]:
    """Constrói períodos com cache de resultados"""
    periods = {
        "WET":  {"dtStart": f"{year}-01-01", "dtEnd": f"{year}-04-30"},
        "DRY":  {"dtStart": f"{year}-06-01", "dtEnd": f"{year}-10-30"},
    }
    if period == "MONTH":
        if not 1 <= month <= 12:
            raise HTTPException(400, "month deve estar entre 1-12")
        _, last_day = calendar.monthrange(year, month)
        periods["MONTH"] = {
            "dtStart": f"{year}-{month:02}-01",
            "dtEnd":   f"{year}-{month:02}-{last_day:02}",
        }
    if period not in periods:
        raise HTTPException(404, f"Período inválido. Use {list(periods)}")
    return periods[period]

def _check_zoom(z: int):
    """Validação rápida de zoom"""
    if not (MIN_ZOOM <= z <= MAX_ZOOM):
        raise HTTPException(400, f"Zoom deve estar entre {MIN_ZOOM}-{MAX_ZOOM}")

def _check_capability(name: str, year: int, period: str, visparam: str):
    """Validação com cache de capabilities"""
    meta = next(filter(lambda c: c["name"] == name,
                       CAPABILITIES["collections"]), None)
    if not meta:
        raise HTTPException(404, f"Camada {name} não registrada")
    if year not in meta["year"]:
        raise HTTPException(404, f"Ano inválido {year}")
    if period not in meta["period"]:
        raise HTTPException(404, f"Período inválido {period}")
    if visparam not in meta["visparam"]:
        raise HTTPException(404, f"Visparam inválido {visparam}")

# --------------------------------------------------------------------------- #
# Builders específicos com execução em thread pool                            #
# --------------------------------------------------------------------------- #

def _vis_param(visparam: str) -> dict[str, Any]:
    vis = VISPARAMS.get(visparam)
    if vis is None:
        raise HTTPException(404, f"visparam não encontrado {visparam}")
    return vis

def _create_s2_layer_sync(geom: ee.Geometry, dates: Dict[str, str], vis: dict) -> str:
    """Versão síncrona para Earth Engine"""
    s2 = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
          .filterDate(dates["dtStart"], dates["dtEnd"])
          .filterBounds(geom)
          .sort("CLOUDY_PIXEL_PERCENTAGE", False)
          .select(*vis["select"]))
    best = s2.mosaic()
    map_id = ee.data.getMapId({"image": best, **vis["visparam"]})
    return map_id["tile_fetcher"].url_format

def _create_landsat_layer_sync(geom: ee.Geometry,
                              dates: Dict[str, str],
                              visparam_name: str) -> str:
    """Versão síncrona para Earth Engine"""
    year = datetime.fromisoformat(dates["dtStart"]).year
    collection = get_landsat_collection(year)

    vis = get_landsat_vis_params(visparam_name, collection)

    for key in ("min", "max", "gamma"):
        if isinstance(vis.get(key), list):
            vis[key] = ",".join(map(str, vis[key]))

    def scale(img):
        return img.addBands(img.select("SR_B.").multiply(0.0000275).add(-0.2),
                            None, True)

    landsat = (ee.ImageCollection(collection)
               .filterDate(dates["dtStart"], dates["dtEnd"])
               .filterBounds(geom)
               .map(scale)
               .select(vis["bands"])
               .sort("CLOUD_COVER", False)
               .mosaic())

    map_id = ee.data.getMapId({"image": landsat, **vis})
    return map_id["tile_fetcher"].url_format

# --------------------------------------------------------------------------- #
# Fluxo otimizado de tile com cache híbrido                                   #
# --------------------------------------------------------------------------- #

async def _serve_tile_optimized(
    layer: str,
    x: int, y: int, z: int,
    period: str | Period,
    year: int,
    month: int,
    visparam: str,
    builder_sync,
    background_tasks: BackgroundTasks
):
    """Versão otimizada com cache híbrido e processamento assíncrono"""
    _check_zoom(z)
    _check_capability(layer, year, period, visparam)

    dates = _build_periods(period, year, month)
    vis = _vis_param(visparam)

    geohash, bbox = tile2goehashBBOX(x, y, z)
    path_cache = f"{layer}_{period}_{year}_{month}_{visparam}/{geohash}"
    file_cache = f"{path_cache}/{z}/{x}_{y}.png"

    # 1. Busca rápida no cache híbrido
    png_bytes = await tile_cache.get_png(file_cache)
    if png_bytes:
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=2592000",  # 30 dias
                "X-Cache": "HIT"
            }
        )

    # 2. Busca/atualiza URL do Earth Engine
    meta = await tile_cache.get_meta(path_cache)
    expired = (
        meta is None or
        (datetime.now() - datetime.fromisoformat(meta["date"])).total_seconds()/3600
        > settings.LIFESPAN_URL
    )
    
    if expired:
        geom = ee.Geometry.BBox(bbox["w"], bbox["s"], bbox["e"], bbox["n"])
        try:
            # Executa em thread pool para não bloquear
            loop = asyncio.get_event_loop()
            if layer == "landsat":
                layer_url = await loop.run_in_executor(
                    ee_executor, builder_sync, geom, dates, visparam
                )
            else:
                layer_url = await loop.run_in_executor(
                    ee_executor, builder_sync, geom, dates, vis
                )
            
            await tile_cache.set_meta(path_cache, {
                "url": layer_url,
                "date": datetime.now().isoformat()
            })
        except Exception as e:
            logger.exception("Erro criar layer EE")
            return FileResponse("data/blank.png", media_type="image/png")
    else:
        layer_url = meta["url"]

    # 3. Download assíncrono do tile
    try:
        png_bytes = await _http_get_bytes(layer_url.format(x=x, y=y, z=z))
        
        # Salva no cache em background para não bloquear resposta
        background_tasks.add_task(tile_cache.set_png, file_cache, png_bytes)
        
        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=2592000",
                "X-Cache": "MISS"
            }
        )
    except HTTPException as exc:
        logger.exception("Erro ao baixar tile")
        error_img = generate_error_image(str(exc.detail))
        return StreamingResponse(
            io.BytesIO(error_img),
            media_type="image/png",
            headers={"X-Cache": "ERROR"}
        )

# --------------------------------------------------------------------------- #
# Endpoints otimizados                                                        #
# --------------------------------------------------------------------------- #

@router.get("/s2_harmonized/{x}/{y}/{z}")
@limit_sentinel()
async def s2_harmonized_optimized(
    x: int, y: int, z: int,
    background_tasks: BackgroundTasks,
    request: Request,
    period = Period.WET,
    year: int = datetime.now().year,
    month: int = datetime.now().month,
    visparam: str = "tvi-red"
):
    try:
        return await _serve_tile_optimized(
            "s2_harmonized", x, y, z,
            period.value, year, month,
            visparam, _create_s2_layer_sync,
            background_tasks
        )
    except HTTPException as exc:
        logger.error(f"Erro no tile s2_harmonized/{x}/{y}/{z}: {exc.detail}")
        error_img = generate_error_image(str(exc.detail))
        return StreamingResponse(
            io.BytesIO(error_img),
            media_type="image/png",
            headers={"X-Error": str(exc.detail), "X-Cache": "ERROR"}
        )
    except Exception as exc:
        logger.exception(f"Erro inesperado no tile s2_harmonized/{x}/{y}/{z}")
        error_img = generate_error_image("Erro interno do servidor")
        return StreamingResponse(
            io.BytesIO(error_img),
            media_type="image/png",
            headers={"X-Error": "Internal Server Error", "X-Cache": "ERROR"}
        )

@router.get("/landsat/{x}/{y}/{z}")
@limit_landsat()
async def landsat_optimized(
    x: int, y: int, z: int,
    background_tasks: BackgroundTasks,
    request: Request,
    period: str = "MONTH",
    year: int = datetime.now().year,
    month: int = datetime.now().month,
    visparam: str = "landsat-tvi-false"
):
    try:
        return await _serve_tile_optimized(
            "landsat", x, y, z,
            period, year, month,
            visparam, _create_landsat_layer_sync,
            background_tasks
        )
    except HTTPException as exc:
        logger.error(f"Erro no tile landsat/{x}/{y}/{z}: {exc.detail}")
        error_img = generate_error_image(str(exc.detail))
        return StreamingResponse(
            io.BytesIO(error_img),
            media_type="image/png",
            headers={"X-Error": str(exc.detail), "X-Cache": "ERROR"}
        )
    except Exception as exc:
        logger.exception(f"Erro inesperado no tile landsat/{x}/{y}/{z}")
        error_img = generate_error_image("Erro interno do servidor")
        return StreamingResponse(
            io.BytesIO(error_img),
            media_type="image/png",
            headers={"X-Error": "Internal Server Error", "X-Cache": "ERROR"}
        )

# --------------------------------------------------------------------------- #
# Endpoints auxiliares para monitoramento                                     #
# --------------------------------------------------------------------------- #

@router.get("/cache/stats")
async def cache_stats():
    """Retorna estatísticas do cache para monitoramento"""
    stats = await tile_cache.get_stats()
    return stats

@router.delete("/cache/clear")
async def clear_cache(
    layer: Optional[str] = None,
    year: Optional[int] = None,
    x: Optional[int] = None,
    y: Optional[int] = None,
    z: Optional[int] = None,
    pattern: Optional[str] = None
):
    """
    Remove entradas do cache do Redis e S3 baseado nos parâmetros fornecidos.
    
    Parâmetros:
    - layer: Nome da camada (ex: 'landsat', 's2_harmonized')
    - year: Ano específico para limpar
    - x, y, z: Coordenadas específicas do tile
    - pattern: Padrão customizado para busca (use com cuidado)
    
    Exemplos:
    - DELETE /cache/clear?layer=landsat - Remove todo cache da camada landsat
    - DELETE /cache/clear?year=2023 - Remove todo cache do ano 2023
    - DELETE /cache/clear?x=123&y=456&z=10 - Remove cache de um tile específico
    - DELETE /cache/clear?layer=landsat&year=2023 - Remove cache landsat de 2023
    """
    
    deleted_count = 0
    
    # Validação dos parâmetros
    if x is not None or y is not None or z is not None:
        if not all(v is not None for v in [x, y, z]):
            raise HTTPException(
                status_code=400,
                detail="Para limpar um tile específico, forneça x, y e z"
            )
    
    # Executa limpeza baseada nos parâmetros
    try:
        if pattern:
            # Uso direto de padrão (cuidado!)
            deleted_count = await tile_cache.delete_by_pattern(pattern)
        elif x is not None and y is not None and z is not None:
            # Limpa tile específico
            deleted_count = await tile_cache.clear_cache_by_point(x, y, z)
        elif layer and year:
            # Limpa camada específica de um ano
            deleted_count = await tile_cache.delete_by_pattern(f"{layer}_*_{year}_")
        elif layer:
            # Limpa toda a camada
            deleted_count = await tile_cache.clear_cache_by_layer(layer)
        elif year:
            # Limpa todo o ano
            deleted_count = await tile_cache.clear_cache_by_year(year)
        else:
            raise HTTPException(
                status_code=400,
                detail="Forneça pelo menos um parâmetro: layer, year, x/y/z ou pattern"
            )
        
        return {
            "status": "success",
            "deleted_count": deleted_count,
            "parameters": {
                "layer": layer,
                "year": year,
                "tile": {"x": x, "y": y, "z": z} if x is not None else None,
                "pattern": pattern
            }
        }
        
    except Exception as e:
        logger.exception(f"Erro ao limpar cache: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao limpar cache: {str(e)}"
        )

@router.post("/batch/process")
async def process_batch_tiles(
    request: Request,
    layer: str,
    tiles: list[dict],  # Lista de {"x": int, "y": int, "z": int}
    period: str = "DRY",
    year: int = datetime.now().year,
    month: int = datetime.now().month,
    visparam: str = "true-color"
):
    """
    Processa múltiplos tiles em batch para otimizar requisições
    
    Exemplo:
    POST /batch/process
    {
        "layer": "landsat",
        "tiles": [
            {"x": 123, "y": 456, "z": 10},
            {"x": 124, "y": 456, "z": 10}
        ],
        "period": "DRY",
        "year": 2024,
        "visparam": "true-color"
    }
    """
    # Adiciona ao batch processor
    batch_id = await batch_processor.add_request(
        layer,
        {
            "tiles": tiles,
            "period": period,
            "year": year,
            "month": month,
            "visparam": visparam
        }
    )
    
    return {
        "batch_id": batch_id,
        "status": "queued",
        "tiles_count": len(tiles)
    }

@router.get("/system/metrics")
@limiter.limit("10/minute")
async def system_metrics(request: Request):
    """Retorna métricas do sistema incluindo rate limiting adaptativo"""
    load = adaptive_limiter.get_system_load()
    current_limits = adaptive_limiter.current_limits
    
    return {
        "system": load,
        "rate_limits": current_limits,
        "cache_stats": await tile_cache.get_stats()
    }

@router.post("/cache/prewarm/{layer}")
async def prewarm_tiles(
    layer: str,
    background_tasks: BackgroundTasks,
    zoom_levels: list[int] = [12, 13, 14, 15],
    bounds: dict = None,
    point: dict = None,
    radius_km: float = 50.0
):
    """
    Pré-aquece tiles para uma região específica
    
    Parâmetros:
    - layer: Nome da camada (ex: 'landsat', 's2_harmonized')
    - zoom_levels: Lista de níveis de zoom para pre-aquecer
    - bounds: Caixa delimitadora com {"west", "south", "east", "north"}
    - point: Ponto central com {"lat", "lon"} - alternativa ao bounds
    - radius_km: Raio em km quando usando point (padrão: 50km)
    
    Exemplos:
    1. Com bounds:
       POST /cache/prewarm/landsat
       {
         "bounds": {"west": -50, "south": -20, "east": -40, "north": -10},
         "zoom_levels": [10, 11, 12]
       }
    
    2. Com ponto:
       POST /cache/prewarm/landsat
       {
         "point": {"lat": -15.7801, "lon": -47.9292},
         "radius_km": 100,
         "zoom_levels": [10, 11, 12]
       }
    """
    
    # Validação de parâmetros
    if point is not None and bounds is not None:
        raise HTTPException(
            status_code=400,
            detail="Forneça apenas 'bounds' OU 'point', não ambos"
        )
    
    # Se forneceu point, converte para bounds
    if point is not None:
        if "lat" not in point or "lon" not in point:
            raise HTTPException(
                status_code=400,
                detail="Point deve ter 'lat' e 'lon'"
            )
        
        # Converte point + radius para bounds
        bounds = _point_to_bounds(point["lat"], point["lon"], radius_km)
        logger.info(f"Convertendo point {point} com raio {radius_km}km para bounds {bounds}")
    
    # Se não forneceu nem bounds nem point, usa Brasil
    elif bounds is None:
        bounds = {
            "west": -73.9, "south": -33.7,
            "east": -34.8, "north": 5.3
        }
        logger.info("Usando bounds padrão do Brasil")
    
    # Valida bounds
    required_keys = ["west", "south", "east", "north"]
    if not all(key in bounds for key in required_keys):
        raise HTTPException(
            status_code=400,
            detail=f"Bounds deve ter as chaves: {required_keys}"
        )
    
    # Adiciona tarefa de pre-warming em background
    background_tasks.add_task(
        _prewarm_region,
        layer, zoom_levels, bounds
    )
    
    return {
        "status": "Pre-warming iniciado",
        "layer": layer,
        "zoom_levels": zoom_levels,
        "bounds": bounds,
        "estimated_area_km2": _calculate_area_km2(bounds)
    }

def _point_to_bounds(lat: float, lon: float, radius_km: float) -> dict:
    """Converte ponto + raio para bounds"""
    import math
    
    # Aproximação: 1 grau de latitude = ~111km
    # 1 grau de longitude = ~111km * cos(latitude)
    lat_degree_km = 111.0
    lon_degree_km = 111.0 * math.cos(math.radians(lat))
    
    # Calcula deltas em graus
    lat_delta = radius_km / lat_degree_km
    lon_delta = radius_km / lon_degree_km
    
    return {
        "south": lat - lat_delta,
        "north": lat + lat_delta,
        "west": lon - lon_delta,
        "east": lon + lon_delta
    }

def _calculate_area_km2(bounds: dict) -> float:
    """Calcula área aproximada em km² dos bounds"""
    import math
    
    # Latitude média para cálculo mais preciso
    lat_avg = (bounds["north"] + bounds["south"]) / 2
    
    # Diferenças em graus
    lat_diff = bounds["north"] - bounds["south"]
    lon_diff = bounds["east"] - bounds["west"]
    
    # Converte para km
    lat_km = lat_diff * 111.0
    lon_km = lon_diff * 111.0 * math.cos(math.radians(lat_avg))
    
    return round(lat_km * lon_km, 2)

async def _prewarm_region(layer: str, zoom_levels: list[int], bounds: dict):
    """Executa pre-warming de tiles em background"""
    import math
    from app.tile import latlon_to_tile
    from datetime import datetime
    
    logger.info(f"Iniciando pre-warming {layer} para zooms {zoom_levels}")
    
    # Estatísticas de progresso
    total_tiles = 0
    cached_tiles = 0
    failed_tiles = 0
    
    # Configurações padrão para pre-warming
    current_year = datetime.now().year
    default_params = {
        "landsat": {
            "period": "DRY",
            "year": current_year,
            "month": 1,
            "visparam": "true-color"
        },
        "s2_harmonized": {
            "period": "DRY", 
            "year": current_year,
            "month": 1,
            "visparam": "true-color"
        }
    }
    
    # Usa parâmetros padrão ou custom
    params = default_params.get(layer, {
        "period": "DRY",
        "year": current_year,
        "month": 1,
        "visparam": "true-color"
    })
    
    # Sessão HTTP reutilizável para performance
    async with aiohttp.ClientSession() as session:
        for zoom in zoom_levels:
            # Calcula tiles baseado nos bounds
            x_min, y_max = latlon_to_tile(bounds["south"], bounds["west"], zoom)
            x_max, y_min = latlon_to_tile(bounds["north"], bounds["east"], zoom)
            
            # Ajusta para garantir ordem correta
            if x_min > x_max:
                x_min, x_max = x_max, x_min
            if y_min > y_max:
                y_min, y_max = y_max, y_min
            
            tiles_in_zoom = (x_max - x_min + 1) * (y_max - y_min + 1)
            logger.info(f"Zoom {zoom}: {tiles_in_zoom} tiles ({x_min},{y_min} até {x_max},{y_max})")
            
            # Processa tiles em batches para não sobrecarregar
            batch_size = 10
            tasks = []
            
            for x in range(x_min, x_max + 1):
                for y in range(y_min, y_max + 1):
                    total_tiles += 1
                    
                    # Cria tarefa assíncrona
                    task = _prewarm_single_tile(
                        layer, x, y, zoom,
                        params["period"],
                        params["year"],
                        params["month"],
                        params["visparam"],
                        session
                    )
                    tasks.append(task)
                    
                    # Processa em batches
                    if len(tasks) >= batch_size:
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        
                        # Contabiliza resultados
                        for result in results:
                            if isinstance(result, Exception):
                                failed_tiles += 1
                                logger.warning(f"Falha no pre-warming: {result}")
                            elif result:
                                cached_tiles += 1
                        
                        tasks = []
                        
                        # Log de progresso a cada 100 tiles
                        if total_tiles % 100 == 0:
                            logger.info(
                                f"Progresso: {total_tiles} tiles processados, "
                                f"{cached_tiles} em cache, {failed_tiles} falhas"
                            )
            
            # Processa tiles restantes
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        failed_tiles += 1
                    elif result:
                        cached_tiles += 1
    
    # Log final
    logger.info(
        f"Pre-warming concluído para {layer}: "
        f"{total_tiles} tiles processados, "
        f"{cached_tiles} adicionados ao cache, "
        f"{failed_tiles} falhas"
    )
    
    return {
        "layer": layer,
        "zoom_levels": zoom_levels,
        "total_tiles": total_tiles,
        "cached_tiles": cached_tiles,
        "failed_tiles": failed_tiles
    }

async def _prewarm_single_tile(
    layer: str, x: int, y: int, z: int,
    period: str, year: int, month: int, visparam: str,
    session: aiohttp.ClientSession
) -> bool:
    """Pre-aquece um único tile"""
    try:
        # Constrói parâmetros do tile
        from app.tile import tile2goehashBBOX
        geohash, bbox = tile2goehashBBOX(x, y, z)
        path_cache = f"{layer}_{period}_{year}_{month}_{visparam}/{geohash}"
        file_cache = f"{path_cache}/{z}/{x}_{y}.png"
        
        # Verifica se já está em cache
        png_bytes = await tile_cache.get_png(file_cache)
        if png_bytes:
            return False  # Já estava em cache
        
        # Busca/atualiza URL do Earth Engine se necessário
        meta = await tile_cache.get_meta(path_cache)
        dates = _build_periods(period, year, month)
        
        if not meta or (datetime.now() - datetime.fromisoformat(meta["date"])).total_seconds()/3600 > settings.LIFESPAN_URL:
            # Precisa atualizar URL do Earth Engine
            geom = ee.Geometry.BBox(bbox["w"], bbox["s"], bbox["e"], bbox["n"])
            
            # Executa builder apropriado
            loop = asyncio.get_event_loop()
            if layer == "landsat":
                layer_url = await loop.run_in_executor(
                    ee_executor, _create_landsat_layer_sync, geom, dates, visparam
                )
            else:
                vis = _vis_param(visparam)
                layer_url = await loop.run_in_executor(
                    ee_executor, _create_s2_layer_sync, geom, dates, vis
                )
            
            # Salva metadata
            new_meta = {"url": layer_url, "date": datetime.now().isoformat()}
            await tile_cache.set_meta(path_cache, new_meta)
        else:
            layer_url = meta["url"]
        
        # Download do tile
        tile_url = layer_url.replace("{x}", str(x)).replace("{y}", str(y)).replace("{z}", str(z))
        png_bytes = await _http_get_bytes(tile_url, session)
        
        # Salva no cache
        await tile_cache.set_png(file_cache, png_bytes)
        
        return True  # Novo tile adicionado ao cache
        
    except Exception as e:
        logger.debug(f"Erro no pre-warming tile {layer}/{z}/{x}/{y}: {e}")
        raise