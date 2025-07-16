"""
WebSocket para streaming eficiente de tiles
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status, HTTPException
from typing import Dict, List, Set, Any, Optional
from pydantic import BaseModel, Field
import asyncio
import json
from datetime import datetime
from app.config import logger
from app.request_queue import request_queue

router = APIRouter(prefix="/ws", tags=["WebSocket"])

class TileStreamManager:
    """Gerencia conexões WebSocket para streaming de tiles"""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.subscriptions: Dict[str, Set[str]] = {}  # client_id -> tile_ids
        
    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket
        self.subscriptions[client_id] = set()
        logger.info(f"Cliente {client_id} conectado via WebSocket")
        
    def disconnect(self, client_id: str):
        self.active_connections.pop(client_id, None)
        self.subscriptions.pop(client_id, None)
        logger.info(f"Cliente {client_id} desconectado")
        
    async def subscribe_tiles(self, client_id: str, tile_ids: List[str]):
        """Cliente se inscreve para receber tiles específicos"""
        if client_id in self.subscriptions:
            self.subscriptions[client_id].update(tile_ids)
            
    async def send_tile(self, client_id: str, tile_data: Dict):
        """Envia tile para cliente específico"""
        if client_id in self.active_connections:
            websocket = self.active_connections[client_id]
            try:
                await websocket.send_json(tile_data)
            except Exception as e:
                logger.error(f"Erro enviando tile para {client_id}: {e}")
                self.disconnect(client_id)

# Instância global
stream_manager = TileStreamManager()

@router.websocket("/tiles/{client_id}")
async def websocket_tiles(websocket: WebSocket, client_id: str):
    """
    WebSocket endpoint para streaming de tiles
    
    Protocolo:
    1. Cliente conecta e envia lista de tiles desejados
    2. Servidor envia tiles conforme ficam disponíveis
    3. Priorização inteligente baseada em viewport
    """
    await stream_manager.connect(websocket, client_id)
    
    try:
        while True:
            # Recebe mensagem do cliente
            data = await websocket.receive_json()
            
            if data["type"] == "subscribe":
                # Cliente quer receber tiles específicos
                tiles = data["tiles"]  # Lista de {x, y, z, year, layer}
                
                # Adiciona à fila com priorização
                for idx, tile in enumerate(tiles):
                    priority = data.get("priorities", {}).get(
                        f"{tile['x']}_{tile['y']}_{tile['z']}_{tile['year']}", 
                        5
                    )
                    
                    await request_queue.add_request(
                        request_id=f"{client_id}_{idx}",
                        url=f"/api/layers/{tile['layer']}/{tile['x']}/{tile['y']}/{tile['z']}",
                        priority=priority,
                        metadata={
                            "client_id": client_id,
                            "tile": tile,
                            "year": tile.get("year")
                        }
                    )
                
                await websocket.send_json({
                    "type": "subscribed",
                    "count": len(tiles),
                    "status": "processing"
                })
                
            elif data["type"] == "prioritize":
                # Cliente mudou viewport/ano, re-prioriza
                target_year = data.get("year")
                if target_year:
                    await request_queue.reprioritize_by_year(target_year)
                    
            elif data["type"] == "cancel":
                # Cliente não precisa mais de certos tiles
                tile_ids = data.get("tile_ids", [])
                # Implementar cancelamento
                
            elif data["type"] == "stats":
                # Cliente quer estatísticas
                stats = request_queue.get_stats()
                await websocket.send_json({
                    "type": "stats",
                    "data": stats
                })
                
    except WebSocketDisconnect:
        stream_manager.disconnect(client_id)
    except Exception as e:
        logger.error(f"Erro no WebSocket {client_id}: {e}")
        stream_manager.disconnect(client_id)

# Modelos para documentação do endpoint batch
class TileBatchItem(BaseModel):
    """Item de tile para processamento em batch"""
    x: int = Field(..., description="Coordenada X do tile")
    y: int = Field(..., description="Coordenada Y do tile")
    z: int = Field(..., description="Nível de zoom")
    year: int = Field(..., description="Ano dos dados")
    layer: str = Field(..., description="Camada de dados")
    in_viewport: bool = Field(False, description="Se o tile está visível no viewport")
    url: str = Field(None, description="URL do tile (gerada automaticamente)")

class BatchStrategy(BaseModel):
    """Estratégia de processamento de batch"""
    strategy: str = Field(
        "viewport_optimized",
        description="Estratégia de otimização",
        enum=["viewport_optimized", "temporal_sequence", "spatial_cluster"]
    )

class BatchRequest(BaseModel):
    """Requisição para processar batch de tiles"""
    tiles: List[TileBatchItem] = Field(..., description="Lista de tiles para processar")
    strategy: str = Field(
        "viewport_optimized",
        description="Estratégia de processamento"
    )

class BatchResponse(BaseModel):
    """Resposta do processamento em batch"""
    batch_id: str = Field(..., description="ID único do batch")
    tiles_count: int = Field(..., description="Total de tiles no batch")
    strategy: str = Field(..., description="Estratégia aplicada")
    estimated_time_seconds: float = Field(..., description="Tempo estimado de processamento")

@router.post(
    "/batch",
    response_model=BatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Processa batch de tiles",
    description="""
    Processa um conjunto de tiles em batch com estratégia de otimização específica.
    
    **Estratégias Disponíveis:**
    
    1. **viewport_optimized** (padrão):
       - Prioriza tiles visíveis no viewport
       - Tiles com `in_viewport=true` têm prioridade máxima
       - Ideal para carregamento inicial de mapa
    
    2. **temporal_sequence**:
       - Otimiza para animação temporal
       - Ordena tiles por ano para pré-carregar sequência
       - Ideal para visualizações animadas
    
    3. **spatial_cluster**:
       - Agrupa tiles espacialmente próximos
       - Carrega em espiral do centro para as bordas
       - Ideal para exploração gradual de área
    
    **Processo:**
    1. Tiles são organizados conforme estratégia
    2. Adicionados à fila de processamento
    3. Cliente pode monitorar progresso via WebSocket
    """,
    responses={
        202: {
            "description": "Batch aceito para processamento",
            "content": {
                "application/json": {
                    "example": {
                        "batch_id": "batch_1702345678.123456",
                        "tiles_count": 150,
                        "strategy": "viewport_optimized",
                        "estimated_time_seconds": 15.0
                    }
                }
            }
        },
        400: {
            "description": "Parâmetros inválidos",
            "content": {
                "application/json": {
                    "example": {"detail": "Estratégia inválida: unknown_strategy"}
                }
            }
        }
    }
)
async def stream_batch_tiles(
    body: BatchRequest
):
    """
    Processa batch de tiles com estratégia específica
    
    Estratégias:
    - viewport_optimized: Prioriza tiles visíveis
    - temporal_sequence: Otimiza para animação temporal
    - spatial_cluster: Agrupa tiles espacialmente próximos
    """
    # Extrai dados do body
    tiles = body.tiles
    strategy = body.strategy
    
    # Valida estratégia
    strategies_map = {
        "viewport_optimized": _viewport_strategy,
        "temporal_sequence": _temporal_strategy,
        "spatial_cluster": _spatial_strategy
    }
    
    if strategy not in strategies_map:
        raise HTTPException(
            status_code=400,
            detail=f"Estratégia inválida: {strategy}"
        )
    
    # Converte tiles para dicts para processamento
    tiles_dict = [tile.dict() for tile in tiles]
    
    # Aplica estratégia
    strategy_func = strategies_map[strategy]
    optimized_tiles = strategy_func(tiles_dict)
    
    # Adiciona à fila de processamento
    batch_id = f"batch_{datetime.now().timestamp()}"
    
    for idx, tile in enumerate(optimized_tiles):
        # Gera URL se não fornecida
        if not tile.get("url"):
            tile["url"] = f"/api/layers/{tile['layer']}/{tile['x']}/{tile['y']}/{tile['z']}?year={tile['year']}"
        
        await request_queue.add_request(
            request_id=f"{batch_id}_{idx}",
            url=tile["url"],
            priority=tile.get("priority", 5),
            metadata=tile
        )
    
    return BatchResponse(
        batch_id=batch_id,
        tiles_count=len(optimized_tiles),
        strategy=strategy,
        estimated_time_seconds=len(optimized_tiles) * 0.1
    )

def _viewport_strategy(tiles: List[Dict]) -> List[Dict]:
    """Prioriza tiles no viewport atual"""
    # Tiles com viewport=true têm prioridade máxima
    for tile in tiles:
        if tile.get("in_viewport", False):
            tile["priority"] = 0
        else:
            tile["priority"] = 5
    return sorted(tiles, key=lambda t: t["priority"])

def _temporal_strategy(tiles: List[Dict]) -> List[Dict]:
    """Otimiza para sequência temporal (animação)"""
    # Ordena por ano para pre-carregar sequência
    return sorted(tiles, key=lambda t: (t.get("year", 9999), t.get("x", 0), t.get("y", 0)))

def _spatial_strategy(tiles: List[Dict]) -> List[Dict]:
    """Agrupa tiles espacialmente próximos"""
    # Ordena por proximidade espacial (espiral do centro)
    if not tiles:
        return tiles
        
    # Encontra centro
    avg_x = sum(t.get("x", 0) for t in tiles) / len(tiles)
    avg_y = sum(t.get("y", 0) for t in tiles) / len(tiles)
    
    # Ordena por distância do centro
    for tile in tiles:
        dx = tile.get("x", 0) - avg_x
        dy = tile.get("y", 0) - avg_y
        tile["priority"] = int((dx*dx + dy*dy) ** 0.5)
        
    return sorted(tiles, key=lambda t: t["priority"])