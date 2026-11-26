from fastapi import FastAPI
from app.modules.bdc.router import create_router


def create_app(service=None):
    app = FastAPI(title="Laboratório BDC")
    app.include_router(create_router(service), prefix="/api/bdc")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8766)
