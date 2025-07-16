"""
Sistema de batch processing para requisições Landsat/Sentinel
"""
import asyncio
from typing import List, Dict, Any
from datetime import datetime
import uuid
from app.config import logger

class BatchProcessor:
    def __init__(self, batch_size: int = 50, max_wait_time: float = 0.5):
        self.batch_size = batch_size
        self.max_wait_time = max_wait_time  # segundos
        self.pending_requests: Dict[str, List[Dict]] = {}
        self.processing_lock = asyncio.Lock()
        
    async def add_request(self, collection: str, params: Dict[str, Any]) -> str:
        """Adiciona requisição ao batch e retorna ID para tracking"""
        request_id = str(uuid.uuid4())
        
        async with self.processing_lock:
            if collection not in self.pending_requests:
                self.pending_requests[collection] = []
                # Agenda processamento após max_wait_time
                asyncio.create_task(self._process_batch_after_delay(collection))
            
            self.pending_requests[collection].append({
                "id": request_id,
                "params": params,
                "timestamp": datetime.now()
            })
            
            # Processa imediatamente se atingir batch_size
            if len(self.pending_requests[collection]) >= self.batch_size:
                asyncio.create_task(self._process_batch(collection))
        
        return request_id
    
    async def _process_batch_after_delay(self, collection: str):
        """Processa batch após delay máximo"""
        await asyncio.sleep(self.max_wait_time)
        await self._process_batch(collection)
    
    async def _process_batch(self, collection: str):
        """Processa um batch de requisições"""
        async with self.processing_lock:
            if collection not in self.pending_requests:
                return
                
            batch = self.pending_requests.pop(collection, [])
            if not batch:
                return
        
        logger.info(f"Processando batch de {len(batch)} requisições para {collection}")
        
        # Aqui você processaria as requisições em batch
        # Por exemplo, combinando bbox, otimizando queries GEE, etc.
        
        # Por agora, apenas simula processamento
        await asyncio.sleep(0.1)
        
        return batch

# Instância global
batch_processor = BatchProcessor()