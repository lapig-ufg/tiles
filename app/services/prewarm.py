"""
Sistema de pre-warming de tiles para popular cache antecipadamente
Essencial para suportar milhões de requisições por segundo
"""
import asyncio
import math
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

import aiohttp
from loguru import logger

from app.services.tile import latlon_to_tile
from app.core.config import settings


class TilePreWarmer:
    """
    Pré-aquece tiles populares para reduzir latência inicial
    """
    
    def __init__(self, base_url: str = "http://localhost:80"):
        self.base_url = base_url
        self.session: Optional[aiohttp.ClientSession] = None
        self.concurrent_requests = 50  # Ajustar baseado na capacidade
        self.semaphore = asyncio.Semaphore(self.concurrent_requests)
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=100)
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    def _get_tile_bounds_for_bbox(
        self,
        bbox: Dict[str, float],
        zoom: int
    ) -> Tuple[int, int, int, int]:
        """Calcula os tiles necessários para cobrir um bounding box"""
        x_min, y_max = latlon_to_tile(bbox["south"], bbox["west"], zoom)
        x_max, y_min = latlon_to_tile(bbox["north"], bbox["east"], zoom)
        return x_min, y_min, x_max, y_max
    
    async def _warm_single_tile(
        self,
        layer: str,
        x: int,
        y: int,
        z: int,
        params: Dict[str, str]
    ) -> bool:
        """Aquece um único tile"""
        async with self.semaphore:
            url = f"{self.base_url}/api/layers/{layer}/{x}/{y}/{z}"
            try:
                async with self.session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        # Lê o conteúdo para garantir download completo
                        await response.read()
                        return True
                    else:
                        logger.warning(f"Falha ao aquecer tile {url}: {response.status}")
                        return False
            except Exception as e:
                logger.error(f"Erro ao aquecer tile {url}: {e}")
                return False
    
    async def warm_region(
        self,
        layer: str,
        bbox: Dict[str, float],
        zoom_levels: List[int],
        params: Dict[str, str],
        progress_callback=None
    ) -> Dict[str, int]:
        """
        Aquece todos os tiles de uma região
        
        Args:
            layer: Nome da camada (s2_harmonized, landsat)
            bbox: {"west": -73.9, "south": -33.7, "east": -34.8, "north": 5.3}
            zoom_levels: Lista de níveis de zoom [10, 11, 12]
            params: Parâmetros da requisição (period, year, month, visparam)
            progress_callback: Função para reportar progresso
        
        Returns:
            Estatísticas do aquecimento
        """
        stats = {"total": 0, "success": 0, "failed": 0}
        tasks = []
        
        for zoom in zoom_levels:
            x_min, y_min, x_max, y_max = self._get_tile_bounds_for_bbox(bbox, zoom)
            
            # Calcula total de tiles para este zoom
            tiles_count = (x_max - x_min + 1) * (y_max - y_min + 1)
            logger.info(f"Zoom {zoom}: {tiles_count} tiles ({x_min},{y_min} até {x_max},{y_max})")
            
            for x in range(x_min, x_max + 1):
                for y in range(y_min, y_max + 1):
                    tasks.append(self._warm_single_tile(layer, x, y, zoom, params))
                    stats["total"] += 1
        
        # Processa em batches para não sobrecarregar
        batch_size = 100
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            results = await asyncio.gather(*batch, return_exceptions=True)
            
            # Contabiliza resultados
            for result in results:
                if isinstance(result, Exception):
                    stats["failed"] += 1
                elif result:
                    stats["success"] += 1
                else:
                    stats["failed"] += 1
            
            # Callback de progresso
            if progress_callback:
                progress = (i + len(batch)) / len(tasks) * 100
                await progress_callback(progress, stats)
            
            # Pequena pausa entre batches
            await asyncio.sleep(0.1)
        
        logger.info(f"Pre-warming concluído: {stats}")
        return stats
    
    async def warm_popular_regions(self):
        """Aquece regiões mais populares do Brasil"""
        popular_regions = [
            # Grandes capitais
            {"name": "São Paulo", "bbox": {"west": -47.0, "south": -24.0, "east": -46.0, "north": -23.0}},
            {"name": "Rio de Janeiro", "bbox": {"west": -43.8, "south": -23.1, "east": -42.8, "north": -22.7}},
            {"name": "Brasília", "bbox": {"west": -48.3, "south": -16.0, "east": -47.3, "north": -15.5}},
            {"name": "Belo Horizonte", "bbox": {"west": -44.1, "south": -20.1, "east": -43.8, "north": -19.7}},
            
            # Regiões agrícolas importantes
            {"name": "Mato Grosso", "bbox": {"west": -58.0, "south": -16.0, "east": -50.0, "north": -10.0}},
            {"name": "RS Pampa", "bbox": {"west": -57.0, "south": -33.0, "east": -53.0, "north": -30.0}},
        ]
        
        # Parâmetros padrão
        current_year = datetime.now().year
        current_month = datetime.now().month
        
        layers_params = [
            {
                "layer": "landsat",
                "params": {
                    "period": "MONTH",
                    "year": current_year,
                    "month": current_month,
                    "visparam": "landsat-tvi-false"
                }
            },
            {
                "layer": "s2_harmonized",
                "params": {
                    "period": "WET",
                    "year": current_year,
                    "visparam": "tvi-red"
                }
            }
        ]
        
        # Zoom levels prioritários
        priority_zooms = [10, 11, 12]  # Níveis mais usados
        
        total_stats = {"total": 0, "success": 0, "failed": 0}
        
        for region in popular_regions:
            logger.info(f"Aquecendo região: {region['name']}")
            
            for layer_config in layers_params:
                stats = await self.warm_region(
                    layer_config["layer"],
                    region["bbox"],
                    priority_zooms,
                    layer_config["params"]
                )
                
                # Acumula estatísticas
                for key in total_stats:
                    total_stats[key] += stats[key]
        
        return total_stats


# Função para executar pre-warming periodicamente
async def run_periodic_prewarm(interval_hours: int = 6):
    """Executa pre-warming periodicamente"""
    while True:
        try:
            logger.info("Iniciando pre-warming periódico...")
            async with TilePreWarmer() as warmer:
                stats = await warmer.warm_popular_regions()
                logger.info(f"Pre-warming periódico concluído: {stats}")
        except Exception as e:
            logger.error(f"Erro no pre-warming periódico: {e}")
        
        # Aguarda próximo ciclo
        await asyncio.sleep(interval_hours * 3600)


# CLI para pre-warming manual
if __name__ == "__main__":
    import sys
    
    async def main():
        if len(sys.argv) < 2:
            print("Uso: python prewarm.py [popular|custom]")
            sys.exit(1)
        
        command = sys.argv[1]
        
        async with TilePreWarmer() as warmer:
            if command == "popular":
                print("Aquecendo regiões populares...")
                stats = await warmer.warm_popular_regions()
                print(f"Concluído: {stats}")
            
            elif command == "custom" and len(sys.argv) >= 8:
                # python prewarm.py custom landsat -50 -20 -45 -15 10,11,12
                layer = sys.argv[2]
                bbox = {
                    "west": float(sys.argv[3]),
                    "south": float(sys.argv[4]),
                    "east": float(sys.argv[5]),
                    "north": float(sys.argv[6])
                }
                zooms = [int(z) for z in sys.argv[7].split(",")]
                
                params = {
                    "period": "MONTH",
                    "year": datetime.now().year,
                    "month": datetime.now().month,
                    "visparam": "landsat-tvi-false" if layer == "landsat" else "tvi-red"
                }
                
                print(f"Aquecendo região customizada: {bbox}")
                stats = await warmer.warm_region(layer, bbox, zooms, params)
                print(f"Concluído: {stats}")
            else:
                print("Parâmetros inválidos")
                sys.exit(1)
    
    asyncio.run(main())