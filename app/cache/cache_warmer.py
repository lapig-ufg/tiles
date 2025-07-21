"""
Sistema inteligente de cache warming para tiles
Simula padrões de requisições de webmaps (Leaflet/OpenLayers)
"""
import math
import random
from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple, Dict, Any

from celery import group
from loguru import logger

from app.tasks.celery_app import celery_app


class LoadingPattern(Enum):
    """Padrões de carregamento de tiles em webmaps"""
    SPIRAL = "spiral"  # Carrega do centro para fora (padrão Leaflet)
    GRID = "grid"  # Carrega em grade (padrão OpenLayers)
    VIEWPORT = "viewport"  # Carrega apenas tiles visíveis
    PREDICTIVE = "predictive"  # Carrega tiles com base em movimento


@dataclass
class TileRequest:
    """Representa uma requisição de tile"""
    z: int
    x: int
    y: int
    layer: str
    params: Dict[str, Any]
    priority: int = 1
    pattern: LoadingPattern = LoadingPattern.SPIRAL


@dataclass
class ViewportBounds:
    """Limites de um viewport"""
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    zoom: int


class TileCoordGenerator:
    """Gera coordenadas de tiles baseado em diferentes estratégias"""
    
    @staticmethod
    def lat_lon_to_tile(lat: float, lon: float, zoom: int) -> Tuple[int, int]:
        """Converte lat/lon para coordenadas de tile"""
        lat_rad = math.radians(lat)
        n = 2.0 ** zoom
        x = int((lon + 180.0) / 360.0 * n)
        y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
        return (x, y)
    
    @staticmethod
    def tile_to_lat_lon(x: int, y: int, z: int) -> Tuple[float, float]:
        """Converte coordenadas de tile para lat/lon (canto superior esquerdo)"""
        n = 2.0 ** z
        lon = x / n * 360.0 - 180.0
        lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
        lat = math.degrees(lat_rad)
        return (lat, lon)
    
    @staticmethod
    def get_tiles_in_viewport(bounds: ViewportBounds) -> List[Tuple[int, int]]:
        """Retorna todos os tiles dentro de um viewport"""
        min_x, max_y = TileCoordGenerator.lat_lon_to_tile(
            bounds.min_lat, bounds.min_lon, bounds.zoom
        )
        max_x, min_y = TileCoordGenerator.lat_lon_to_tile(
            bounds.max_lat, bounds.max_lon, bounds.zoom
        )
        
        tiles = []
        for x in range(min_x, max_x + 1):
            for y in range(min_y, max_y + 1):
                tiles.append((x, y))
        
        return tiles
    
    @staticmethod
    def generate_spiral_tiles(center_x: int, center_y: int, radius: int) -> List[Tuple[int, int]]:
        """Gera tiles em padrão espiral (Leaflet-like)"""
        tiles = [(center_x, center_y)]
        
        for r in range(1, radius + 1):
            # Cima
            for x in range(center_x - r, center_x + r + 1):
                tiles.append((x, center_y - r))
            
            # Direita
            for y in range(center_y - r + 1, center_y + r + 1):
                tiles.append((center_x + r, y))
            
            # Baixo
            for x in range(center_x + r - 1, center_x - r - 1, -1):
                tiles.append((x, center_y + r))
            
            # Esquerda
            for y in range(center_y + r - 1, center_y - r, -1):
                tiles.append((center_x - r, y))
        
        return tiles
    
    @staticmethod
    def generate_grid_tiles(bounds: ViewportBounds, buffer_tiles: int = 2) -> List[Tuple[int, int]]:
        """Gera tiles em grade com buffer (OpenLayers-like)"""
        tiles = TileCoordGenerator.get_tiles_in_viewport(bounds)
        
        if buffer_tiles > 0:
            # Adiciona tiles de buffer ao redor
            min_x = min(t[0] for t in tiles) - buffer_tiles
            max_x = max(t[0] for t in tiles) + buffer_tiles
            min_y = min(t[1] for t in tiles) - buffer_tiles
            max_y = max(t[1] for t in tiles) + buffer_tiles
            
            buffered_tiles = []
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    buffered_tiles.append((x, y))
            
            return buffered_tiles
        
        return tiles


class CacheWarmer:
    """Sistema inteligente de cache warming"""
    
    def __init__(self):
        self.popular_regions = self._load_popular_regions()
        self.zoom_priorities = self._get_zoom_priorities()
    
    def _load_popular_regions(self) -> List[ViewportBounds]:
        """Carrega regiões populares para priorização"""
        # Exemplos de regiões brasileiras importantes
        return [
            ViewportBounds(-23.5505, -23.4205, -46.7333, -46.5333, 10),  # São Paulo
            ViewportBounds(-22.9068, -22.7468, -43.2096, -43.1096, 10),  # Rio de Janeiro
            ViewportBounds(-15.7942, -15.6342, -47.8822, -47.7222, 10),  # Brasília
            ViewportBounds(-12.9714, -12.8114, -38.5014, -38.3414, 10),  # Salvador
            ViewportBounds(-30.0346, -29.8746, -51.2177, -51.0577, 10),  # Porto Alegre
        ]
    
    def _get_zoom_priorities(self) -> Dict[int, int]:
        """Define prioridades por nível de zoom"""
        return {
            # Zooms mais comuns têm prioridade maior
            10: 10,
            11: 9,
            12: 8,
            13: 7,
            14: 6,
            9: 5,
            15: 4,
            8: 3,
            16: 2,
            7: 1,
        }
    
    def generate_warmup_tasks(
        self,
        layer: str,
        params: Dict[str, Any],
        patterns: List[LoadingPattern] = None,
        max_tiles: int = 1000
    ) -> List[TileRequest]:
        """Gera tasks de warmup inteligentes"""
        if patterns is None:
            patterns = [LoadingPattern.SPIRAL, LoadingPattern.GRID]
        
        tile_requests = []
        
        # 1. Tiles de regiões populares
        for region in self.popular_regions:
            for zoom in range(region.zoom - 2, region.zoom + 3):
                if zoom not in self.zoom_priorities:
                    continue
                
                bounds = ViewportBounds(
                    region.min_lat, region.max_lat,
                    region.min_lon, region.max_lon,
                    zoom
                )
                
                # Alterna entre padrões
                pattern = random.choice(patterns)
                
                if pattern == LoadingPattern.SPIRAL:
                    center_lat = (bounds.min_lat + bounds.max_lat) / 2
                    center_lon = (bounds.min_lon + bounds.max_lon) / 2
                    center_x, center_y = TileCoordGenerator.lat_lon_to_tile(
                        center_lat, center_lon, zoom
                    )
                    tiles = TileCoordGenerator.generate_spiral_tiles(center_x, center_y, 5)
                else:
                    tiles = TileCoordGenerator.generate_grid_tiles(bounds, buffer_tiles=1)
                
                for x, y in tiles[:max_tiles // len(self.popular_regions)]:
                    tile_requests.append(TileRequest(
                        z=zoom,
                        x=x,
                        y=y,
                        layer=layer,
                        params=params,
                        priority=self.zoom_priorities.get(zoom, 1),
                        pattern=pattern
                    ))
        
        # 2. Tiles aleatórios para cobertura geral
        remaining_tiles = max_tiles - len(tile_requests)
        if remaining_tiles > 0:
            tile_requests.extend(self._generate_random_tiles(
                layer, params, remaining_tiles
            ))
        
        # Ordena por prioridade
        tile_requests.sort(key=lambda t: t.priority, reverse=True)
        
        return tile_requests
    
    def _generate_random_tiles(
        self,
        layer: str,
        params: Dict[str, Any],
        count: int
    ) -> List[TileRequest]:
        """Gera tiles aleatórios para cobertura"""
        tiles = []
        
        # Foca no Brasil
        brazil_bounds = ViewportBounds(-33.0, 5.0, -74.0, -34.0, 10)
        
        for _ in range(count):
            zoom = random.choices(
                list(self.zoom_priorities.keys()),
                weights=list(self.zoom_priorities.values()),
                k=1
            )[0]
            
            lat = random.uniform(brazil_bounds.min_lat, brazil_bounds.max_lat)
            lon = random.uniform(brazil_bounds.min_lon, brazil_bounds.max_lon)
            
            x, y = TileCoordGenerator.lat_lon_to_tile(lat, lon, zoom)
            
            tiles.append(TileRequest(
                z=zoom,
                x=x,
                y=y,
                layer=layer,
                params=params,
                priority=self.zoom_priorities.get(zoom, 1),
                pattern=LoadingPattern.VIEWPORT
            ))
        
        return tiles


@celery_app.task(name='cache_warmer.warm_tiles')
def warm_tiles_task(tile_requests: List[Dict[str, Any]]):
    """Task Celery para aquecer cache de tiles"""
    from app.api.layers import process_tile_data
    
    results = []
    for req in tile_requests:
        try:
            # Simula requisição de tile
            tile_data = process_tile_data(
                req['layer'],
                req['z'],
                req['x'],
                req['y'],
                req['params']
            )
            
            results.append({
                'status': 'success',
                'tile': f"{req['z']}/{req['x']}/{req['y']}",
                'cached': True
            })
            
        except Exception as e:
            logger.error(f"Erro ao aquecer tile {req['z']}/{req['x']}/{req['y']}: {e}")
            results.append({
                'status': 'error',
                'tile': f"{req['z']}/{req['x']}/{req['y']}",
                'error': str(e)
            })
    
    return results


@celery_app.task(name='cache_warmer.schedule_warmup')
def schedule_warmup_task(
    layer: str,
    params: Dict[str, Any],
    max_tiles: int = 500,
    batch_size: int = 50
):
    """Agenda aquecimento de cache em lotes"""
    warmer = CacheWarmer()
    
    # Gera requisições de tiles
    tile_requests = warmer.generate_warmup_tasks(
        layer=layer,
        params=params,
        max_tiles=max_tiles
    )
    
    # Converte para dicts para serialização
    tile_dicts = [
        {
            'z': t.z,
            'x': t.x,
            'y': t.y,
            'layer': t.layer,
            'params': t.params,
            'priority': t.priority,
            'pattern': t.pattern.value
        }
        for t in tile_requests
    ]
    
    # Divide em lotes
    batches = [
        tile_dicts[i:i + batch_size]
        for i in range(0, len(tile_dicts), batch_size)
    ]
    
    # Cria grupo de tasks
    job = group(warm_tiles_task.s(batch) for batch in batches)
    
    # Executa
    result = job.apply_async()
    
    return {
        'total_tiles': len(tile_requests),
        'batches': len(batches),
        'task_id': result.id,
        'status': 'scheduled'
    }


@celery_app.task(name='cache_warmer.analyze_usage_patterns')
def analyze_usage_patterns_task(days: int = 7):
    """Analisa padrões de uso para otimizar cache warming"""
    # TODO: Implementar análise de logs/métricas para identificar:
    # - Tiles mais requisitados
    # - Horários de pico
    # - Padrões de navegação
    # - Zooms mais utilizados
    
    return {
        'status': 'not_implemented',
        'message': 'Análise de padrões será implementada com base em métricas'
    }

