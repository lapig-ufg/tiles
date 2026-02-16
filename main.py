import typing
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

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
from app.core.config import start_logger, REDIS_URL
from app.core.database import Base, engine
from app.router import created_routes
from app.utils.cors import origin_regex, allow_origins
from app.cache import tile_cache

Base.metadata.create_all(bind=engine)

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
    from app.core.gee_auth import initialize_earth_engine
    initialize_earth_engine()
    
    yield
    
    # Shutdown
    from app.core.mongodb import close_mongo_connection
    await close_mongo_connection()
    logger.info("MongoDB connection closed")

    await tile_cache.close()
    logger.info("Cache híbrido fechado")

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
        # Usa o cliente S3 do cache híbrido
        async with tile_cache.s3_session.client(
            's3',
            endpoint_url=tile_cache.s3_endpoint,
            aws_access_key_id=settings.get('S3_ACCESS_KEY', 'minioadmin'),
            aws_secret_access_key=settings.get('S3_SECRET_KEY', 'minioadmin'),
            use_ssl=settings.get("S3_USE_SSL",True),  # <-- ADICIONE ISSO
            verify=settings.get("S3_VERIFY_SSL", True) 
        ) as s3_client:
            # Tenta listar buckets para verificar conectividade
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
