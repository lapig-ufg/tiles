import uuid

import fakeredis
import pytest
from celery import Celery

import app.tasks.cache_operations as operations


@pytest.fixture
def broker(monkeypatch):
    name = "warm-tests-" + uuid.uuid4().hex
    app = Celery(name, broker="memory://", backend="cache+memory://")
    app.conf.task_default_queue = name

    @app.task(name=name + ".tile")
    def tile(**parameters):
        return parameters

    monkeypatch.setattr(operations, "cache_warm_tile", tile)
    monkeypatch.setattr(operations, "celery_app", app)
    server = fakeredis.FakeServer()
    monkeypatch.setattr(operations, '_warm_job_redis',
                        lambda: fakeredis.FakeRedis(server=server), raising=False)
    with app.connection_for_read() as connection:
        queue = connection.SimpleQueue(name)
        yield app, queue
        queue.clear()
        queue.close()
    app.close()


def tile(x, period="WET", month=1):
    return {"x": x, "y": 34, "z": 10, "endpoint": "landsat",
            "params": {"period": period, "year": 2020, "month": month,
                       "visparam": "landsat-tvi-false", "compositeMode": "BEST_IMAGE"}}


def test_pre_cache_publica_somente_a_primeira_janela(broker):
    _, queue = broker
    result = operations.cache_warm_from_tiles_json.run([tile(x) for x in range(5)], batch_size=2)
    assert result["total_tiles"] == 5
    assert result["job_id"]
    assert queue.qsize() == 2


def test_pre_cache_deduplica_periodos_sazonais_equivalentes(broker):
    _, queue = broker
    result = operations.cache_warm_from_tiles_json.run([tile(12, month=1), tile(12, month=9), tile(13)], batch_size=50)
    assert result["total_tiles"] == 2
    assert queue.qsize() == 2


def test_pre_cache_preserva_produtos_mensais_distintos(broker):
    _, queue = broker
    result = operations.cache_warm_from_tiles_json.run([tile(12, "MONTH", 1), tile(12, "MONTH", 9)], batch_size=50)
    assert result["total_tiles"] == 2
    assert queue.qsize() == 2


@pytest.mark.parametrize("size", [0, -1, 1.5, True])
def test_pre_cache_rejeita_janela_invalida_antes_de_publicar(broker, size):
    _, queue = broker
    with pytest.raises(ValueError):
        operations.cache_warm_from_tiles_json.run([tile(12)], batch_size=size)
    assert queue.qsize() == 0


def test_pre_cache_vazio_nao_cria_trabalho(broker):
    _, queue = broker
    result = operations.cache_warm_from_tiles_json.run([], batch_size=2)
    assert result["total_tiles"] == 0
    assert result["job_id"] is None
    assert queue.qsize() == 0


def test_pre_cache_callback_duplicado_nao_reconta_nem_publica_outra_janela(broker):
    app, queue = broker
    started = operations.cache_warm_from_tiles_json.run([tile(x) for x in range(4)], batch_size=2)
    for _ in range(2):
        queue.get(block=False).ack()
    first = [{'status': 'failed', 'tile': 'tile-0', 'reason': 'cache_warm_failed'},
             {'status': 'cached', 'tile': 'tile-1'}]
    operations.cache_warm_batch_finished.run(first, started['job_id'], 0)
    assert queue.qsize() == 2
    for _ in range(2):
        queue.get(block=False).ack()
    operations.cache_warm_batch_finished.run(first, started['job_id'], 0)
    assert queue.qsize() == 0
    operations.cache_warm_batch_finished.run(
        [{'status': 'cached'}, {'status': 'already_cached'}], started['job_id'], 2,
    )
    summary = app.AsyncResult(started['job_id']).result
    assert summary['processed'] == 4
    assert summary['cached'] == 2
    assert summary['already_cached'] == 1
    assert summary['failed'] == 1
    assert summary['errors'][0]['tile'] == 'tile-0'


@pytest.mark.parametrize('count', [600, 1000])
def test_pre_cache_mantem_payload_da_janela_limitado(count):
    import json
    import subprocess
    import sys
    from pathlib import Path

    script = r"""
import json
import os
import resource
import sys
import fakeredis
from celery import Celery
import app.tasks.cache_operations as operations

app = Celery('payload-test', broker='memory://', backend='cache+memory://')
app.conf.task_default_queue = 'payload-test'
@app.task(name='payload-test.tile')
def tile(**parameters):
    return parameters
operations.celery_app = app
operations.cache_warm_tile = tile
server = fakeredis.FakeServer()
operations._warm_job_redis = lambda: fakeredis.FakeRedis(server=server)
with open('/proc/self/statm') as statm:
    virtual = int(statm.read().split()[0]) * os.sysconf('SC_PAGE_SIZE')
ceiling = virtual + 128 * 1024 * 1024
resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
tiles = [{'x': x, 'y': 34, 'z': 10, 'endpoint': 'landsat',
          'params': {'period': 'WET', 'year': 2020, 'month': 1,
                     'visparam': 'landsat-tvi-false', 'compositeMode': 'BEST_IMAGE'}}
         for x in range(int(sys.argv[1]))]
operations.cache_warm_from_tiles_json.run(tiles, batch_size=10)
with app.connection_for_read() as connection:
    queue = connection.SimpleQueue('payload-test')
    sizes = []
    while queue.qsize():
        message = queue.get(block=False)
        sizes.append(len(message.body))
        message.ack()
    queue.close()
app.close()
print(json.dumps(sizes))
"""
    result = subprocess.run(
        [sys.executable, '-c', script, str(count)], text=True, capture_output=True,
        cwd=Path(__file__).resolve().parents[2], timeout=20,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    sizes = json.loads(result.stdout)
    assert len(sizes) == 10
    assert sum(sizes) < 100_000
    assert max(sizes) < 10_000
