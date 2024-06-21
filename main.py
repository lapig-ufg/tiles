import typing

from app.utils.capabilities import CAPABILITIES
import ee
import orjson
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from google.oauth2 import service_account
import valkey

from app.config import settings, logger, start_logger
from app.database import Base, engine
from app.router import created_routes


Base.metadata.create_all(bind=engine)


class ORJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content: typing.Any) -> bytes:
        return orjson.dumps(content)


app = FastAPI(default_response_class=ORJSONResponse)


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
    
    app.state.valkey = valkey.Valkey(host='valkey', port=6379)
    
@app.on_event("shutdown")
async def shutdown_event():
    app.state.valkey.close()


@app.get("/")
def read_root():
    return {"message": "Welcome to the GEE FastAPI"}


@app.get('/api/capabilities')
def get_capabilities():
    return CAPABILITIES


app = created_routes(app)
