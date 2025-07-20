from .api import layers, timeseries, tile_aggregation, cache, vis_params, capabilities, tasks, admin


def created_routes(app):

    app.include_router(layers.router, prefix="/api/layers", tags=["Layers"])
    app.include_router(timeseries.router, prefix="/api/timeseries", tags=["Timeseries"])
    
    # Rota para agregação de tiles
    app.include_router(tile_aggregation.router, prefix="/api", tags=["Aggregation"])
    
    # Rota unificada para gerenciamento de cache (inclui todos os endpoints de cache)
    app.include_router(cache.router, tags=["Cache Management"])
    
    # Rota para gerenciamento de parâmetros de visualização
    app.include_router(vis_params.router, tags=["Visualization Parameters"])
    
    # Rota para capabilities dinâmicas
    app.include_router(capabilities.router, tags=["Capabilities"])
    
    # Rota para gerenciamento e monitoramento de tasks do Celery
    app.include_router(tasks.router, tags=["Task Management"])
    
    # Rota para administração (limpar cache, corrigir dados, etc.)
    app.include_router(admin.router, tags=["Administration"])

    return app
