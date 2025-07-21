"""
Sistema de agregação de tiles para reduzir número de requisições
"""
import asyncio
import io
from datetime import datetime
from typing import List

from PIL import Image
from fastapi import APIRouter, HTTPException, BackgroundTasks, Response, status, Query, Path
from pydantic import BaseModel, Field

from app.cache.cache_hybrid import tile_cache
from app.core.config import logger

router = APIRouter(prefix="/aggregation", tags=["Agregação de Tiles"])

# Modelos Pydantic para documentação
class MegatileResponse(BaseModel):
    """Resposta binária do megatile"""
    # Nota: Este modelo é apenas para documentação
    # A resposta real é uma imagem PNG binária
    pass

@router.get(
    "/megatile/{layer}/{x}/{y}/{z}",
    response_class=Response,
    status_code=status.HTTP_200_OK,
    summary="Obtém um megatile agregado",
    description="""
    Retorna um 'megatile' que combina múltiplos tiles em uma única imagem PNG.
    
    **Vantagens:**
    - Reduz drasticamente o número de requisições HTTP
    - Melhora performance de carregamento
    - Otimiza uso de banda
    
    **Como funciona:**
    - Em vez de fazer 16 requisições para um grid 4x4
    - Faz apenas 1 requisição que retorna todos os tiles combinados
    - O cliente recorta a imagem localmente
    
    **Formato de saída:**
    - Imagem PNG com tiles organizados em grid
    - Cada ano é uma linha no grid
    - Largura: `size * 256` pixels
    - Altura: `num_anos * size * 256` pixels
    """,
    responses={
        200: {
            "description": "Imagem PNG contendo o megatile",
            "content": {
                "image/png": {
                    "example": "[Dados binários da imagem PNG]"
                }
            },
            "headers": {
                "X-Cache": {
                    "description": "Status do cache (HIT/MISS)",
                    "schema": {"type": "string"}
                },
                "X-Megatile": {
                    "description": "Indica que é um megatile",
                    "schema": {"type": "string", "example": "true"}
                }
            }
        },
        400: {
            "description": "Parâmetros inválidos",
            "content": {
                "application/json": {
                    "example": {"detail": "Lista de anos inválida"}
                }
            }
        },
        500: {
            "description": "Erro ao gerar megatile",
            "content": {
                "application/json": {
                    "example": {"detail": "Falha ao processar tiles"}
                }
            }
        }
    }
)
async def get_megatile(
    layer: str = Path(..., description="Camada de dados (ex: landsat, sentinel)"),
    x: int = Path(..., description="Coordenada X base do megatile"),
    y: int = Path(..., description="Coordenada Y base do megatile"), 
    z: int = Path(..., ge=0, le=20, description="Nível de zoom"),
    years: str = Query(..., description="Anos separados por vírgula", example="2020,2021,2022,2023"),
    size: int = Query(2, ge=1, le=8, description="Tamanho do grid (NxN tiles)"),
    background_tasks: BackgroundTasks = None
):
    """
    Retorna um 'megatile' que combina múltiplos tiles em uma imagem
    Reduz drasticamente o número de requisições HTTP
    
    Exemplo: Em vez de 16 requisições para um grid 4x4,
    faz 1 requisição que retorna todos os 16 tiles combinados
    """
    year_list = [int(y) for y in years.split(",")]
    
    # Gera megatile ou retorna do cache
    cache_key = f"megatile/{layer}/{x}_{y}_{z}_{years}_{size}"
    
    cached = await tile_cache.get_png(cache_key)
    if cached:
        return Response(
            content=cached,
            media_type="image/png",
            headers={"X-Cache": "HIT", "X-Megatile": "true"}
        )
    
    # Gera megatile
    megatile = await _generate_megatile(
        layer, x, y, z, year_list, size
    )
    
    # Salva em cache em background
    background_tasks.add_task(
        tile_cache.set_png, cache_key, megatile
    )
    
    return Response(
        content=megatile,
        media_type="image/png",
        headers={"X-Cache": "MISS", "X-Megatile": "true"}
    )

async def _generate_megatile(
    layer: str,
    base_x: int,
    base_y: int,
    z: int,
    years: List[int],
    size: int
) -> bytes:
    """Gera um megatile combinando múltiplos tiles"""
    
    # Tamanho padrão de um tile
    TILE_SIZE = 256
    
    # Cria imagem grande para combinar tiles
    megatile_size = TILE_SIZE * size * len(years)
    megatile = Image.new('RGBA', (TILE_SIZE * size, megatile_size))
    
    # Coleta todos os tiles necessários
    tasks = []
    positions = []
    
    for year_idx, year in enumerate(years):
        for dx in range(size):
            for dy in range(size):
                x = base_x + dx
                y = base_y + dy
                
                # Posição no megatile
                pos_x = dx * TILE_SIZE
                pos_y = (year_idx * size + dy) * TILE_SIZE
                
                positions.append((pos_x, pos_y))
                
                # URL do tile individual
                tile_url = f"/api/layers/{layer}/{x}/{y}/{z}?year={year}"
                tasks.append(_fetch_tile(tile_url))
    
    # Busca todos os tiles em paralelo
    tiles = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Combina tiles na imagem grande
    for (pos_x, pos_y), tile_data in zip(positions, tiles):
        if isinstance(tile_data, Exception):
            logger.warning(f"Falha ao buscar tile: {tile_data}")
            continue
            
        try:
            tile_img = Image.open(io.BytesIO(tile_data))
            megatile.paste(tile_img, (pos_x, pos_y))
        except Exception as e:
            logger.error(f"Erro ao processar tile: {e}")
    
    # Converte para bytes
    output = io.BytesIO()
    megatile.save(output, format='PNG', optimize=True)
    return output.getvalue()

async def _fetch_tile(url: str) -> bytes:
    """Busca um tile individual"""
    # Aqui você faria a requisição real
    # Por simplicidade, retorna um tile dummy
    return b"dummy_tile_data"

class RegionBounds(BaseModel):
    """Limites geográficos de uma região"""
    north: float = Field(..., description="Latitude norte (máxima)", example=-10.5)
    south: float = Field(..., description="Latitude sul (mínima)", example=-15.5)
    east: float = Field(..., description="Longitude leste (máxima)", example=-45.0)
    west: float = Field(..., description="Longitude oeste (mínima)", example=-50.0)

class SpriteSheetRequest(BaseModel):
    """Requisição para gerar sprite sheet"""
    layer: str = Field(..., description="Camada de dados (ex: landsat, sentinel)")
    region: RegionBounds = Field(..., description="Região geográfica para o sprite sheet")
    years: List[int] = Field(..., description="Lista de anos a incluir", example=[2020, 2021, 2022, 2023])
    zoom: int = Field(..., ge=0, le=20, description="Nível de zoom")

class SpriteSheetResponse(BaseModel):
    """Resposta da geração de sprite sheet"""
    sprite_id: str = Field(..., description="ID único do sprite sheet")
    status: str = Field(..., description="Status da geração")
    tiles_count: int = Field(..., description="Total de tiles no sprite sheet")
    estimated_size_mb: float = Field(..., description="Tamanho estimado em MB")

@router.post(
    "/sprites/generate",
    response_model=SpriteSheetResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Gera sprite sheet de tiles",
    description="""
    Inicia a geração de um sprite sheet contendo todos os tiles de uma região e período.
    
    **O que é um Sprite Sheet?**
    - Arquivo único contendo múltiplos tiles
    - Cliente baixa apenas 1 arquivo grande
    - Recortes são feitos localmente
    
    **Vantagens:**
    - Ideal para regiões pequenas/médias
    - Reduz latência de rede
    - Permite cache offline
    
    **Processo:**
    1. Cliente solicita geração do sprite sheet
    2. Servidor processa em background
    3. Cliente consulta status via sprite_id
    4. Quando pronto, baixa o arquivo completo
    
    **Limitações:**
    - Máximo 1000 tiles por sprite sheet
    - Tempo de processamento proporcional ao tamanho
    """,
    responses={
        202: {
            "description": "Geração iniciada com sucesso",
            "content": {
                "application/json": {
                    "example": {
                        "sprite_id": "landsat_10_2345_5678_2350_5683_2020_2021_2022_2023",
                        "status": "generating",
                        "tiles_count": 240,
                        "estimated_size_mb": 24.0
                    }
                }
            }
        },
        400: {
            "description": "Parâmetros inválidos",
            "content": {
                "application/json": {
                    "example": {"detail": "Região muito grande para sprite sheet"}
                }
            }
        }
    }
)
async def generate_sprite_sheet(
    body: SpriteSheetRequest,
    background_tasks: BackgroundTasks
):
    """
    Gera um sprite sheet com todos os tiles de uma região/período
    Cliente baixa 1 arquivo e faz recortes localmente
    """
    from app.services.tile import latlon_to_tile
    
    # Extrai dados do body
    layer = body.layer
    region = body.region
    years = body.years
    zoom = body.zoom
    
    # Calcula tiles necessários
    x_min, y_max = latlon_to_tile(region.south, region.west, zoom)
    x_max, y_min = latlon_to_tile(region.north, region.east, zoom)
    
    # Validação de tamanho
    tiles_count = (x_max - x_min + 1) * (y_max - y_min + 1) * len(years)
    if tiles_count > 1000:
        raise HTTPException(
            status_code=400,
            detail=f"Região muito grande para sprite sheet. Total de tiles: {tiles_count} (máximo: 1000)"
        )
    
    sprite_id = f"{layer}_{zoom}_{x_min}_{y_min}_{x_max}_{y_max}_{'_'.join(map(str, years))}"
    
    # Adiciona geração em background
    background_tasks.add_task(
        _generate_sprite_sheet,
        sprite_id, layer, x_min, y_min, x_max, y_max, zoom, years
    )
    
    return SpriteSheetResponse(
        sprite_id=sprite_id,
        status="generating",
        tiles_count=tiles_count,
        estimated_size_mb=tiles_count * 0.1
    )

async def _generate_sprite_sheet(
    sprite_id: str,
    layer: str,
    x_min: int, y_min: int,
    x_max: int, y_max: int,
    zoom: int,
    years: List[int]
):
    """Gera sprite sheet em background"""
    logger.info(f"Gerando sprite sheet {sprite_id}")
    
    # Aqui implementaria a geração real
    # Salvaria no S3 para download posterior
    
    await tile_cache.set_meta(f"sprite/{sprite_id}", {
        "status": "completed",
        "url": f"/sprites/{sprite_id}.png",
        "generated_at": datetime.now().isoformat()
    })