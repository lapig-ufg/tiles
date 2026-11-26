from importlib import import_module

__all__ = ['HybridTileCache', 'tile_cache', 'CacheWarmer']


def __getattr__(name):
    if name in ('HybridTileCache', 'tile_cache'):
        return getattr(import_module('.cache_hybrid', __name__), name)
    if name == 'CacheWarmer':
        return getattr(import_module('.cache_warmer', __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
