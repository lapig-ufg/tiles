"""Cobre o helper que orquestra rotação de SA + invalidação de cache +
regeneração de URL em 429 do download de tile.

Caminho feliz: passthrough simples para http_get_bytes.
Caminho de erro: 429 → rotate + delete_meta + url_factory + retry único.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.http import EarthEngineRateLimitedError


@pytest.fixture
def fake_manager():
    mgr = MagicMock()
    mgr.current_sa_name = "sa-old@proj"
    mgr.rotate_on_429 = MagicMock(
        side_effect=lambda *_a, **_k: setattr(mgr, "current_sa_name", "sa-new@proj")
    )
    return mgr


@pytest.mark.asyncio
async def test_happy_path_no_rotation(fake_manager):
    """200 no primeiro shot — não rotaciona, não invalida cache, não regenera."""
    from app.utils import ee_tile_fetch

    url_factory = AsyncMock()

    with patch.object(ee_tile_fetch, "http_get_bytes", AsyncMock(return_value=b"PNG")) as fake_get, \
         patch.object(ee_tile_fetch, "adelete_meta", AsyncMock()) as fake_del, \
         patch.object(ee_tile_fetch, "aset_meta", AsyncMock()) as fake_set, \
         patch.object(ee_tile_fetch, "get_gee_manager", return_value=fake_manager):

        result = await ee_tile_fetch.fetch_tile_with_rotation(
            cache_key="landsat_MONTH_2007_7_landsat-tvi-false/abc",
            cached_url="https://ee.googleapis.com/.../{z}/{x}/{y}",
            url_factory=url_factory,
            x=1, y=2, z=10,
            layer="landsat",
        )

    assert result == b"PNG"
    fake_get.assert_awaited_once()
    url_factory.assert_not_called()
    fake_del.assert_not_called()
    fake_set.assert_not_called()
    fake_manager.rotate_on_429.assert_not_called()


@pytest.mark.asyncio
async def test_429_triggers_full_rotation_cycle(fake_manager):
    """429 na primeira chamada → rotate + delete_meta + url_factory + retry."""
    from app.utils import ee_tile_fetch

    url_factory = AsyncMock(return_value="https://ee.googleapis.com/.../new/{z}/{x}/{y}")

    # Primeira chamada lança 429; segunda devolve bytes.
    fake_get = AsyncMock(side_effect=[
        EarthEngineRateLimitedError("rate limited", sa_name="sa-old@proj"),
        b"PNG-NEW",
    ])

    with patch.object(ee_tile_fetch, "http_get_bytes", fake_get), \
         patch.object(ee_tile_fetch, "adelete_meta", AsyncMock()) as fake_del, \
         patch.object(ee_tile_fetch, "aset_meta", AsyncMock()) as fake_set, \
         patch.object(ee_tile_fetch, "get_gee_manager", return_value=fake_manager):

        result = await ee_tile_fetch.fetch_tile_with_rotation(
            cache_key="landsat_MONTH_2007_7_landsat-tvi-false/abc",
            cached_url="https://ee.googleapis.com/.../old/{z}/{x}/{y}",
            url_factory=url_factory,
            x=1, y=2, z=10,
            layer="landsat",
        )

    assert result == b"PNG-NEW"
    assert fake_get.await_count == 2
    fake_manager.rotate_on_429.assert_called_once()
    fake_del.assert_awaited_once_with("landsat_MONTH_2007_7_landsat-tvi-false/abc")
    url_factory.assert_awaited_once()
    fake_set.assert_awaited_once()


@pytest.mark.asyncio
async def test_persistent_429_raises_after_one_regeneration(fake_manager):
    """Se o retry após regeneração também devolve 429, propaga a exceção
    em vez de tentar uma terceira rodada — getMapId é caro e amplificar
    custa mais que entregar 503."""
    from app.utils import ee_tile_fetch

    url_factory = AsyncMock(return_value="https://ee.googleapis.com/.../new/{z}/{x}/{y}")

    fake_get = AsyncMock(side_effect=[
        EarthEngineRateLimitedError("rate limited", sa_name="sa-old@proj"),
        EarthEngineRateLimitedError("rate limited again", sa_name="sa-new@proj"),
    ])

    with patch.object(ee_tile_fetch, "http_get_bytes", fake_get), \
         patch.object(ee_tile_fetch, "adelete_meta", AsyncMock()), \
         patch.object(ee_tile_fetch, "aset_meta", AsyncMock()), \
         patch.object(ee_tile_fetch, "get_gee_manager", return_value=fake_manager):

        with pytest.raises(EarthEngineRateLimitedError):
            await ee_tile_fetch.fetch_tile_with_rotation(
                cache_key="landsat_MONTH_2007_7_landsat-tvi-false/abc",
                cached_url="https://ee.googleapis.com/.../old/{z}/{x}/{y}",
                url_factory=url_factory,
                x=1, y=2, z=10,
                layer="landsat",
            )

    assert fake_get.await_count == 2
    fake_manager.rotate_on_429.assert_called_once()  # rotação única


@pytest.mark.asyncio
async def test_url_factory_failure_propagates(fake_manager):
    """Se url_factory falha durante regeneração, propaga limpo — sem deixar
    cache em estado inconsistente. delete_meta já foi chamado, mas isso é
    aceitável (idempotente; próximo request regenera)."""
    from app.utils import ee_tile_fetch

    factory_exc = RuntimeError("getMapId failed: project quota exhausted")
    url_factory = AsyncMock(side_effect=factory_exc)

    fake_get = AsyncMock(side_effect=EarthEngineRateLimitedError("rate limited"))

    with patch.object(ee_tile_fetch, "http_get_bytes", fake_get), \
         patch.object(ee_tile_fetch, "adelete_meta", AsyncMock()), \
         patch.object(ee_tile_fetch, "aset_meta", AsyncMock()), \
         patch.object(ee_tile_fetch, "get_gee_manager", return_value=fake_manager):

        with pytest.raises(RuntimeError, match="getMapId failed"):
            await ee_tile_fetch.fetch_tile_with_rotation(
                cache_key="landsat_MONTH_2007_7/abc",
                cached_url="https://ee.googleapis.com/.../old/{z}/{x}/{y}",
                url_factory=url_factory,
                x=1, y=2, z=10,
                layer="landsat",
            )
