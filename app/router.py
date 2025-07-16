from .api import layers, timeseries, viewport, tile_aggregation, websocket_tiles


def created_routes(app):

    app.include_router(layers.router, prefix="/api/layers", tags=["Layers"])
    app.include_router(timeseries.router, prefix="/api/timeseries", tags=["Timeseries"])
    
    # Novas rotas para otimização de carregamento em grid
    app.include_router(viewport.router, prefix="/api", tags=["Viewport"])
    app.include_router(tile_aggregation.router, prefix="/api", tags=["Aggregation"])
    app.include_router(websocket_tiles.router, tags=["WebSocket"])

    return app
