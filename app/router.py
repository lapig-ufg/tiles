from .api import layers, timeseries, tile_aggregation, cache_unified, vis_params_management, capabilities_endpoints


def created_routes(app):

    app.include_router(layers.router, prefix="/api/layers", tags=["Layers"])
    app.include_router(timeseries.router, prefix="/api/timeseries", tags=["Timeseries"])
    
    # Rota para agregação de tiles
    app.include_router(tile_aggregation.router, prefix="/api", tags=["Aggregation"])
    
    # Rota unificada para gerenciamento de cache (inclui todos os endpoints de cache)
    app.include_router(cache_unified.router, tags=["Cache Management"])
    
    # Rota para gerenciamento de parâmetros de visualização
    app.include_router(vis_params_management.router, tags=["Visualization Parameters"])
    
    # Rota para capabilities dinâmicas
    app.include_router(capabilities_endpoints.router, tags=["Capabilities"])

    return app
