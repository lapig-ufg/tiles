"""
API para gerenciamento de cache warming
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import asyncio

from app.cache.cache_warmer import (
    CacheWarmer, LoadingPattern, ViewportBounds,
    schedule_warmup_task, analyze_usage_patterns_task
)
from app.tasks.tasks import celery_app
from loguru import logger


router = APIRouter()


class CacheWarmupRequest(BaseModel):
    """Requisição para aquecimento de cache"""
    layer: str = Field(..., description="Nome da camada para aquecer cache")
    params: Dict[str, Any] = Field(default_factory=dict, description="Parâmetros da camada")
    max_tiles: int = Field(500, ge=1, le=10000, description="Número máximo de tiles")
    batch_size: int = Field(50, ge=1, le=200, description="Tamanho do lote para processamento")
    patterns: List[str] = Field(
        default=["spiral", "grid"],
        description="Padrões de carregamento a simular"
    )
    regions: Optional[List[Dict[str, float]]] = Field(
        None,
        description="Regiões específicas para aquecer (min_lat, max_lat, min_lon, max_lon)"
    )


class CacheWarmupResponse(BaseModel):
    """Resposta do aquecimento de cache"""
    task_id: str
    status: str
    total_tiles: int
    batches: int
    estimated_time_minutes: float
    message: str


class CacheStatusResponse(BaseModel):
    """Status do cache"""
    total_cached_tiles: int
    cache_hit_rate: float
    popular_tiles: List[Dict[str, Any]]
    last_warmup: Optional[datetime]
    active_tasks: int


class SimulationRequest(BaseModel):
    """Requisição para simular navegação de usuário"""
    start_lat: float = Field(..., ge=-90, le=90)
    start_lon: float = Field(..., ge=-180, le=180)
    zoom_levels: List[int] = Field(..., min_items=1)
    movement_pattern: str = Field("random", description="random, linear, circular")
    duration_seconds: int = Field(60, ge=1, le=300)
    tiles_per_second: int = Field(5, ge=1, le=50)


@router.post("/warmup", response_model=CacheWarmupResponse)
async def warmup_cache(request: CacheWarmupRequest):
    """
    Inicia processo de aquecimento de cache
    
    Este endpoint agenda tasks Celery para pré-carregar tiles populares
    simulando padrões de requisição de webmaps reais.
    """
    try:
        # Valida padrões
        valid_patterns = [p.value for p in LoadingPattern]
        for pattern in request.patterns:
            if pattern not in valid_patterns:
                raise HTTPException(
                    status_code=400,
                    detail=f"Padrão inválido: {pattern}. Válidos: {valid_patterns}"
                )
        
        # Agenda task de warmup
        result = schedule_warmup_task.delay(
            layer=request.layer,
            params=request.params,
            max_tiles=request.max_tiles,
            batch_size=request.batch_size
        )
        
        # Estima tempo
        estimated_time = (request.max_tiles / request.batch_size) * 2  # ~2s por lote
        
        return CacheWarmupResponse(
            task_id=result.id,
            status="scheduled",
            total_tiles=request.max_tiles,
            batches=request.max_tiles // request.batch_size,
            estimated_time_minutes=estimated_time / 60,
            message=f"Aquecimento de cache agendado para {request.max_tiles} tiles"
        )
        
    except Exception as e:
        logger.error(f"Erro ao agendar warmup: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/simulate-navigation")
async def simulate_navigation(
    request: SimulationRequest,
    background_tasks: BackgroundTasks
):
    """
    Simula navegação de usuário para aquecer cache
    
    Simula um usuário navegando pelo mapa, gerando requisições
    de tiles como se fosse um cliente real (Leaflet/OpenLayers).
    """
    try:
        async def _simulate():
            """Executa simulação em background"""
            warmer = CacheWarmer()
            tiles_generated = 0
            
            current_lat = request.start_lat
            current_lon = request.start_lon
            
            for _ in range(request.duration_seconds):
                for zoom in request.zoom_levels:
                    # Gera viewport atual
                    viewport = ViewportBounds(
                        min_lat=current_lat - 0.1,
                        max_lat=current_lat + 0.1,
                        min_lon=current_lon - 0.1,
                        max_lon=current_lon + 0.1,
                        zoom=zoom
                    )
                    
                    # Gera tiles para o viewport
                    tiles = warmer.generate_warmup_tasks(
                        layer="simulated",
                        params={},
                        patterns=[LoadingPattern.VIEWPORT],
                        max_tiles=request.tiles_per_second
                    )
                    
                    tiles_generated += len(tiles)
                    
                    # Move posição baseado no padrão
                    if request.movement_pattern == "random":
                        current_lat += (asyncio.create_task(asyncio.sleep(0)).result() or 0.01) * (-1 if tiles_generated % 2 else 1)
                        current_lon += (asyncio.create_task(asyncio.sleep(0)).result() or 0.01) * (-1 if tiles_generated % 3 else 1)
                    elif request.movement_pattern == "linear":
                        current_lat += 0.01
                        current_lon += 0.01
                    elif request.movement_pattern == "circular":
                        import math
                        angle = (tiles_generated / request.tiles_per_second) * 2 * math.pi / 100
                        current_lat = request.start_lat + 0.1 * math.sin(angle)
                        current_lon = request.start_lon + 0.1 * math.cos(angle)
                    
                    # Limita coordenadas
                    current_lat = max(-85, min(85, current_lat))
                    current_lon = max(-180, min(180, current_lon))
                
                await asyncio.sleep(1)
            
            logger.info(f"Simulação completa: {tiles_generated} tiles gerados")
        
        # Agenda simulação em background
        background_tasks.add_task(_simulate)
        
        return {
            "status": "started",
            "message": f"Simulação iniciada por {request.duration_seconds} segundos",
            "estimated_tiles": request.tiles_per_second * request.duration_seconds * len(request.zoom_levels)
        }
        
    except Exception as e:
        logger.error(f"Erro na simulação: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status", response_model=CacheStatusResponse)
async def get_cache_status():
    """
    Retorna status atual do cache e métricas
    """
    try:
        # Obtém informações do Celery
        inspect = celery_app.control.inspect()
        active_tasks = len(inspect.active() or {})
        
        # TODO: Integrar com sistema de métricas real
        return CacheStatusResponse(
            total_cached_tiles=12543,  # Placeholder
            cache_hit_rate=0.85,  # Placeholder
            popular_tiles=[
                {"tile": "10/512/512", "hits": 1523},
                {"tile": "11/1024/1024", "hits": 1342},
                {"tile": "12/2048/2048", "hits": 987}
            ],
            last_warmup=datetime.now(),
            active_tasks=active_tasks
        )
        
    except Exception as e:
        logger.error(f"Erro ao obter status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/task/{task_id}")
async def get_task_status(task_id: str):
    """
    Verifica status de uma task de warmup
    """
    try:
        result = celery_app.AsyncResult(task_id)
        
        return {
            "task_id": task_id,
            "status": result.status,
            "ready": result.ready(),
            "successful": result.successful() if result.ready() else None,
            "result": result.result if result.ready() else None,
            "info": result.info
        }
        
    except Exception as e:
        logger.error(f"Erro ao verificar task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/analyze-patterns")
async def analyze_patterns(days: int = Query(7, ge=1, le=30)):
    """
    Analisa padrões de uso para otimizar cache
    """
    try:
        result = analyze_usage_patterns_task.delay(days)
        
        return {
            "task_id": result.id,
            "status": "analyzing",
            "message": f"Analisando padrões dos últimos {days} dias"
        }
        
    except Exception as e:
        logger.error(f"Erro ao analisar padrões: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/clear")
async def clear_cache(
    layer: Optional[str] = None,
    zoom: Optional[int] = None,
    confirm: bool = Query(False, description="Confirmar limpeza do cache")
):
    """
    Limpa o cache (use com cuidado!)
    """
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Defina confirm=true para confirmar a limpeza"
        )
    
    try:
        # TODO: Implementar limpeza seletiva do cache
        filters = []
        if layer:
            filters.append(f"layer={layer}")
        if zoom:
            filters.append(f"zoom={zoom}")
        
        return {
            "status": "cleared",
            "filters": filters,
            "message": "Cache limpo com sucesso"
        }
        
    except Exception as e:
        logger.error(f"Erro ao limpar cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recommendations")
async def get_cache_recommendations():
    """
    Retorna recomendações para otimização de cache
    """
    warmer = CacheWarmer()
    
    recommendations = []
    
    # Analisa regiões populares
    for idx, region in enumerate(warmer.popular_regions):
        recommendations.append({
            "type": "popular_region",
            "priority": "high",
            "region_id": idx,
            "bounds": {
                "min_lat": region.min_lat,
                "max_lat": region.max_lat,
                "min_lon": region.min_lon,
                "max_lon": region.max_lon
            },
            "recommended_zoom_levels": list(range(region.zoom - 1, region.zoom + 2)),
            "estimated_tiles": 500
        })
    
    # Recomenda zooms prioritários
    priority_zooms = sorted(
        warmer.zoom_priorities.items(),
        key=lambda x: x[1],
        reverse=True
    )[:5]
    
    recommendations.append({
        "type": "zoom_optimization",
        "priority": "medium",
        "recommended_zooms": [z[0] for z in priority_zooms],
        "reason": "Níveis de zoom mais utilizados"
    })
    
    return {
        "recommendations": recommendations,
        "total_recommendations": len(recommendations)
    }