from .api import layers, timeseries, tile_aggregation, cache, vis_params, capabilities, tasks, admin, imagery
from .modules.embedding_maps.router import router as embedding_maps_router


def created_routes(app):

    app.include_router(layers.router, prefix="/api/layers", tags=["Layers"])
    app.include_router(imagery.router, prefix="/api/imagery", tags=["Imagery"])
    app.include_router(timeseries.router, prefix="/api/timeseries", tags=["Timeseries"])
    
    # Rota para agregação de tiles - OCULTA DA DOCUMENTAÇÃO
    app.include_router(tile_aggregation.router, prefix="/api", tags=["Aggregation"], include_in_schema=False)
    
    # Rota unificada para gerenciamento de cache - OCULTA DA DOCUMENTAÇÃO
    app.include_router(cache.router, tags=["Cache Management"], include_in_schema=False)
    
    # Rota para gerenciamento de parâmetros de visualização - OCULTA DA DOCUMENTAÇÃO
    app.include_router(vis_params.router, tags=["Visualization Parameters"], include_in_schema=False)
    
    # Rota para capabilities dinâmicas
    app.include_router(capabilities.router, tags=["Capabilities"])
    
    # Rota para gerenciamento e monitoramento de tasks do Celery - OCULTA DA DOCUMENTAÇÃO
    app.include_router(tasks.router, tags=["Task Management"], include_in_schema=False)
    
    # Rota para administração - OCULTA DA DOCUMENTAÇÃO
    app.include_router(admin.router, tags=["Administration"], include_in_schema=False)

    # Embedding Maps - análise de embeddings de satélite
    app.include_router(embedding_maps_router, prefix="/api/embedding-maps", tags=["Embedding Maps"])

    return app
