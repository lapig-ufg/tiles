from __future__ import annotations

import asyncio
import io
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from app.cache import cache_hybrid
from app.cache.cache_hybrid import HybridTileCache


class RedisStore:
    def __init__(self):
        self.values = {}
        self.hashes = {}
        self.deadlines = {}

    def purge(self, key):
        if self.deadlines.get(key, float('inf')) <= time.time():
            self.values.pop(key, None)
            self.hashes.pop(key, None)
            self.deadlines.pop(key, None)

    async def hgetall(self, key):
        self.purge(key)
        return self.hashes.get(key, {})

    async def hset(self, key, mapping):
        self.hashes[key] = {str(k).encode(): str(v).encode() for k, v in mapping.items()}

    async def expire(self, key, ttl):
        self.deadlines[key] = time.time() + ttl
        return True

    async def pttl(self, key):
        self.purge(key)
        return int((self.deadlines[key] - time.time()) * 1000) if key in self.deadlines else -1

    async def set(self, key, value, nx=False, ex=None, px=None):
        self.purge(key)
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.deadlines[key] = time.time() + (px / 1000 if px else ex)
        return True

    async def get(self, key):
        self.purge(key)
        return self.values.get(key)

    async def exists(self, key):
        self.purge(key)
        return key in self.values

    async def delete(self, key):
        self.values.pop(key, None)
        self.hashes.pop(key, None)
        self.deadlines.pop(key, None)

    async def eval(self, script, numkeys, key, token, *args):
        self.purge(key)
        if self.values.get(key) != token:
            return 0
        if 'pexpire' in script.lower():
            self.deadlines[key] = time.time() + int(args[0]) / 1000
        else:
            await self.delete(key)
        return 1


class Body:
    def __init__(self, data):
        self.buffer = io.BytesIO(data)

    async def read(self, size=-1):
        return self.buffer.read(size)

    def close(self):
        pass


class Storage:
    def __init__(self):
        self.objects = {}
        self.put_count = 0
        self.get_count = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.failure = False
        self.active = 0
        self.max_active = 0

    async def put_object(self, **kwargs):
        self.put_count += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            await self.release.wait()
            if self.failure:
                raise OSError('armazenamento indisponível')
            self.objects[kwargs['Key']] = dict(kwargs)
        finally:
            self.active -= 1

    async def get_object(self, **kwargs):
        self.get_count += 1
        value = self.objects.get(kwargs['Key'])
        if value is None:
            raise ClientError({'Error': {'Code': 'NoSuchKey'}}, 'GetObject')
        return {'Body': Body(value['Body']), 'Metadata': value.get('Metadata', {}),
                'ContentLength': len(value['Body'])}


@pytest.fixture
def cache_env(monkeypatch):
    now = SimpleNamespace(value=1_800_000_000.0)
    monkeypatch.setattr(cache_hybrid.time, 'time', lambda: now.value)
    cache = HybridTileCache(redis_url='redis://fake:6379')
    store = RedisStore()
    storage = Storage()

    @asynccontextmanager
    async def connection():
        yield store

    monkeypatch.setattr(cache, '_get_redis', connection)
    cache._s3_client = storage
    return cache, store, storage, now


@pytest.mark.asyncio
async def test_leitura_preserva_validade_historica_apos_sete_dias(cache_env):
    cache, store, storage, now = cache_env
    await cache.set_png('historico', b'png', ttl=365 * 86400)
    now.value += 8 * 86400
    assert await cache.get_png('historico') == b'png'
    assert await store.pttl('tile:historico') == 357 * 86400 * 1000


@pytest.mark.asyncio
async def test_recupera_indice_ausente_somente_com_validade_do_objeto(cache_env):
    cache, store, storage, now = cache_env
    await cache.set_png('tile', b'png', ttl=30 * 86400)
    await store.delete('tile:tile')
    now.value += 8 * 86400
    assert await cache.get_png('tile') == b'png'
    assert await store.pttl('tile:tile') == 22 * 86400 * 1000


@pytest.mark.asyncio
async def test_cache_local_nao_prolonga_validade_do_png(cache_env):
    cache, store, storage, now = cache_env
    await cache.set_png('curto', b'png', ttl=2)
    now.value += 3
    assert await cache.get_png('curto') is None


@pytest.mark.asyncio
async def test_objeto_expirado_ou_legado_sem_indice_nao_e_recuperado(cache_env):
    cache, store, storage, now = cache_env
    storage.objects[cache._generate_s3_key('expirado')] = {
        'Body': b'old', 'Metadata': {'expires_at': str(now.value - 1)}}
    storage.objects[cache._generate_s3_key('legado')] = {'Body': b'old'}
    assert await cache.get_png('expirado') is None
    assert await cache.get_png('legado') is None
    assert storage.get_count == 2
    assert await cache.get_png('legado') is None
    assert storage.get_count == 2


@pytest.mark.asyncio
async def test_cache_local_limita_bytes_e_contabiliza_substituicao(cache_env):
    cache, store, storage, now = cache_env
    cache.local_cache_max_bytes = 6
    await cache.set_png('a', b'1234')
    await cache.set_png('b', b'5678')
    assert list(cache.local_cache) == ['b']
    await cache.set_png('b', b'xy')
    await cache.set_png('c', b'abcd')
    assert cache.local_cache_bytes == 6
    now.value += cache.LOCAL_CACHE_TTL + 1
    storage.objects.clear()
    assert await cache.get_png('b') is None
    assert cache.local_cache_bytes == 4


@pytest.mark.asyncio
async def test_uploads_pendentes_deduplicados_por_chave(cache_env):
    cache, store, storage, now = cache_env
    storage.release.clear()
    try:
        await cache.set_png('tile', b'png', background=True)
        await storage.started.wait()
        await cache.set_png('tile', b'png', background=True)
        await asyncio.sleep(0)
        assert storage.put_count == 1
        assert cache.pending_upload_bytes == 3
    finally:
        storage.release.set()
        await asyncio.gather(*cache._bg_tasks)
    assert cache.pending_upload_bytes == 0


@pytest.mark.asyncio
async def test_uploads_respeitam_orcamento_em_bytes_e_concorrencia(cache_env):
    cache, store, storage, now = cache_env
    cache.MAX_PENDING_BYTES = 8
    cache._upload_semaphore = asyncio.Semaphore(1)
    storage.release.clear()
    try:
        await cache.set_png('a', b'1234', background=True)
        await cache.set_png('b', b'5678', background=True)
        await cache.set_png('c', b'9012', background=True)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert storage.max_active == 1
        assert cache.pending_upload_bytes == 8
    finally:
        storage.release.set()
        await asyncio.gather(*cache._bg_tasks)
    assert len(storage.objects) == 2
    assert 'tile:c' not in store.hashes


@pytest.mark.asyncio
async def test_close_cancela_uploads_pendentes_apos_prazo(cache_env):
    cache, store, storage, now = cache_env
    cache.CLOSE_TIMEOUT = 0.01
    storage.release.clear()
    await cache.set_png('tile', b'png', background=True)
    await storage.started.wait()
    try:
        await asyncio.wait_for(cache.close(), 0.2)
        assert not cache._bg_tasks
        assert cache.pending_upload_bytes == 0
        assert storage.active == 0
        assert not store.hashes
    finally:
        storage.release.set()
        await asyncio.gather(*cache._bg_tasks)


@pytest.mark.asyncio
async def test_lock_antigo_nao_libera_proprietario_subsequente(cache_env):
    cache, store, storage, now = cache_env
    async with cache.tile_lock('tile') as acquired:
        assert acquired
        now.value += cache.LOCK_TTL + 1
        await store.set('lock:tile', 'outro-proprietario', nx=True, ex=60)
    assert await store.get('lock:tile') == 'outro-proprietario'


@pytest.mark.asyncio
async def test_lock_aguarda_e_adquire_posse_antes_de_liberar_geracao(cache_env):
    cache, store, storage, now = cache_env
    cache.LOCK_POLL_INTERVAL = 0.001
    await store.set('lock:tile', 'proprietario', nx=True, ex=60)

    async def release():
        await asyncio.sleep(0.01)
        await store.delete('lock:tile')

    task = asyncio.create_task(release())
    async with cache.tile_lock('tile') as acquired:
        assert acquired is True
        assert await store.get('lock:tile') is not None
    await task


@pytest.mark.asyncio
async def test_lock_saturado_nao_permite_geracao_sem_posse(cache_env):
    cache, store, storage, now = cache_env
    cache.LOCK_WAIT_TIMEOUT = 0.01
    cache.LOCK_POLL_INTERVAL = 0.001
    cache.LOCK_TTL = 0
    await store.set('lock:tile', 'proprietario', nx=True, ex=60)
    with pytest.raises(TimeoutError):
        async with cache.tile_lock('tile'):
            pytest.fail('geração admitida sem posse')


@pytest.mark.asyncio
async def test_lock_renova_concessao_durante_geracao(cache_env):
    cache, store, storage, now = cache_env
    cache.LOCK_TTL = 0.06
    async with cache.tile_lock('tile'):
        deadline = store.deadlines['lock:tile']
        now.value += 0.03
        await asyncio.sleep(0.04)
        assert store.deadlines['lock:tile'] > deadline


@pytest.mark.asyncio
async def test_perda_da_concessao_interrompe_geracao(cache_env):
    cache, store, storage, now = cache_env
    cache.LOCK_TTL = 0.03
    with pytest.raises(TimeoutError):
        async with cache.tile_lock('tile'):
            await store.set('lock:tile', 'outro-proprietario', ex=60)
            await asyncio.sleep(0.05)
    assert await store.get('lock:tile') == 'outro-proprietario'


@pytest.mark.asyncio
async def test_close_libera_cache_local_e_nao_readmite_persistencia(cache_env):
    cache, store, storage, now = cache_env
    await cache.set_png('a', b'1234')
    await cache.close()
    await cache.set_png('b', b'5678', background=True)
    assert cache.local_cache_bytes == 0
    assert not cache.local_cache
    assert not cache._bg_tasks


@pytest.mark.asyncio
async def test_cancelamento_do_close_ainda_interrompe_uploads(cache_env):
    cache, store, storage, now = cache_env
    storage.release.clear()
    await cache.set_png('tile', b'png', background=True)
    await storage.started.wait()
    close = asyncio.create_task(cache.close())
    await asyncio.sleep(0)
    close.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await close
        assert storage.active == 0
        assert cache.pending_upload_bytes == 0
    finally:
        storage.release.set()
        await asyncio.gather(*cache._bg_tasks)


@pytest.mark.asyncio
async def test_inicializacao_concorrente_preserva_pool_unico(cache_env, monkeypatch):
    cache, store, storage, now = cache_env
    created = []

    def pool(*args, **kwargs):
        value = object()
        created.append(value)
        return value

    async def head_bucket(**kwargs):
        await asyncio.sleep(0)

    monkeypatch.setattr(cache_hybrid.redis.ConnectionPool, 'from_url', pool)
    monkeypatch.setattr(storage, 'head_bucket', head_bucket, raising=False)
    await asyncio.gather(cache.initialize(), cache.initialize())
    assert len(created) == 1


@pytest.mark.parametrize('name,value', [
    ('CACHE_UPLOAD_CONCURRENCY', 0), ('CACHE_READ_CONCURRENCY', -1),
    ('CACHE_IO_TIMEOUT', 0), ('CACHE_MAX_PENDING_BYTES', -1),
    ('LOCAL_CACHE_MAX_BYTES', -1), ('CACHE_MAX_TILE_BYTES', 0),
    ('CACHE_LOCK_WAIT_TIMEOUT', float('nan')),
])
def test_configuracao_invalida_falha_antes_de_admitir_trabalho(monkeypatch, name, value):
    settings = SimpleNamespace(get=lambda key, default=None: value if key == name else default)
    monkeypatch.setattr(cache_hybrid, 'settings', settings)
    with pytest.raises(ValueError):
        HybridTileCache()


@pytest.mark.asyncio
async def test_leitura_s3_reune_fragmentos_sem_truncar_png(cache_env, monkeypatch):
    cache, store, storage, now = cache_env
    await cache.set_png('tile', b'conteudo-completo', ttl=86400)
    now.value += 3601
    original = storage.get_object

    async def get_object(**kwargs):
        response = await original(**kwargs)
        body = response['Body']
        read = body.read

        async def fragmented(size=-1):
            return await read(min(size, 2))

        body.read = fragmented
        return response

    monkeypatch.setattr(storage, 'get_object', get_object)
    assert await cache.get_png('tile') == b'conteudo-completo'


@pytest.mark.asyncio
async def test_get_apos_close_nao_reabre_recursos(cache_env, monkeypatch):
    cache, store, storage, now = cache_env
    await cache.close()

    async def forbidden_client():
        pytest.fail('cliente S3 reaberto após encerramento')

    monkeypatch.setattr(cache, '_ensure_s3_client', forbidden_client)
    assert await cache.get_png('tile') is None


@pytest.mark.asyncio
async def test_cancelamento_antes_de_iniciar_upload_registra_desfecho(cache_env):
    from app.cache.metrics import cache_uploads_total

    cache, store, storage, now = cache_env
    before = cache_uploads_total.labels(result='cancelled')._value.get()
    await cache.set_png('tile', b'png', background=True)
    task = next(iter(cache._bg_tasks))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert cache.pending_upload_bytes == 0
    assert cache_uploads_total.labels(result='cancelled')._value.get() == before + 1


@pytest.mark.asyncio
async def test_legado_com_indice_valido_preserva_prazo_redis(cache_env):
    cache, store, storage, now = cache_env
    key = cache._generate_s3_key('legado')
    storage.objects[key] = {'Body': b'png'}
    await store.hset('tile:legado', {'s3_key': key})
    await store.expire('tile:legado', 120)
    assert await cache.get_png('legado') == b'png'
    assert await store.pttl('tile:legado') == 120000
    now.value += 121
    assert await cache.get_png('legado') is None


@pytest.mark.asyncio
async def test_falha_s3_libera_orcamento_e_registra_resultado(cache_env):
    from app.cache.metrics import cache_uploads_total, cache_uploads_active

    cache, store, storage, now = cache_env
    before = cache_uploads_total.labels(result='failed')._value.get()
    active = cache_uploads_active._value.get()
    storage.failure = True
    await cache.set_png('tile', b'png')
    assert not store.hashes
    assert cache.pending_upload_bytes == 0
    assert cache_uploads_active._value.get() == active
    assert cache_uploads_total.labels(result='failed')._value.get() == before + 1


@pytest.mark.asyncio
async def test_indice_indisponivel_nao_impede_recuperacao_de_objeto_valido(cache_env, monkeypatch):
    cache, store, storage, now = cache_env
    await cache.set_png('tile', b'png', ttl=86400)
    now.value += 3601

    @asynccontextmanager
    async def unavailable():
        raise ConnectionError('Redis indisponível')
        yield

    monkeypatch.setattr(cache, '_get_redis', unavailable)
    assert await cache.get_png('tile') == b'png'
    with pytest.raises(TimeoutError):
        async with cache.tile_lock('tile'):
            pytest.fail('geração admitida sem coordenador')


@pytest.mark.asyncio
async def test_cancelamento_do_proprietario_libera_lock(cache_env):
    cache, store, storage, now = cache_env
    entered = asyncio.Event()

    async def owner():
        async with cache.tile_lock('tile'):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(owner())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await store.get('lock:tile') is None


@pytest.mark.asyncio
@pytest.mark.parametrize('apply_before_cancel', [True, False])
async def test_cancelamento_durante_aquisicao_nao_deixa_lock_orfao(cache_env, monkeypatch, apply_before_cancel):
    cache, store, _, _ = cache_env
    original_set = store.set
    request_started = asyncio.Event()
    response_ready = asyncio.Event()
    entered = False

    async def delayed_set(*args, **kwargs):
        if apply_before_cancel:
            await original_set(*args, **kwargs)
        request_started.set()
        await response_ready.wait()
        if not apply_before_cancel:
            await original_set(*args, **kwargs)
        return True

    async def generate():
        nonlocal entered
        async with cache.tile_lock('tile'):
            entered = True

    monkeypatch.setattr(store, 'set', delayed_set)
    task = asyncio.create_task(generate())
    await request_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    response_ready.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)
    assert not entered
    assert await store.get('lock:tile') is None


@pytest.mark.asyncio
async def test_prazo_de_persistencia_inclui_espera_na_fila(cache_env):
    cache, store, storage, _ = cache_env
    cache.IO_TIMEOUT = .02
    cache._upload_semaphore = asyncio.Semaphore(0)
    await asyncio.wait_for(cache.set_png('fila', b'png', ttl=60), timeout=.3)
    assert storage.put_count == 0
    assert await store.hgetall('tile:fila') == {}
    assert cache.pending_upload_bytes == 0


@pytest.mark.asyncio
async def test_cancelamento_de_gravacao_sincrona_aguarda_persistencia(cache_env):
    cache, _, storage, _ = cache_env
    storage.release.clear()
    task = asyncio.create_task(cache.set_png('tile', b'png', ttl=60))
    try:
        await storage.started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        storage.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await cache.close()


@pytest.mark.asyncio
async def test_objeto_com_tamanho_excessivo_nao_e_carregado(cache_env):
    cache, store, storage, now = cache_env
    cache.MAX_TILE_BYTES = 2
    storage.objects[cache._generate_s3_key('tile')] = {
        'Body': b'png', 'Metadata': {'expires_at': str(now.value + 60)}}
    assert await cache.get_png('tile') is None
    assert not cache.local_cache


@pytest.mark.parametrize('ttl', [0, -1, float('nan'), float('inf')])
@pytest.mark.asyncio
async def test_validade_invalida_nao_admite_png(cache_env, ttl):
    cache, store, storage, now = cache_env
    await cache.set_png('tile', b'png', ttl=ttl)
    assert not cache.local_cache
    assert storage.put_count == 0


@pytest.mark.asyncio
async def test_fluxo_s3_incompleto_nao_retorna_png_parcial(cache_env, monkeypatch):
    cache, store, storage, now = cache_env

    async def incomplete(**kwargs):
        return {'Body': Body(b'png'), 'ContentLength': 100,
                'Metadata': {'expires_at': str(now.value + 60)}}

    monkeypatch.setattr(storage, 'get_object', incomplete)
    assert await cache.get_png('tile') is None


@pytest.mark.asyncio
async def test_close_nao_aguarda_indefinidamente_cancelamento_do_transporte(cache_env, monkeypatch):
    cache, store, storage, now = cache_env
    cache.CLOSE_TIMEOUT = 0.01
    cache.CANCEL_TIMEOUT = 0.01
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_cancellation(**kwargs):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

    monkeypatch.setattr(storage, 'put_object', delayed_cancellation)
    await cache.set_png('tile', b'png', background=True)
    await entered.wait()
    close = asyncio.create_task(cache.close())
    try:
        done, _ = await asyncio.wait({close}, timeout=0.1)
        assert close in done
    finally:
        release.set()
        await close
        await asyncio.gather(*cache._bg_tasks)


@pytest.mark.asyncio
async def test_reconexao_aguardando_mutex_nao_reabre_cliente_apos_close(cache_env, monkeypatch):
    cache, store, storage, now = cache_env
    cache._s3_client = None

    def forbidden_client(**kwargs):
        pytest.fail('reconexão S3 após encerramento')

    monkeypatch.setattr(cache.s3_session, 'client', forbidden_client)
    await cache._s3_lock.acquire()
    reconnect = asyncio.create_task(cache._ensure_s3_client())
    await asyncio.sleep(0)
    await cache.close()
    cache._s3_lock.release()
    with pytest.raises(RuntimeError):
        await reconnect


@pytest.mark.asyncio
async def test_expiracao_durante_reindexacao_nao_retorna_png_vencido(cache_env, monkeypatch):
    cache, store, storage, now = cache_env
    storage.objects[cache._generate_s3_key('tile')] = {
        'Body': b'png', 'Metadata': {'expires_at': str(now.value + 1)}}
    original = store.hset

    async def delayed_index(key, mapping):
        now.value += 2
        await original(key, mapping)

    monkeypatch.setattr(store, 'hset', delayed_index)
    assert await cache.get_png('tile') is None
    assert not cache.local_cache


@pytest.mark.asyncio
async def test_falha_no_fechamento_s3_ainda_fecha_pool_redis(cache_env):
    cache, store, storage, now = cache_env
    closed = []

    class Context:
        async def __aexit__(self, *args):
            raise OSError('falha no fechamento S3')

    class Pool:
        async def disconnect(self):
            closed.append(True)

    cache._s3_ctx = Context()
    cache._redis_pool = Pool()
    await cache.close()
    assert closed == [True]
