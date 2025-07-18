import typing
import time
from contextlib import asynccontextmanager

from app.utils.capabilities import get_capabilities_provider
import ee
import orjson
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from google.oauth2 import service_account
import valkey
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
# from prometheus_client import Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

from app.core import settings, logger
from app.core.config import start_logger
from app.core.database import Base, engine
from app.router import created_routes
from app.utils.cors import origin_regex, allow_origins
from app.cache import HybridTileCache

# Instância global do cache
tile_cache = HybridTileCache()

# Inicializa New Relic se estiver em produção
# try:
#     import newrelic.agent
#     newrelic.agent.initialize()
# except ImportError:
#     pass

Base.metadata.create_all(bind=engine)

# Métricas simples (sem Prometheus por enquanto)
# request_count = Counter(
#     'tiles_requests_total',
#     'Total de requisições',
#     ['method', 'endpoint', 'status']
# )
# request_duration = Histogram(
#     'tiles_request_duration_seconds',
#     'Duração das requisições em segundos',
#     ['method', 'endpoint']
# )

# Rate limiter - configuração adaptativa para Landsat/Sentinel
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.get('RATE_LIMIT_PER_MINUTE', 100000)}/minute"],
    # Configuração específica para burst
    storage_uri=settings.get('REDIS_URL', 'redis://valkey:6379'),
    strategy="moving-window"  # Melhor para rajadas
)

class ORJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content: typing.Any) -> bytes:
        return orjson.dumps(content)

# Middleware simplificado para timing
class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        response = await call_next(request)
        
        # Calcula duração
        duration = time.time() - start_time
        
        # Adiciona header de tempo de resposta
        response.headers["X-Response-Time"] = f"{duration:.3f}"
        
        return response

# Lifespan manager para inicialização assíncrona
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    start_logger()
    
    # Inicializa MongoDB
    try:
        from app.core.mongodb import connect_to_mongo
        await connect_to_mongo()
        logger.info("MongoDB connected successfully")
    except Exception as e:
        logger.error(f"Failed to connect to MongoDB: {e}")
        if settings.get("TILES_ENV") != "development":
            raise
        logger.warning("Running in development mode without MongoDB")
    
    # Inicializa cache híbrido
    await tile_cache.initialize()
    logger.info("Cache híbrido inicializado")
    
    # Inicializa Earth Engine
    if settings.get("SKIP_GEE_INIT", False):
        logger.warning("Skipping GEE initialization (SKIP_GEE_INIT=true)")
    else:
        try:
            service_account_file = settings.GEE_SERVICE_ACCOUNT_FILE
            logger.debug(f"Initializing service account {service_account_file}")
            credentials = service_account.Credentials.from_service_account_file(
                service_account_file,
                scopes=["https://www.googleapis.com/auth/earthengine.readonly"],
            )
            ee.Initialize(credentials)
            logger.info("GEE Initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize GEE: {e}")
            if settings.get("TILES_ENV") != "development":
                raise
            logger.warning("Running in development mode without GEE")
    
    yield
    
    # Shutdown
    from app.core.mongodb import close_mongo_connection
    await close_mongo_connection()
    logger.info("MongoDB connection closed")
    
    await tile_cache.close()
    logger.info("Cache híbrido fechado")

app = FastAPI(
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
    title="Tiles API - High Performance",
    version="2.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Adiciona rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Middleware de timing
app.add_middleware(TimingMiddleware)

# Proteção contra hosts não confiáveis
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["*"]  # Configurar hosts permitidos em produção
)

# Configurações CORS com expressões regulares para subdomínios dinâmicos
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
    allow_origin_regex=origin_regex,
    expose_headers=["X-Response-Time", "X-Cache-Status"],
    max_age=3600,
)

@app.get("/")
@limiter.limit("100/minute")
async def read_root(request: Request):
    return {
        "message": "Tiles API - High Performance",
        "version": "2.0",
        "status": "operational",
        "docs": "/docs"
    }

@app.get('/api/capabilities')
@limiter.limit("100/minute")
async def get_capabilities(request: Request):
    """Get dynamic capabilities based on available vis_params"""
    provider = get_capabilities_provider()
    capabilities = await provider.get_capabilities()
    
    # Return in legacy format for backward compatibility
    legacy_collections = []
    for coll in capabilities["collections"]:
        legacy_coll = {
            "name": coll["name"],
            "visparam": coll["visparam"],
            "period": coll.get("period", []),
            "year": coll.get("year", [])
        }
        if "months" in coll:
            legacy_coll["month"] = coll["months"]
        legacy_collections.append(legacy_coll)
    
    return {"collections": legacy_collections}

# Endpoint de métricas removido (Prometheus desabilitado)

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Verifica conexões
        stats = await tile_cache.get_stats()
        return {
            "status": "healthy",
            "cache": "connected",
            "redis_keys": stats["redis"]["total_keys"]
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )

app = created_routes(app)
