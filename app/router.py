from .api import layers


def created_routes(app):

    app.include_router(layers.router, prefix="/api/layers", tags=["Layers"])

    return app
