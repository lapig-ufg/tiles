"""adelete_meta — invalida URL cacheada após 429 da SA. Sem isso,
workers continuam lendo URL acoplada à SA penalizada."""
from __future__ import annotations

import pytest

from app.cache import cache as cache_mod


@pytest.mark.asyncio
async def test_adelete_meta_is_async_callable():
    """A função existe e é assíncrona — caller usa `await adelete_meta(...)`."""
    import inspect
    assert hasattr(cache_mod, "adelete_meta")
    assert inspect.iscoroutinefunction(cache_mod.adelete_meta)
