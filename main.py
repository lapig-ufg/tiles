import ee
import typing
import orjson
from google.oauth2 import service_account
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from app.config import settings
from app.database import engine, Base

from app.router import created_routes

Base.metadata.create_all(bind=engine)

class ORJSONResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content: typing.Any) -> bytes:
        return orjson.dumps(content)


app = FastAPI(default_response_class=ORJSONResponse)


@app.on_event("startup")
def initialize_gee():
    try:
        service_account_file = settings.GEE_SERVICE_ACCOUNT_FILE
        credentials = service_account.Credentials.from_service_account_file(
            service_account_file,
            scopes=['https://www.googleapis.com/auth/earthengine.readonly']
        )
        ee.Initialize(credentials)
        
        print("GEE Initialized successfully.")
    except Exception as e:
        
        raise HTTPException(status_code=500, detail="Failed to initialize GEE")
    
    
@app.get("/")
def read_root():
    return {"message": "Welcome to the GEE FastAPI"}

app = created_routes(app)


