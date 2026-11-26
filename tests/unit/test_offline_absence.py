import importlib
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import fakeredis
import pytest


KEY = 'landsat_WET_2020_0_landsat-tvi-false_BEST_IMAGE/abc/10/12_34.png'


@pytest.fixture
def absence_env(monkeypatch):
    module = importlib.import_module('app.cache.offline_absence')
    now = SimpleNamespace(value=1_800_000_000.0)
    monkeypatch.setattr(module.time, 'time', lambda: now.value)
    client = fakeredis.FakeRedis(server=fakeredis.FakeServer())
    lock = client.lock('lock:' + KEY, timeout=60, thread_local=False)
    lock.acquire()
    from app.cache.sync_lock import SyncTileLease
    lease = SyncTileLease(lock, KEY)
    evidence = {'kind': 'empty_collection', 'count': 0,
                'collection': 'LANDSAT/LC08/C02/T1_L2', 'period': 'WET', 'year': 2020,
                'checked_at': datetime.fromtimestamp(now.value, timezone.utc)}
    return module, client, lease, evidence, now


def test_ausencia_comprovada_compartilha_validade_sem_criar_png(absence_env):
    module, client, lease, evidence, now = absence_env
    marker = module.set_absence(client, KEY, evidence, lease=lease)
    assert marker['expires_at'] == now.value + 2_592_000
    assert module.get_absence(client, KEY) == marker
    assert 0 < client.ttl('absence:' + KEY) <= 2_592_000
    assert not client.exists('tile:' + KEY)


@pytest.mark.parametrize('change', [
    {'kind': 'timeout'}, {'kind': 'empty_collection', 'count': False},
    {'count': 1}, {'count': '0'}, {'checked_at': None},
    {'checked_at': datetime.fromtimestamp(1_797_407_999, timezone.utc)},
    {'checked_at': datetime.fromtimestamp(1_800_000_001, timezone.utc)},
])
def test_evidencia_invalida_nao_persiste_ausencia(absence_env, change):
    module, client, lease, evidence, _ = absence_env
    with pytest.raises(ValueError):
        module.set_absence(client, KEY, {**evidence, **change}, lease=lease)
    assert not client.exists('absence:' + KEY)


@pytest.mark.parametrize('ttl', [0, -1, 2_592_001, float('inf'), float('nan'), True])
def test_ttl_invalido_nao_persiste_ausencia(absence_env, ttl):
    module, client, lease, evidence, _ = absence_env
    with pytest.raises(ValueError):
        module.set_absence(client, KEY, evidence, lease=lease, ttl=ttl)


def test_ausencia_expirada_nao_e_reutilizada(absence_env):
    module, client, lease, evidence, now = absence_env
    module.set_absence(client, KEY, evidence, lease=lease, ttl=60)
    now.value += 61
    assert module.get_absence(client, KEY) is None


@pytest.mark.parametrize('raw', [b'[]', b'null', b'broken', b'x' * 2049,
    json.dumps({'kind': 'empty_collection', 'count': 0,
                'checked_at': 1_800_000_000, 'expires_at': 1_800_100_000}).encode(),
    json.dumps({'kind': 'empty_collection', 'count': False,
                'checked_at': 1_800_000_000, 'expires_at': 1_800_000_060}).encode(),
])
def test_marcador_corrompido_e_ignorado(absence_env, raw):
    module, client, _, _, _ = absence_env
    client.set('absence:' + KEY, raw, ex=60)
    assert module.get_absence(client, KEY) is None


def test_regeneracao_invalida_ausencia_sob_mesmo_lock(absence_env):
    module, client, lease, evidence, _ = absence_env
    module.set_absence(client, KEY, evidence, lease=lease)
    module.clear_absence(client, KEY, lease=lease)
    assert module.get_absence(client, KEY) is None


def test_concessao_de_outro_tile_nao_pode_gravar_ou_remover(absence_env):
    module, client, lease, evidence, _ = absence_env
    module.set_absence(client, KEY, evidence, lease=lease)
    lease.key = 'other'
    with pytest.raises(ValueError):
        module.set_absence(client, KEY, evidence, lease=lease)
    with pytest.raises(ValueError):
        module.clear_absence(client, KEY, lease=lease)
    assert module.get_absence(client, KEY) is not None


def test_concessao_perdida_nao_pode_gravar_ou_remover(absence_env):
    module, client, lease, evidence, _ = absence_env
    module.set_absence(client, KEY, evidence, lease=lease)
    client.set('lock:' + KEY, b'another-owner')
    from app.cache.errors import TileLockUnavailable
    with pytest.raises(TileLockUnavailable):
        module.set_absence(client, KEY, evidence, lease=lease)
    with pytest.raises(TileLockUnavailable):
        module.clear_absence(client, KEY, lease=lease)
    assert module.get_absence(client, KEY) is not None


@pytest.mark.asyncio
async def test_leitura_async_consulta_o_marcador_do_worker(absence_env, monkeypatch):
    module, client, lease, evidence, _ = absence_env
    marker = module.set_absence(client, KEY, evidence, lease=lease)
    async_client = fakeredis.FakeAsyncRedis(server=client.connection_pool.connection_kwargs['server'])

    @asynccontextmanager
    async def connection():
        yield async_client

    from app.cache.cache_hybrid import tile_cache
    monkeypatch.setattr(tile_cache, '_get_redis', connection)
    assert await module.aget_absence(KEY) == marker
    await async_client.aclose()


def test_evidencia_reutilizada_nao_renova_validade_original(absence_env):
    module, client, lease, evidence, now = absence_env
    evidence['checked_at'] = datetime.fromtimestamp(now.value - 1_296_000, timezone.utc)
    marker = module.set_absence(client, KEY, evidence, lease=lease)
    assert marker['expires_at'] == 1_801_296_000
    assert 0 < client.ttl('absence:' + KEY) <= 1_296_000


def test_evidencia_excessiva_nao_e_persistida(absence_env):
    module, client, lease, evidence, _ = absence_env
    with pytest.raises(ValueError):
        module.set_absence(client, KEY, {**evidence, 'collection': 'x' * 2048}, lease=lease)
    assert not client.exists('absence:' + KEY)


@pytest.mark.parametrize('operation', ['set', 'clear'])
def test_perda_de_lock_entre_validacao_e_mutacao_nao_altera_marcador(absence_env, monkeypatch, operation):
    module, client, lease, evidence, _ = absence_env
    original = module.set_absence(client, KEY, evidence, lease=lease)
    monkeypatch.setattr(lease, 'ensure_owned', lambda: client.set('lock:' + KEY, b'another-owner'))
    from app.cache.errors import TileLockUnavailable
    with pytest.raises(TileLockUnavailable):
        if operation == 'set':
            module.set_absence(client, KEY, evidence, lease=lease, ttl=30)
        else:
            module.clear_absence(client, KEY, lease=lease)
    assert module.get_absence(client, KEY) == original


@pytest.mark.asyncio
async def test_indisponibilidade_redis_nao_e_convertida_em_ausencia(absence_env, monkeypatch):
    module, _, _, _, _ = absence_env
    import redis
    from app.cache.cache_hybrid import tile_cache
    from app.cache.errors import TileLockUnavailable

    @asynccontextmanager
    async def unavailable():
        raise redis.ConnectionError('unavailable')
        yield

    monkeypatch.setattr(tile_cache, '_get_redis', unavailable)
    with pytest.raises(TileLockUnavailable):
        await module.aget_absence(KEY)


def test_json_excessivamente_aninhado_e_ignorado(absence_env):
    module, client, _, _, _ = absence_env
    client.set('absence:' + KEY, '[' * 1000 + '0' + ']' * 1000)
    assert module.get_absence(client, KEY) is None


@pytest.mark.parametrize('change', [
    {'collection': ''}, {'collection': None}, {'collection': 'x' * 257},
    {'period': 'DRY'}, {'year': 2021}, {'year': '2020'}, {'period': None},
])
def test_evidencia_de_outro_produto_nao_pode_omitir_tile(absence_env, change):
    module, client, lease, evidence, _ = absence_env
    with pytest.raises(ValueError):
        module.set_absence(client, KEY, {**evidence, **change}, lease=lease)
    assert not client.exists('absence:' + KEY)


def test_ausencia_permanece_valida_durante_campanha_de_varios_dias(absence_env):
    module, client, lease, evidence, now = absence_env
    marker = module.set_absence(client, KEY, evidence, lease=lease)
    now.value += 7 * 86400
    assert module.get_absence(client, KEY) == marker
    now.value += 23 * 86400
    assert module.get_absence(client, KEY) is None


def test_marcador_nao_pode_prolongar_validade_alem_de_trinta_dias(absence_env):
    module, client, lease, evidence, now = absence_env
    marker = module.set_absence(client, KEY, evidence, lease=lease)
    marker['expires_at'] = now.value + 2_592_001
    client.set('absence:' + KEY, json.dumps(marker), ex=2_592_001)
    assert module.get_absence(client, KEY) is None
