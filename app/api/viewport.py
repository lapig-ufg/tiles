"""
Sistema de otimização baseado em viewport para carregar apenas tiles visíveis
"""
from fastapi import APIRouter, HTTPException, Request, status
from typing import List, Dict, Any
from pydantic import BaseModel, Field
import asyncio
from app.config import logger

router = APIRouter(prefix="/viewport", tags=["Viewport"])

# Modelos Pydantic para documentação
class ViewportBounds(BaseModel):
    """Limites geográficos do viewport"""
    north: float = Field(..., description="Latitude norte (máxima)", example=-10.5)
    south: float = Field(..., description="Latitude sul (mínima)", example=-15.5)
    east: float = Field(..., description="Longitude leste (máxima)", example=-45.0)
    west: float = Field(..., description="Longitude oeste (mínima)", example=-50.0)
    
    class Config:
        schema_extra = {
            "example": {
                "north": -10.5,
                "south": -15.5,
                "east": -45.0,
                "west": -50.0
            }
        }

class TileInfo(BaseModel):
    """Informações de um tile individual"""
    x: int = Field(..., description="Coordenada X do tile")
    y: int = Field(..., description="Coordenada Y do tile")
    z: int = Field(..., description="Nível de zoom")
    year: int = Field(..., description="Ano dos dados")
    layer: str = Field(..., description="Camada de dados (ex: landsat, sentinel)")
    period: str = Field(..., description="Período (DRY/WET)")
    priority: int = Field(..., description="Prioridade de carregamento (0=máxima)")
    url: str = Field(..., description="URL para baixar o tile")

class ViewportTilesRequest(BaseModel):
    """Requisição para obter tiles do viewport"""
    viewport: ViewportBounds = Field(..., description="Limites geográficos da área visível")
    zoom: int = Field(..., ge=0, le=20, description="Nível de zoom do mapa")
    years: List[int] = Field(..., description="Lista de anos solicitados", example=[2020, 2021, 2022, 2023])
    layer: str = Field("landsat", description="Camada de dados a ser carregada")
    period: str = Field("DRY", description="Período do ano (DRY=seco, WET=úmido)")
    priority_year: int = Field(None, description="Ano atualmente visível (para priorização)")

class ViewportTilesResponse(BaseModel):
    """Resposta com tiles otimizados para o viewport"""
    viewport: ViewportBounds = Field(..., description="Viewport processado")
    total_tiles: int = Field(..., description="Total de tiles gerados")
    tiles_per_year: int = Field(..., description="Quantidade de tiles por ano")
    years_count: int = Field(..., description="Quantidade de anos processados")
    tiles: List[TileInfo] = Field(..., description="Lista de tiles organizados por prioridade")

@router.post(
    "/tiles",
    response_model=ViewportTilesResponse,
    status_code=status.HTTP_200_OK,
    summary="Obtém tiles visíveis no viewport",
    description="""
    Retorna uma lista otimizada de tiles baseada no viewport atual do mapa.
    
    Este endpoint implementa uma estratégia inteligente de carregamento:
    - **Prioridade 0**: Ano atualmente visível (priority_year)
    - **Prioridade 1-2**: Anos adjacentes ao ano atual
    - **Prioridade 3+**: Demais anos em ordem decrescente
    
    Útil para aplicações que precisam carregar tiles progressivamente,
    priorizando o conteúdo mais relevante para o usuário.
    """,
    responses={
        200: {
            "description": "Lista de tiles calculada com sucesso",
            "content": {
                "application/json": {
                    "example": {
                        "viewport": {
                            "north": -10.5,
                            "south": -15.5,
                            "east": -45.0,
                            "west": -50.0
                        },
                        "total_tiles": 240,
                        "tiles_per_year": 60,
                        "years_count": 4,
                        "tiles": [
                            {
                                "x": 2345,
                                "y": 5678,
                                "z": 10,
                                "year": 2023,
                                "layer": "landsat",
                                "period": "DRY",
                                "priority": 0,
                                "url": "/api/layers/landsat/2345/5678/10?year=2023&period=DRY"
                            }
                        ]
                    }
                }
            }
        },
        400: {
            "description": "Parâmetros inválidos",
            "content": {
                "application/json": {
                    "example": {"detail": "Viewport inválido: coordenadas fora dos limites"}
                }
            }
        }
    }
)
async def get_viewport_tiles(
    request: Request,
    body: ViewportTilesRequest
):
    """
    Retorna tiles otimizados baseado no viewport atual
    
    Estratégia:
    1. Carrega primeiro o ano em foco (priority_year)
    2. Carrega anos adjacentes (+1, -1)
    3. Carrega resto em background
    """
    from app.tile import latlon_to_tile
    
    # Extrai dados do body
    viewport = body.viewport
    zoom = body.zoom
    years = body.years
    layer = body.layer
    period = body.period
    priority_year = body.priority_year
    
    # Calcula tiles visíveis no viewport
    x_min, y_max = latlon_to_tile(viewport.south, viewport.west, zoom)
    x_max, y_min = latlon_to_tile(viewport.north, viewport.east, zoom)
    
    # Organiza anos por prioridade
    if priority_year and priority_year in years:
        # Ano atual primeiro, depois adjacentes, depois resto
        ordered_years = [priority_year]
        
        # Anos adjacentes
        if priority_year - 1 in years:
            ordered_years.append(priority_year - 1)
        if priority_year + 1 in years:
            ordered_years.append(priority_year + 1)
            
        # Resto dos anos
        for year in sorted(years):
            if year not in ordered_years:
                ordered_years.append(year)
    else:
        ordered_years = sorted(years, reverse=True)  # Mais recentes primeiro
    
    # Gera lista de tiles organizados por prioridade
    tiles = []
    
    for priority, year in enumerate(ordered_years):
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                tiles.append(TileInfo(
                    x=x,
                    y=y,
                    z=zoom,
                    year=year,
                    layer=layer,
                    period=period,
                    priority=priority,  # 0 = máxima prioridade
                    url=f"/api/layers/{layer}/{x}/{y}/{zoom}?year={year}&period={period}"
                ))
    
    return ViewportTilesResponse(
        viewport=viewport,
        total_tiles=len(tiles),
        tiles_per_year=(x_max - x_min + 1) * (y_max - y_min + 1),
        years_count=len(years),
        tiles=tiles[:1000]  # Limita para evitar response muito grande
    )

# Modelos adicionais para o endpoint progressive
class CurrentView(BaseModel):
    """Estado atual da visualização"""
    year: int = Field(..., description="Ano atualmente visível")
    animation_playing: bool = Field(False, description="Se a animação está em execução")
    
    class Config:
        schema_extra = {
            "example": {
                "year": 2023,
                "animation_playing": False
            }
        }

class ProgressiveRequest(BaseModel):
    """Requisição para estratégia progressiva"""
    viewport: ViewportBounds = Field(..., description="Limites geográficos da área visível")
    zoom: int = Field(..., ge=0, le=20, description="Nível de zoom do mapa")
    years: List[int] = Field(..., description="Lista de anos disponíveis")
    current_view: CurrentView = Field(..., description="Estado atual da visualização")

class LoadStrategy(BaseModel):
    """Estratégia de carregamento"""
    name: str = Field(..., description="Nome da estratégia")
    description: str = Field(..., description="Descrição da estratégia")
    load_order: List[int] = Field(None, description="Ordem de carregamento dos anos")
    steps: List[Dict[str, Any]] = Field(None, description="Passos para carregamento progressivo")

@router.post(
    "/progressive",
    response_model=List[LoadStrategy],
    status_code=status.HTTP_200_OK,
    summary="Obtém estratégia de carregamento progressivo",
    description="""
    Retorna estratégias inteligentes de carregamento baseadas no contexto atual.
    
    **Estratégias disponíveis:**
    
    1. **animation_optimized**: Para quando a animação está ativa
       - Carrega os próximos 5 anos na sequência
       - Otimiza para transições suaves entre frames
    
    2. **static_optimized**: Para visualização estática
       - Carrega o ano atual primeiro
       - Depois carrega anos adjacentes (±5 anos)
       - Por fim, carrega o restante
    
    3. **resolution_progressive**: Sempre incluída
       - Carrega preview em baixa resolução
       - Progressivamente aumenta a qualidade
       - Melhora a experiência do usuário
    """,
    responses={
        200: {
            "description": "Estratégias calculadas com sucesso",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "name": "static_optimized",
                            "description": "Carrega ano atual primeiro, depois adjacentes",
                            "load_order": [2023, 2022, 2024, 2021, 2025, 2020]
                        },
                        {
                            "name": "resolution_progressive",
                            "description": "Carrega baixa resolução primeiro, depois refina",
                            "steps": [
                                {"zoom": 8, "quality": "preview"},
                                {"zoom": 9, "quality": "medium"},
                                {"zoom": 10, "quality": "full"}
                            ]
                        }
                    ]
                }
            }
        }
    }
)
async def progressive_load_strategy(
    body: ProgressiveRequest
):
    """
    Estratégia de carregamento progressivo inteligente
    """
    # Extrai dados do body
    viewport = body.viewport
    zoom = body.zoom
    years = body.years
    current_view = body.current_view
    
    strategies = []
    
    # Se está em animação
    if current_view.animation_playing:
        strategies.append(LoadStrategy(
            name="animation_optimized",
            description="Carrega anos em sequência para animação suave",
            load_order=_get_animation_order(years, current_view.year)
        ))
    else:
        # Visualização estática
        strategies.append(LoadStrategy(
            name="static_optimized",
            description="Carrega ano atual primeiro, depois adjacentes",
            load_order=_get_static_order(years, current_view.year)
        ))
    
    # Sempre inclui estratégia de resolução progressiva
    strategies.append(LoadStrategy(
        name="resolution_progressive",
        description="Carrega baixa resolução primeiro, depois refina",
        steps=[
            {"zoom": max(6, zoom - 2), "quality": "preview"},
            {"zoom": zoom - 1, "quality": "medium"},
            {"zoom": zoom, "quality": "full"}
        ]
    ))
    
    return strategies

def _get_animation_order(years: List[int], current_year: int) -> List[int]:
    """Ordena anos para otimizar animação"""
    # Carrega próximos 5 anos na direção da animação
    idx = years.index(current_year) if current_year in years else 0
    
    result = []
    # Próximos 5 anos
    for i in range(5):
        if idx + i < len(years):
            result.append(years[idx + i])
    
    # Resto dos anos
    for year in years:
        if year not in result:
            result.append(year)
            
    return result

def _get_static_order(years: List[int], current_year: int) -> List[int]:
    """Ordena anos para visualização estática"""
    if current_year not in years:
        return sorted(years, reverse=True)
    
    result = [current_year]
    
    # Adiciona anos próximos (±5 anos)
    for delta in range(1, 6):
        if current_year - delta in years:
            result.append(current_year - delta)
        if current_year + delta in years:
            result.append(current_year + delta)
    
    # Adiciona resto
    for year in sorted(years, reverse=True):
        if year not in result:
            result.append(year)
    
    return result