"""
Sistema de fila de requisições com priorização e throttling
"""
import asyncio
import heapq
from datetime import datetime
from typing import Dict, Any

from app.core.config import logger


class PriorityRequestQueue:
    """
    Fila de requisições com priorização inteligente
    """
    def __init__(self, max_concurrent: int = 100, requests_per_second: int = 1000):
        self.max_concurrent = max_concurrent
        self.requests_per_second = requests_per_second
        self.queue = []  # Heap priority queue
        self.active_requests = {}
        self.request_counter = 0
        self._lock = asyncio.Lock()
        
    async def add_request(
        self,
        request_id: str,
        url: str,
        priority: int = 5,  # 0 = máxima prioridade
        metadata: Dict[str, Any] = None
    ) -> str:
        """Adiciona requisição à fila com prioridade"""
        async with self._lock:
            self.request_counter += 1
            
            item = {
                "id": request_id,
                "url": url,
                "priority": priority,
                "sequence": self.request_counter,  # Para desempate
                "metadata": metadata or {},
                "added_at": datetime.now(),
                "status": "queued"
            }
            
            # Adiciona à heap (priority, sequence, item)
            heapq.heappush(self.queue, (priority, self.request_counter, item))
            
        return request_id
    
    async def process_queue(self):
        """Processa fila respeitando limites de concorrência"""
        while True:
            async with self._lock:
                # Remove requisições concluídas
                completed = [k for k, v in self.active_requests.items() 
                           if v["status"] in ["completed", "failed"]]
                for req_id in completed:
                    del self.active_requests[req_id]
                
                # Adiciona novas requisições se houver espaço
                while len(self.active_requests) < self.max_concurrent and self.queue:
                    _, _, item = heapq.heappop(self.queue)
                    self.active_requests[item["id"]] = item
                    
                    # Cria task para processar requisição
                    asyncio.create_task(self._process_request(item))
            
            # Throttling
            await asyncio.sleep(1.0 / (self.requests_per_second / self.max_concurrent))
    
    async def _process_request(self, item: Dict[str, Any]):
        """Processa uma requisição individual"""
        try:
            item["status"] = "processing"
            item["started_at"] = datetime.now()
            
            # Aqui faria a requisição real
            # Por agora, simula com sleep
            await asyncio.sleep(0.1)
            
            item["status"] = "completed"
            item["completed_at"] = datetime.now()
            
        except Exception as e:
            item["status"] = "failed"
            item["error"] = str(e)
            logger.error(f"Erro processando requisição {item['id']}: {e}")
    
    async def reprioritize_by_year(self, target_year: int):
        """Re-prioriza requisições baseado no ano alvo"""
        async with self._lock:
            new_queue = []
            
            while self.queue:
                priority, seq, item = heapq.heappop(self.queue)
                
                # Ajusta prioridade baseado na proximidade do ano
                if "year" in item["metadata"]:
                    year_diff = abs(item["metadata"]["year"] - target_year)
                    new_priority = min(year_diff, 10)  # Cap em 10
                else:
                    new_priority = priority
                
                heapq.heappush(new_queue, (new_priority, seq, item))
            
            self.queue = new_queue
            logger.info(f"Re-priorizadas {len(self.queue)} requisições para ano {target_year}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas da fila"""
        return {
            "queued": len(self.queue),
            "active": len(self.active_requests),
            "completed": sum(1 for r in self.active_requests.values() 
                           if r["status"] == "completed"),
            "failed": sum(1 for r in self.active_requests.values() 
                         if r["status"] == "failed")
        }

# Instância global
request_queue = PriorityRequestQueue()

# Inicia processamento automático quando o módulo é importado
async def start_queue_processing():
    """Inicia o processamento da fila em background"""
    asyncio.create_task(request_queue.process_queue())

# Se executado diretamente
if __name__ == "__main__":
    asyncio.run(start_queue_processing())