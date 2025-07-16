from .api import layers, timeseries, tile_aggregation, cache_management


def created_routes(app):

    app.include_router(layers.router, prefix="/api/layers", tags=["Layers"])
    app.include_router(timeseries.router, prefix="/api/timeseries", tags=["Timeseries"])
    
    # Rota para agregação de tiles
    app.include_router(tile_aggregation.router, prefix="/api", tags=["Aggregation"])
    
    # Rota para gerenciamento de cache
    app.include_router(cache_management.router, prefix="/api/cache", tags=["Cache Management"])

    return app
