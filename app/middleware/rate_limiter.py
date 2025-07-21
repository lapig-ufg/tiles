"""
Rate limiter customizado com diferentes limites por tipo de requisição
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

# Limiter principal com configurações específicas
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["5000/minute"]  # Limite padrão
)

# Limites específicos por tipo de operação
RATE_LIMITS = {
    # Tiles individuais - limite MUITO mais alto para suportar 35 anos * 12 meses * tiles
    "tiles": "100000/minute;10000/second",
    
    # Landsat/Sentinel - tiles individuais precisam de limite alto
    "landsat": "50000/minute;5000/second",
    "sentinel": "50000/minute;5000/second",
    
    # Timeseries - ainda mais pesado mas precisa processar múltiplos anos
    "timeseries": "10000/minute;1000/second",
    
    # Endpoints gerais
    "general": "5000/minute;500/second",
    
    # Health/metrics
    "health": "1000/minute"
}

def get_rate_limit(endpoint_type: str) -> str:
    """Retorna o rate limit apropriado para o tipo de endpoint"""
    return RATE_LIMITS.get(endpoint_type, RATE_LIMITS["general"])

# Decoradores específicos
def limit_tiles():
    """Rate limit para requisições de tiles"""
    return limiter.limit(RATE_LIMITS["tiles"])

def limit_landsat():
    """Rate limit para requisições Landsat"""
    return limiter.limit(RATE_LIMITS["landsat"])

def limit_sentinel():
    """Rate limit para requisições Sentinel"""
    return limiter.limit(RATE_LIMITS["sentinel"])

def limit_timeseries():
    """Rate limit para requisições de timeseries"""
    return limiter.limit(RATE_LIMITS["timeseries"])