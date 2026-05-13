import typing
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import ee
import orjson
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from google.oauth2 import service_account
import valkey
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

from app.core import settings, logger
from app.core.config import start_logger, REDIS_URL
from app.core.metrics import observe_request
from app.core.database import Base, engine
from app.router import created_routes
from app.utils.cors import origin_regex, allow_origins
from app.cache import tile_cache

# create_all com tratamento de race condition para multi-worker (uvicorn/gunicorn).
# Múltiplos workers podem tentar criar tabelas simultaneamente no mesmo SQLite.
try:
    Base.metadata.create_all(bind=engine)
except Exception:
    pass  # Tabelas já existem — criadas por outro worker
except Exception:
    pass  # Tabelas já existem — criadas por outro worker

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[f"{settings.get('RATE_LIMIT_PER_MINUTE', 100000)}/minute"],
    # Configuração específica para burst
    storage_uri=REDIS_URL,
    strategy="moving-window"  # Melhor para rajadas
)

class ORJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content: typing.Any) -> bytes:
        return orjson.dumps(content)

# Middleware simplificado para timing + métricas Prometheus.
class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        response = await call_next(request)

        duration = time.time() - start_time
        response.headers["X-Response-Time"] = f"{duration:.3f}"

        # Observa métrica de tile. `observe_request` ignora paths fora dos
        # layers (health, metrics, root) — sem label cardinality explosion.
        try:
            observe_request(
                path=request.url.path,
                status_code=response.status_code,
                error_reason=response.headers.get("X-Error-Reason"),
                duration_seconds=duration,
            )
        except Exception:  # nunca quebrar request por falha de métrica
            logger.exception("Falha ao observar métrica")

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
    
    # Inicializa GEE com SA do pool.
    # Com uvicorn --workers N, cada worker roda o lifespan independentemente,
    # então cada processo adquire uma SA distinta via Redis.
    # Com gunicorn, o post_fork (gunicorn_conf.py) também inicializa —
    # o guard interno (_manager is not None) evita dupla inicialização.
    from app.core.gee_auth import initialize_earth_engine
    initialize_earth_engine()

    yield
    
    # Shutdown
    from app.core.gee_auth import shutdown_earth_engine
    shutdown_earth_engine()
    logger.info("GEE manager encerrado")

    from app.core.mongodb import close_mongo_connection
    await close_mongo_connection()
    logger.info("MongoDB connection closed")

    await tile_cache.close()
    logger.info("Cache híbrido fechado")

    from app.utils.ee_compute import close_session as close_ee_session
    await close_ee_session()
    logger.info("EE compute session closed")

    from app.api.cog_proxy import shutdown_client
    await shutdown_client()
    logger.info("COG proxy client closed")

    from app.core.otel import shutdown_otel_logging
    shutdown_otel_logging()

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

# Request ID precisa vir ANTES do timing middleware para que a contextvar
# esteja setada quando observe_request() lê. ASGI middlewares executam
# em ordem inversa da adição — então este precisa ser o último add_middleware
# chamado entre os dois.
from app.middleware.request_id import RequestIdMiddleware

# Middleware de timing + métricas (inclui request_id no contexto via loguru)
app.add_middleware(TimingMiddleware)
app.add_middleware(RequestIdMiddleware)

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

from app.core.gee_pool import PoolExhaustedError


@app.exception_handler(PoolExhaustedError)
async def handle_pool_exhausted(request: Request, exc: PoolExhaustedError) -> Response:
    """Mapeia exaustão do pool de SAs para 503 com cabeçalho ``Retry-After``."""
    retry_after = max(1, int(exc.retry_after) + 1)
    logger.warning(
        f"PoolExhausted handler: 503 retry_after={retry_after}s "
        f"path={request.url.path}"
    )
    return Response(
        status_code=503,
        headers={
            "Retry-After": str(retry_after),
            "X-Error-Reason": "gee_pool_exhausted",
        },
    )


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Endpoint Prometheus. Exposto sem rate-limit para scraping interno.

    Em produção, proteger via network policy (Traefik restringe a rede do
    cluster Prometheus) — o próprio endpoint não valida origem.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/", include_in_schema=False)
@limiter.limit("100/minute")
async def read_root(request: Request):
    return {
        "message": "Tiles API - High Performance",
        "version": "2.0",
        "status": "operational",
        "docs": "/docs"
    }

@app.get("/health/light", include_in_schema=False)
async def health_light():
    """
    Lightweight health check endpoint for Traefik.
    Only performs basic connectivity checks without heavy operations.
    """
    try:
        # Basic Redis ping using the cache's connection manager
        async with tile_cache._get_redis() as r:
            await r.ping()
        
        return {
            "status": "healthy",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Light health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": "Service unavailable",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )

@app.get("/health", include_in_schema=False)
async def health_check():
    """Health check endpoint with comprehensive service validation"""
    health_status = {
        "status": "healthy",
        "services": {
            "cache": {"status": "unknown"},
            "mongodb": {"status": "unknown"},
            "gee": {"status": "unknown"},
            "s3": {"status": "unknown"}
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    errors = []
    
    # 1. Verificar cache (Redis/Valkey)
    try:
        stats = await tile_cache.get_stats()
        health_status["services"]["cache"] = {
            "status": "healthy",
            "redis_keys": stats["redis"]["total_keys"],
            "memory_usage": stats["redis"]["used_memory_human"]
        }
    except Exception as e:
        health_status["services"]["cache"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        errors.append(f"Cache: {str(e)}")
    
    # 2. Verificar MongoDB
    try:
        from app.core.mongodb import mongodb
        if mongodb.client is not None and mongodb.database is not None:
            # Tenta fazer um ping no MongoDB
            await mongodb.client.admin.command('ping')
            health_status["services"]["mongodb"] = {
                "status": "healthy",
                "database": mongodb.database.name
            }
        else:
            health_status["services"]["mongodb"] = {
                "status": "unhealthy",
                "error": "MongoDB client not initialized"
            }
            errors.append("MongoDB: Client not initialized")
    except Exception as e:
        health_status["services"]["mongodb"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        errors.append(f"MongoDB: {str(e)}")
    
    # 3. Verificar Google Earth Engine
    try:
        import ee
        if ee.data._credentials:
            # Tenta fazer uma operação simples para verificar se está funcionando
            test_image = ee.Image(1)
            test_image.getInfo()
            health_status["services"]["gee"] = {
                "status": "healthy",
                "initialized": True
            }
        else:
            health_status["services"]["gee"] = {
                "status": "unhealthy",
                "error": "GEE not initialized"
            }
            errors.append("GEE: Not initialized")
    except Exception as e:
        health_status["services"]["gee"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        errors.append(f"GEE: {str(e)}")
    
    # 4. Verificar S3/MinIO
    try:
        s3_client = await tile_cache._ensure_s3_client()
        response = await s3_client.list_buckets()
        bucket_exists = any(b['Name'] == tile_cache.s3_bucket for b in response['Buckets'])

        health_status["services"]["s3"] = {
            "status": "healthy",
            "endpoint": tile_cache.s3_endpoint,
            "bucket": tile_cache.s3_bucket,
            "bucket_exists": bucket_exists
        }
    except Exception as e:
        health_status["services"]["s3"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        errors.append(f"S3: {str(e)}")
    
    # Determinar status geral
    if errors:
        health_status["status"] = "unhealthy"
        health_status["errors"] = errors
        return JSONResponse(
            status_code=503,
            content=health_status
        )
    
    return health_status

app = created_routes(app)
