from .api import layers, timeseries


def created_routes(app):

    app.include_router(layers.router, prefix="/api/layers", tags=["Layers"])
    app.include_router(timeseries.router, prefix="/api/timeseries", tags=["Timeseries"])

    return app
