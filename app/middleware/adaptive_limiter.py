"""
Rate limiter adaptativo que ajusta limites baseado na carga do sistema
"""
import time
from typing import Dict

import psutil
from slowapi import Limiter
from slowapi.util import get_remote_address


class AdaptiveLimiter:
    def __init__(self):
        self.limiter = Limiter(key_func=get_remote_address)
        self.base_limits = {
            "tiles": 50000,
            "landsat": 25000,
            "sentinel": 25000,
            "timeseries": 5000
        }
        self.current_limits = self.base_limits.copy()
        self.last_check = time.time()
        
    def get_system_load(self) -> Dict[str, float]:
        """Retorna métricas do sistema"""
        return {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "memory_percent": psutil.virtual_memory().percent,
            "load_avg": psutil.getloadavg()[0] if hasattr(psutil, 'getloadavg') else 0
        }
    
    def adjust_limits(self):
        """Ajusta limites baseado na carga do sistema"""
        now = time.time()
        if now - self.last_check < 30:  # Checa a cada 30 segundos
            return
            
        self.last_check = now
        metrics = self.get_system_load()
        
        # Fator de ajuste baseado na carga
        cpu_factor = 1.0
        memory_factor = 1.0
        
        if metrics["cpu_percent"] > 80:
            cpu_factor = 0.5  # Reduz pela metade
        elif metrics["cpu_percent"] > 60:
            cpu_factor = 0.7
        elif metrics["cpu_percent"] < 30:
            cpu_factor = 1.5  # Aumenta 50%
            
        if metrics["memory_percent"] > 85:
            memory_factor = 0.3  # Reduz drasticamente
        elif metrics["memory_percent"] > 70:
            memory_factor = 0.6
        elif metrics["memory_percent"] < 50:
            memory_factor = 1.2
            
        # Aplica o fator mais restritivo
        adjustment_factor = min(cpu_factor, memory_factor)
        
        # Ajusta limites
        for key, base_limit in self.base_limits.items():
            new_limit = int(base_limit * adjustment_factor)
            self.current_limits[key] = max(new_limit, 100)  # Mínimo de 100
    
    def get_limit(self, endpoint_type: str) -> str:
        """Retorna limite atual para o tipo de endpoint"""
        self.adjust_limits()
        limit = self.current_limits.get(endpoint_type, 1000)
        return f"{limit}/minute"
    
    def limit_endpoint(self, endpoint_type: str):
        """Decorator para aplicar rate limiting adaptativo"""
        def decorator(func):
            return self.limiter.limit(self.get_limit(endpoint_type))(func)
        return decorator

# Instância global
adaptive_limiter = AdaptiveLimiter()