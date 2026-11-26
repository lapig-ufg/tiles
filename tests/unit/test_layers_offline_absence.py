import io
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from tests.unit.test_layers_index_visualization import layers_module


@pytest.fixture
def serving(layers_module, monkeypatch):
    layers = layers_module
    monkeypatch.setattr(layers, '_check_capability', AsyncMock())
    monkeypatch.setattr(layers, '_vis_param', AsyncMock(return_value={}))
    monkeypatch.setattr(layers, 'tile2goehashBBOX', lambda *args: (
        'abc', {'w': -50, 's': -10, 'e': -49, 'n': -9}))
    monkeypatch.setattr(layers, 'get_png', AsyncMock(return_value=None))
    monkeypatch.setattr(layers, 'aget_absence', AsyncMock(return_value={
        'kind': 'empty_collection', 'count': 0, 'expires_at': 1_800_003_600}), raising=False)
    monkeypatch.setattr(layers, 'get_meta', AsyncMock(side_effect=AssertionError('Google não deve ser consultado')))
    return layers


async def body(response):
    if hasattr(response, 'body'):
        return response.body
    return b''.join([chunk async for chunk in response.body_iterator])


@pytest.mark.asyncio
async def test_ausencia_comprovada_retorna_png_transparente_sem_gerar(serving):
    response = await serving._serve_tile('landsat', 12, 34, 10, 'WET', 2020, 7,
                                         'landsat-tvi-false', None)
    assert response.status_code == 200
    assert response.headers['X-Tile-Status'] == 'no_data'
    assert response.headers['Cache-Control'] == 'no-store'
    with Image.open(io.BytesIO(await body(response))) as image:
        assert image.size == (256, 256)
        assert image.getchannel('A').getbbox() is None
    assert serving.aget_absence.call_args.args == (
        'landsat_WET_2020_0_landsat-tvi-false_BEST_IMAGE/abc/10/12_34.png',)


@pytest.mark.asyncio
async def test_png_existente_prevalece_sobre_ausencia(serving):
    png = encoded('green')
    serving.get_png.return_value = png
    response = await serving._serve_tile('landsat', 12, 34, 10, 'WET', 2020, 7,
                                         'landsat-tvi-false', None)
    assert await body(response) == png
    assert 'X-Tile-Status' not in response.headers


@pytest.mark.asyncio
async def test_ausencia_publicada_enquanto_aguarda_lock_e_reutilizada(serving):
    serving.aget_absence.side_effect = [None, {'kind': 'empty_collection', 'count': 0}]
    response = await serving._serve_tile('landsat', 12, 34, 10, 'WET', 2020, 7,
                                         'landsat-tvi-false', None)
    assert response.status_code == 200
    assert response.headers['X-Tile-Status'] == 'no_data'


@pytest.mark.asyncio
async def test_sem_ausencia_preserva_indisponibilidade_do_cache(serving, monkeypatch):
    serving.aget_absence.return_value = None

    @asynccontextmanager
    async def busy(*args):
        yield False

    monkeypatch.setattr(serving, 'tile_lock', busy)
    response = await serving._serve_tile('landsat', 12, 34, 10, 'WET', 2020, 7,
                                         'landsat-tvi-false', None)
    assert response.status_code == 503
    assert response.headers['X-Error-Reason'] == 'cache_busy'
    assert 'X-Tile-Status' not in response.headers


def encoded(color):
    output = io.BytesIO()
    Image.new('RGBA', (256, 256), color).save(output, format='PNG')
    return output.getvalue()


@pytest.mark.parametrize('invalid', [encoded('white'), encoded((0, 0, 0, 0)), b'broken-png'], ids=['white', 'transparent', 'corrupt'])
@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.asyncio
async def test_png_invalido_preexistente_nao_oculta_ausencia(serving, invalid, legacy):
    async def cached(key):
        return invalid if ('_2020_7_' in key) == legacy else None

    serving.get_png.side_effect = cached
    response = await serving._serve_tile('landsat', 12, 34, 10, 'WET', 2020, 7,
                                         'landsat-tvi-false', None)
    assert response.status_code == 200
    assert response.headers['X-Tile-Status'] == 'no_data'
    with Image.open(io.BytesIO(await body(response))) as image:
        assert image.getchannel('A').getbbox() is None


@pytest.mark.asyncio
async def test_legado_valido_prevalece_sobre_canonico_invalido_e_ausencia(serving):
    valid = encoded('green')

    async def cached(key):
        return encoded('white') if '_2020_0_' in key else valid

    serving.get_png.side_effect = cached
    response = await serving._serve_tile('landsat', 12, 34, 10, 'WET', 2020, 7,
                                         'landsat-tvi-false', None)
    assert await body(response) == valid
    assert 'X-Tile-Status' not in response.headers


@pytest.mark.asyncio
async def test_sem_evidencia_preserva_fluxo_de_png_existente(serving):
    serving.get_png.return_value = b'cached-image'
    serving.aget_absence.return_value = None
    response = await serving._serve_tile('landsat', 12, 34, 10, 'WET', 2020, 7,
                                         'landsat-tvi-false', None)
    assert await body(response) == b'cached-image'
    assert 'X-Tile-Status' not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_ausencia_supera_png_branco_retido_no_s3_e_memoria(serving, monkeypatch, legacy):
    from app.cache.cache_hybrid import HybridTileCache
    from tests.unit.test_cache_reliability import RedisStore, Storage

    cache = HybridTileCache(redis_url='redis://fake:6379')
    store, storage = RedisStore(), Storage()

    @asynccontextmanager
    async def connection():
        yield store

    monkeypatch.setattr(cache, '_get_redis', connection)
    cache._s3_client = storage
    month = 7 if legacy else 0
    key = f'landsat_WET_2020_{month}_landsat-tvi-false_BEST_IMAGE/abc/10/12_34.png'
    white = encoded('white')
    await cache.set_png(key, white, ttl=86400)
    assert await cache.get_png(key) == white
    monkeypatch.setattr(serving, 'get_png', cache.get_png)
    try:
        response = await serving._serve_tile('landsat', 12, 34, 10, 'WET', 2020, 7,
                                             'landsat-tvi-false', None)
        assert response.headers['X-Tile-Status'] == 'no_data'
        assert await cache.get_png(key) == white
        assert storage.objects[cache._generate_s3_key(key)]['Body'] == white
    finally:
        await cache.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy', [False, True])
async def test_png_valido_continua_disponivel_sem_redis_de_ausencia(serving, legacy):
    from app.cache.errors import TileLockUnavailable
    valid = encoded('green')

    async def cached(key):
        if legacy and '_2020_0_' in key:
            return encoded('white')
        return valid

    serving.get_png.side_effect = cached
    serving.aget_absence.side_effect = TileLockUnavailable('Redis indisponível')
    response = await serving._serve_tile('landsat', 12, 34, 10, 'WET', 2020, 7,
                                         'landsat-tvi-false', None)
    assert response.status_code == 200
    assert await body(response) == valid
    assert 'X-Tile-Status' not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize('png', [None, encoded('white'), encoded((0, 0, 0, 0)), b'corrupt'],
                         ids=['missing', 'white', 'transparent', 'corrupt'])
async def test_sem_png_valido_falha_do_redis_nao_simula_ausencia(serving, png):
    from app.cache.errors import TileLockUnavailable
    serving.get_png.return_value = png
    serving.aget_absence.side_effect = TileLockUnavailable('Redis indisponível')
    with pytest.raises(TileLockUnavailable):
        await serving._serve_tile('landsat', 12, 34, 10, 'WET', 2020, 7,
                                  'landsat-tvi-false', None)
    serving.get_meta.assert_not_awaited()


@pytest.mark.asyncio
async def test_falha_inesperada_do_png_nao_e_ocultada_pelo_fallback(serving):
    from app.cache.errors import TileLockUnavailable
    serving.get_png.side_effect = RuntimeError('Falha inesperada do armazenamento')
    serving.aget_absence.side_effect = TileLockUnavailable('Redis indisponível')
    with pytest.raises(RuntimeError, match='Falha inesperada do armazenamento'):
        await serving._serve_tile('landsat', 12, 34, 10, 'WET', 2020, 7,
                                  'landsat-tvi-false', None)
