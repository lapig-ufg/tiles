import typing

from app.utils.capabilities import CAPABILITIES
import ee
import orjson
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from google.oauth2 import service_account
import valkey
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings, logger, start_logger
from app.database import Base, engine
from app.router import created_routes
from app.utils.cors import origin_regex, allow_origins
from app.cache import close_cache

Base.metadata.create_all(bind=engine)

class ORJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content: typing.Any) -> bytes:
        return orjson.dumps(content)

app = FastAPI(default_response_class=ORJSONResponse)

# Configurações CORS com expressões regulares para subdomínios dinâmicos
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,  # Lista de origens estáticas (deixe vazio se estiver usando regex)
    allow_methods=["*"],  # Métodos permitidos
    allow_headers=["*"],  # Cabeçalhos permitidos
    allow_credentials=True,  # Permite o envio de cookies/credenciais
    allow_origin_regex=origin_regex,
    expose_headers=["X-Response-Time"],  # Cabeçalhos expostos
    max_age=3600,  # Tempo máximo para cache da resposta preflight
)

@app.on_event("startup")
async def startup_event():
    start_logger()
    try:
        service_account_file = settings.GEE_SERVICE_ACCOUNT_FILE
        logger.debug(f"Initializing service account {service_account_file}")
        credentials = service_account.Credentials.from_service_account_file(
            service_account_file,
            scopes=["https://www.googleapis.com/auth/earthengine.readonly"],
        )
        ee.Initialize(credentials)

        print("GEE Initialized successfully.")
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to initialize GEE")
    

@app.on_event("shutdown")
async def shutdown_event():
    close_cache()

@app.get("/")
def read_root():
    return {"message": "Welcome to the GEE FastAPI"}

@app.get('/api/capabilities')
def get_capabilities():
    return CAPABILITIES


app = created_routes(app)
