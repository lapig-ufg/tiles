import hashlib
import importlib
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace
from urllib.error import HTTPError

import boto3
import fakeredis
import orjson
import pytest
import redis
from botocore.exceptions import ClientError
from PIL import Image

import app.services.tile


KEY = 'landsat_WET_2020_0_landsat-tvi-false_BEST_IMAGE/7ec/10/455_578.png'
PATH = 'landsat_WET_2020_0_landsat-tvi-false_BEST_IMAGE/7ec'
URL = 'https://earthengine.googleapis.com/v1/projects/test/maps/map/tiles/{z}/{x}/{y}'


class Storage:
    def __init__(self):
        self.objects = {}
        self.uploads = []
        self.before_upload = None
        self.failure = None
        self.closed = 0

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        value = self.objects[Key]
        return {'ContentLength': len(value['Body']), 'ContentType': value.get('ContentType', 'image/png'),
                'Metadata': value.get('Metadata', {})}

    def put_object(self, **kwargs):
        if self.before_upload:
            self.before_upload()
        if self.failure:
            raise self.failure
        self.uploads.append(kwargs)
        self.objects[kwargs['Key']] = kwargs

    def close(self):
        self.closed += 1


@pytest.fixture
def warming(monkeypatch):
    import ee
    import urllib.request
    from app.core import gee_admission
    from app.tasks import cache_operations
    import importlib
    offline_campaign = importlib.import_module('app.tasks.offline_campaign')
    source_absence_evidence = offline_campaign.source_absence_evidence
    monkeypatch.setattr(offline_campaign, 'source_absence_evidence', lambda params: None)

    def resolve_configuration(name, year):
        assert name == 'landsat-tvi-false'
        collection = 'LANDSAT/LC09/C02/T1_L2' if year >= 2022 else 'LANDSAT/LC08/C02/T1_L2'
        return collection, {
            'bands': ['SR_B5', 'SR_B6', 'SR_B4'], 'min': [0.05, 0.05, 0.03],
            'max': [0.6, 0.55, 0.3], 'gamma': [1.2],
        }

    monkeypatch.setattr(cache_operations, 'resolve_landsat_config_sync',
                        resolve_configuration)

    server = fakeredis.FakeServer()
    anchor = fakeredis.FakeRedis(server=server, decode_responses=False)
    clients = []

    class Client(fakeredis.FakeRedis):
        def __init__(self):
            super().__init__(server=server, decode_responses=False)
            self.was_closed = False

        def close(self):
            self.was_closed = True
            super().close()

    def redis_client(*args, **kwargs):
        client = Client()
        clients.append(client)
        return client

    monkeypatch.setattr(redis, 'from_url', redis_client)
    monkeypatch.setattr(redis.Redis, 'from_url', redis_client)
    storage = Storage()
    monkeypatch.setattr(boto3, 'client', lambda *args, **kwargs: storage)
    coordinator = gee_admission.RedisAdmission(anchor, gee_admission.AdmissionLimits(wait_seconds=0))
    monkeypatch.setattr(gee_admission, 'get_admission_controller', lambda: coordinator)
    monkeypatch.setattr(gee_admission, '_sdk_transport_installed', False)
    data = io.BytesIO()
    Image.new('RGB', (256, 256), 'green').save(data, format='PNG')
    state = SimpleNamespace(
        operations=cache_operations, redis=anchor, storage=storage, clients=clients,
        png=data.getvalue(), downloads=[], maps=[], http_error=None, http_gate=None,
        source_absence_evidence=source_absence_evidence,
    )

    class Response(io.BytesIO):
        status = 200
        headers = {'Content-Type': 'image/png'}

    def download(opener, request, data=None, timeout=None):
        state.downloads.append(request.full_url)
        if state.http_gate:
            state.http_gate()
        if state.http_error:
            raise state.http_error
        return Response(state.png)

    def get_map(parameters):
        state.maps.append(parameters)
        return {'tile_fetcher': SimpleNamespace(url_format=URL)}

    monkeypatch.setattr(urllib.request.OpenerDirector, 'open', download)
    monkeypatch.setattr(ee.data, 'getMapId', get_map)
    return state


def warm(state, **overrides):
    arguments = dict(x=455, y=578, z=10, layer='landsat', period='WET', year=2020,
                     month=1, visparam='landsat-tvi-false')
    arguments.update(overrides)
    return state.operations.cache_warm_tile.run(**arguments)


def test_warming_uses_same_seasonal_key_and_reuses_confirmed_png(warming):
    first = warm(warming)
    second = warm(warming, month=9)
    assert first['tile'] == KEY
    assert first['status'] == 'cached'
    assert second['status'] == 'already_cached'
    assert len(warming.downloads) == 1
    assert len(warming.storage.uploads) == 1
    assert all(client.was_closed for client in warming.clients)
    assert warming.storage.closed == 2


def test_strict_warming_returns_persisted_expiry_on_download_and_reuse(warming):
    def read_object(*, Bucket, Key):
        item = warming.storage.objects[Key]
        return {**item, 'Body': io.BytesIO(item['Body'])}
    warming.storage.get_object = read_object
    first = warm(warming, validate_image=True)
    second = warm(warming, validate_image=True)
    expiry = float(warming.storage.uploads[0]['Metadata']['expires_at'])
    assert first['expires_at'] == expiry
    assert second['expires_at'] == expiry
    assert len(warming.downloads) == 1


def test_upload_failure_never_publishes_confirmed_index(warming):
    warming.storage.failure = OSError('armazenamento indisponível')
    with pytest.raises(OSError):
        warm(warming)
    assert warming.redis.hgetall('tile:' + KEY) == {}
    assert all(client.was_closed for client in warming.clients)
    assert warming.storage.closed == 1


def test_index_is_absent_until_upload_finishes_and_carries_original_expiry(warming):
    during_upload = []
    warming.storage.before_upload = lambda: during_upload.append(warming.redis.hgetall('tile:' + KEY))
    before = time.time()
    warm(warming)
    index = warming.redis.hgetall('tile:' + KEY)
    assert during_upload == [{}]
    assert index[b's3_synced'] == b'1'
    assert index[b'content_type'] == b'image/png'
    expiry = float(index[b'expires_at'])
    assert before + 365 * 86400 <= expiry <= time.time() + 365 * 86400
    assert float(warming.storage.uploads[0]['Metadata']['expires_at']) == expiry
    assert 365 * 86400 - 2 <= warming.redis.ttl('tile:' + KEY) <= 365 * 86400


def test_current_year_png_expires_in_thirty_days(warming):
    result = warm(warming, year=datetime.now().year)
    remaining = warming.redis.ttl('tile:' + result['tile'])
    assert 30 * 86400 - 2 <= remaining <= 30 * 86400


def test_unconfirmed_index_does_not_skip_generation(warming):
    warming.redis.hset('tile:' + KEY, mapping={'s3_synced': '0', 's3_key': 'missing'})
    result = warm(warming)
    assert result['status'] == 'cached'
    assert len(warming.downloads) == 1
    assert warming.redis.hget('tile:' + KEY, 's3_synced') == b'1'


def test_missing_index_recovers_valid_object_without_google(warming):
    s3_key = 'tiles/' + hashlib.md5(KEY.encode()).hexdigest()[:2] + '/' + KEY
    warming.storage.objects[s3_key] = {
        'Body': warming.png, 'Metadata': {'expires_at': str(time.time() + 120)},
    }
    result = warm(warming)
    assert result['status'] == 'already_cached'
    assert warming.downloads == []
    assert warming.maps == []


@pytest.mark.parametrize('metadata', [{}, {'expires_at': '0'}, {'expires_at': 'nan'}])
def test_object_without_proven_validity_is_regenerated(warming, metadata):
    s3_key = 'tiles/' + hashlib.md5(KEY.encode()).hexdigest()[:2] + '/' + KEY
    warming.storage.objects[s3_key] = {'Body': warming.png, 'Metadata': metadata}
    assert warm(warming)['status'] == 'cached'
    assert len(warming.downloads) == 1


@pytest.mark.parametrize('data', [b'<html>falha</html>', b'\x89PNG\r\n\x1a\ntruncado', b'x' * (8 * 1024 * 1024 + 1)], ids=['html', 'truncado', 'oversize'])
def test_invalid_or_oversized_download_is_not_persisted(warming, data):
    warming.png = data
    result = warm(warming)
    assert result['status'] == 'failed'
    assert result['reason'] == 'invalid_tile'
    assert warming.storage.uploads == []
    assert warming.redis.hgetall('tile:' + KEY) == {}


def test_two_workers_recheck_after_the_same_png_lock(warming):
    entered = threading.Event()
    finish = threading.Event()

    def wait_http():
        entered.set()
        finish.wait(3)

    warming.http_gate = wait_http
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(warm, warming)
        assert entered.wait(2)
        second = pool.submit(warm, warming, month=9)
        time.sleep(.05)
        finish.set()
        statuses = {first.result()['status'], second.result()['status']}
    assert statuses == {'cached', 'already_cached'}
    assert len(warming.downloads) == 1
    assert len(warming.storage.uploads) == 1


def test_google_calls_use_background_budget_and_429_sets_shared_pause(warming):
    from app.core.gee_admission import GoogleAdmissionError

    warming.http_error = HTTPError(URL, 429, 'quota', {'Retry-After': '30'}, io.BytesIO())
    with pytest.raises(Exception):
        warm(warming)
    assert warming.redis.zcard('gee:{admission}:shared:rate_bg') == 2
    from app.core.gee_admission import get_admission_controller
    with pytest.raises(GoogleAdmissionError) as error:
        get_admission_controller().acquire(project='test')
    assert error.value.retry_after > 29
    assert warming.redis.hgetall('tile:' + KEY) == {}
    assert all(client.was_closed for client in warming.clients)


def test_sync_lock_does_not_delete_other_owner_after_lease_loss(warming):
    from app.cache.errors import TileLockUnavailable
    module = importlib.import_module('app.cache.sync_lock')
    with pytest.raises(TileLockUnavailable):
        with module.sync_tile_lock(warming.redis, 'example', lease_seconds=1) as lease:
            warming.redis.set('lock:example', 'successor', px=1000)
            lease.ensure_owned()
    assert warming.redis.get('lock:example') == b'successor'


def test_sync_lock_rejects_unavailable_coordinator_before_work(warming):
    from app.cache.errors import TileLockUnavailable
    module = importlib.import_module('app.cache.sync_lock')
    server = warming.redis.connection_pool.connection_kwargs['server']
    server.connected = False
    with pytest.raises(TileLockUnavailable):
        with module.sync_tile_lock(warming.redis, 'example'):
            pytest.fail('Geração não pode começar sem concessão')


def test_sync_lock_renews_and_bounds_contention(warming):
    from app.cache.errors import TileLockUnavailable
    module = importlib.import_module('app.cache.sync_lock')
    with module.sync_tile_lock(warming.redis, 'example', lease_seconds=.15):
        time.sleep(.35)
        started = time.monotonic()
        with pytest.raises(TileLockUnavailable):
            with module.sync_tile_lock(warming.redis, 'example', wait_timeout=.05):
                pytest.fail('Concessão ativa não pode ser duplicada')
        assert time.monotonic() - started < .2


def test_campaign_map_keeps_credentials_and_separate_url_namespace(warming, monkeypatch):
    import requests
    from app.core import tvi_token

    tvi_token.clear_cache()
    monkeypatch.setattr(tvi_token, 'settings', SimpleNamespace(
        TVI_API_URL='https://tvi.invalid', TVI_INTERNAL_TOKEN='test-internal',
    ))
    warming.redis.set('meta:' + PATH, orjson.dumps({
        'url': 'https://earthengine.googleapis.com/public/{z}/{x}/{y}',
        'date': datetime.now().isoformat(),
    }))
    map_headers = []

    class Reply:
        status_code = 200
        headers = {}

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

        def raise_for_status(self):
            pass

    def post(url, **kwargs):
        if url.startswith('https://tvi.invalid/'):
            assert kwargs['json'] == {'campaignId': 'campaign-test'}
            return Reply({'accessToken': 'campaign-token', 'projectId': 'campaign-project',
                          'expiresAt': (time.time() + 3600) * 1000})
        map_headers.append(kwargs['headers'])
        assert warming.redis.get('lock:url:tvi:campaign-test:' + PATH) is not None
        return Reply({'name': 'projects/campaign-project/maps/campaign-map'})

    monkeypatch.setattr(requests, 'post', post)
    assert warm(warming, tvi_campaign_id='campaign-test')['status'] == 'cached'
    assert map_headers == [{'Authorization': 'Bearer campaign-token',
                            'X-Goog-User-Project': 'campaign-project'}]
    assert warming.maps == []
    assert '/projects/campaign-project/maps/' in warming.downloads[0]
    assert warming.redis.get('meta:tvi:campaign-test:' + PATH) is not None
    assert b'/public/' in warming.redis.get('meta:' + PATH)
    assert warming.redis.zcard('gee:{admission}:shared:rate_bg') == 2


def test_url_creation_rechecks_shared_lock_for_distinct_pngs(warming, monkeypatch):
    import ee

    entered = threading.Event()
    finish = threading.Event()
    original = ee.data.getMapId

    def map_id(parameters):
        assert warming.redis.get('lock:url:' + PATH) is not None
        entered.set()
        finish.wait(3)
        return original(parameters)

    monkeypatch.setattr(ee.data, 'getMapId', map_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(warm, warming, x=910, y=1156, z=11)
        assert entered.wait(2)
        second = pool.submit(warm, warming, x=911, y=1156, z=11)
        time.sleep(.05)
        finish.set()
        assert first.result()['status'] == 'cached'
        assert second.result()['status'] == 'cached'
    assert len(warming.maps) == 1
    assert len(warming.downloads) == 2


def test_lost_png_lock_prevents_upload_and_preserves_successor(warming):
    from app.cache.errors import TileLockUnavailable

    warming.http_gate = lambda: warming.redis.set('lock:' + KEY, 'successor', px=1000)
    with pytest.raises(TileLockUnavailable):
        warm(warming)
    assert warming.storage.uploads == []
    assert warming.redis.hgetall('tile:' + KEY) == {}
    assert warming.redis.get('lock:' + KEY) == b'successor'
    assert all(client.was_closed for client in warming.clients)


def test_lost_lock_during_upload_does_not_publish_index(warming):
    from app.cache.errors import TileLockUnavailable

    warming.storage.before_upload = lambda: warming.redis.set('lock:' + KEY, 'successor', px=1000)
    with pytest.raises(TileLockUnavailable):
        warm(warming)
    assert len(warming.storage.uploads) == 1
    assert warming.redis.hgetall('tile:' + KEY) == {}
    assert warming.redis.get('lock:' + KEY) == b'successor'


def test_legacy_url_is_reused_without_regeneration(warming):
    warming.redis.set('meta:landsat_WET_2020_1_landsat-tvi-false_BEST_IMAGE/7ec', orjson.dumps({
        'url': URL, 'date': datetime.now().isoformat(),
    }))
    assert warm(warming)['status'] == 'cached'
    assert warming.maps == []
    assert len(warming.downloads) == 1


def test_sync_writer_propagates_index_failure_after_confirmed_upload(warming, monkeypatch):
    from app.cache.sync_cache import persist_png_sync
    import redis.client

    def fail(pipeline, *args, **kwargs):
        raise redis.ConnectionError('índice indisponível')

    monkeypatch.setattr(redis.client.Pipeline, 'execute', fail)
    with pytest.raises(redis.ConnectionError):
        persist_png_sync(KEY, warming.png, 86400)
    assert len(warming.storage.uploads) == 1
    assert warming.redis.hgetall('tile:' + KEY) == {}
    assert all(client.was_closed for client in warming.clients)


def test_real_celery_worker_continues_windows_after_individual_failure(warming, monkeypatch):
    import uuid
    from celery import Celery
    from celery.contrib.testing.worker import start_worker

    operations = warming.operations
    name = 'warm-worker-' + uuid.uuid4().hex
    app = Celery(name, broker='memory://', backend='cache+memory://')
    app.conf.task_default_queue = name
    tile_task = app.task(bind=True, name=name + '.tile', max_retries=0)(
        operations.cache_warm_tile.run.__func__,
    )
    callback_task = app.task(bind=True, name=name + '.finished', max_retries=2)(
        operations.cache_warm_batch_finished.run.__func__,
    )
    monkeypatch.setattr(operations, 'cache_warm_batch_finished', callback_task)
    monkeypatch.setattr(operations, 'cache_warm_tile', tile_task)
    monkeypatch.setattr(operations, 'celery_app', app)
    original = warming.storage.put_object

    def upload(**kwargs):
        if kwargs['Key'].endswith('/455_578.png'):
            raise OSError('armazenamento indisponível')
        return original(**kwargs)

    monkeypatch.setattr(warming.storage, 'put_object', upload)
    tiles = [{'x': x, 'y': 578, 'z': 10, 'endpoint': 'landsat',
              'params': {'period': 'WET', 'year': 2020, 'month': 1,
                         'visparam': 'landsat-tvi-false'}} for x in range(455, 459)]
    try:
        with start_worker(app, perform_ping_check=False, pool='solo',
                          queues=[name, 'standard', 'high_priority'], shutdown_timeout=5):
            result = operations.cache_warm_from_tiles_json.run(tiles, batch_size=2)
            completed = app.AsyncResult(result['job_id']).get(timeout=15, disable_sync_subtasks=False)
        assert completed['status'] == 'completed_with_errors'
        assert completed['total_tiles'] == 4
        assert completed['failed'] == 1
        assert completed['cached'] == 3
        assert len(warming.storage.uploads) == 3
        assert completed['errors'][0]['tile'].endswith('/455_578.png')
    finally:
        app.close()


def test_sync_google_download_does_not_follow_redirect_inside_one_attempt():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import urllib.request

    module = importlib.import_module('app.utils.ee_http_sync')
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(302)
            self.send_header('Location', '/unaccounted')
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(HTTPError) as error:
            module.urlopen_without_redirects(
                urllib.request.Request(f'http://127.0.0.1:{server.server_port}/tile'), timeout=1,
            )
        assert error.value.code == 302
        error.value.close()
        assert paths == ['/tile']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_valid_jpeg_is_persisted_and_reused_without_another_download(warming):
    data = io.BytesIO()
    Image.new('RGB', (256, 256), 'green').save(data, format='JPEG')
    warming.png = data.getvalue()
    assert warm(warming)['status'] == 'cached'
    assert warming.storage.uploads[0]['Body'] == warming.png
    assert warming.storage.uploads[0]['ContentType'] == 'image/jpeg'
    assert warming.redis.hget('tile:' + KEY, 'content_type') == b'image/jpeg'
    assert warm(warming)['status'] == 'already_cached'
    assert len(warming.downloads) == 1


def test_truncated_jpeg_is_not_persisted(warming):
    data = io.BytesIO()
    Image.new('RGB', (256, 256), 'green').save(data, format='JPEG')
    warming.png = data.getvalue()[:-100]
    assert warm(warming)['status'] == 'failed'
    assert warming.storage.uploads == []


def test_offline_warming_checks_existing_body_and_regenerates_white_tile(warming):
    def get_object(*, Bucket, Key):
        stored = warming.storage.objects[Key]
        return {**stored, 'Body': io.BytesIO(stored['Body'])}
    warming.storage.get_object = get_object
    data = io.BytesIO()
    Image.new('RGB', (256, 256), 'white').save(data, format='PNG')
    s3_key = 'tiles/' + hashlib.md5(KEY.encode()).hexdigest()[:2] + '/' + KEY
    warming.storage.objects[s3_key] = {
        'Body': data.getvalue(), 'Metadata': {'expires_at': str(time.time() + 120)},
    }
    result = warm(warming, validate_image=True)
    assert result['status'] == 'cached'
    assert len(warming.downloads) == 1


def test_offline_warming_rejects_blank_source_without_confirming_success(warming):
    data = io.BytesIO()
    Image.new('RGB', (256, 256), 'white').save(data, format='PNG')
    warming.png = data.getvalue()
    result = warm(warming, validate_image=True)
    assert result['status'] == 'failed'
    assert result['reason'] == 'invalid_tile'
    assert warming.storage.uploads == []


def test_explicit_regeneration_bypasses_valid_cache(warming):
    warm(warming)
    result = warm(warming, force=True)
    assert result['status'] == 'cached'
    assert len(warming.downloads) == 2


def test_offline_promotes_valid_legacy_image_over_invalid_canonical_without_google(warming):
    from app.cache.sync_cache import _s3_key
    legacy_key = 'landsat_WET_2020_1_landsat-tvi-false_BEST_IMAGE/7ec/10/455_578.png'
    data = io.BytesIO()
    Image.new('RGB', (256, 256), 'white').save(data, format='PNG')
    expiry = time.time() + 120
    warming.storage.objects[_s3_key(KEY)] = {
        'Body': data.getvalue(), 'Metadata': {'expires_at': str(expiry)},
    }
    warming.storage.objects[_s3_key(legacy_key)] = {
        'Body': warming.png, 'Metadata': {'expires_at': str(expiry)},
    }

    def read_object(*, Bucket, Key):
        item = warming.storage.objects[Key]
        return {**item, 'Body': io.BytesIO(item['Body'])}

    warming.storage.get_object = read_object
    result = warm(warming, validate_image=True)
    assert result['status'] == 'already_cached'
    assert warming.downloads == []
    assert warming.storage.objects[_s3_key(KEY)]['Body'] == warming.png
    assert float(warming.storage.objects[_s3_key(KEY)]['Metadata']['expires_at']) <= expiry + 0.01


def test_offline_verification_publishes_digest_for_existing_image(warming):
    from app.cache.sync_cache import _s3_key
    expiry = time.time() + 120
    warming.storage.objects[_s3_key(KEY)] = {
        'Body': warming.png, 'Metadata': {'expires_at': str(expiry)},
    }

    def read_object(*, Bucket, Key):
        item = warming.storage.objects[Key]
        return {**item, 'Body': io.BytesIO(item['Body'])}

    warming.storage.get_object = read_object
    assert warm(warming, validate_image=True)['status'] == 'already_cached'
    assert warming.redis.hget('tile:' + KEY, 'content_sha256') == hashlib.sha256(warming.png).hexdigest().encode()
    assert 0 < warming.redis.ttl('tile:' + KEY) <= 120
    assert warming.downloads == []


def test_proven_absence_is_persisted_and_reused_without_google(warming, monkeypatch):
    from datetime import datetime, timezone
    import importlib
    offline_campaign = importlib.import_module('app.tasks.offline_campaign')
    checks = []
    def prove(params):
        checks.append(params)
        return {'kind': 'empty_collection', 'count': 0, 'collection': 'LANDSAT/LC08/C02/T1_L2',
                'period': params['period'], 'year': params['year'], 'checked_at': datetime.now(timezone.utc)}
    monkeypatch.setattr(offline_campaign, 'source_absence_evidence', prove)
    data = io.BytesIO()
    Image.new('RGBA', (256, 256), (0, 0, 0, 0)).save(data, format='PNG')
    warming.png = data.getvalue()
    first = warm(warming, validate_image=True)
    second = warm(warming, validate_image=True)
    assert first['status'] == second['status'] == 'no_data'
    assert first['expires_at'] == second['expires_at']
    assert warming.redis.get('absence:' + KEY)
    assert len(checks) == len(warming.downloads) == 1
    assert warming.storage.uploads == []


def test_force_generation_clears_proven_absence(warming):
    from datetime import datetime, timezone
    from app.cache.offline_absence import set_absence
    from app.cache.sync_cache import SyncTileCache
    with SyncTileCache() as cache:
        with cache.tile_lock(KEY) as lease:
            set_absence(cache.redis, KEY, {'kind': 'empty_collection', 'count': 0,
                        'collection': 'LANDSAT/LC08/C02/T1_L2', 'period': 'WET', 'year': 2020,
                        'checked_at': datetime.now(timezone.utc)}, lease=lease)
    def read_object(*, Bucket, Key):
        item = warming.storage.objects[Key]
        return {**item, 'Body': io.BytesIO(item['Body'])}
    warming.storage.get_object = read_object
    assert warm(warming, validate_image=True, force=True)['status'] == 'cached'
    assert warming.redis.get('absence:' + KEY) is None


@pytest.mark.parametrize('empty', [False, True])
def test_empty_sentinel_can_be_proved_before_download(warming, monkeypatch, empty):
    import ee
    from datetime import timezone
    offline = importlib.import_module('app.tasks.offline_campaign')
    def builder(*args, **kwargs):
        raise ee.EEException('Image has no bands')
    def prove(params):
        if not empty:
            return None
        return {'kind': 'empty_collection', 'count': 0, 'collection': 'COPERNICUS/S2_HARMONIZED',
                'period': params['period'], 'year': params['year'], 'checked_at': datetime.now(timezone.utc)}
    monkeypatch.setattr(warming.operations, '_warm_create_s2_url', builder)
    monkeypatch.setattr(offline, 'source_absence_evidence', prove)
    if empty:
        assert warm(warming, layer='s2_harmonized', validate_image=True)['status'] == 'no_data'
    else:
        result = warm(warming, layer='s2_harmonized', validate_image=True)
        assert result['status'] == 'failed'
        assert result['reason'] == 'EEException'
        assert result['failure_phase'] == 'map_create'
        assert 'secondary_error' not in result
    assert warming.downloads == []


def test_offline_map_admission_is_deferred_without_calling_source(warming, monkeypatch):
    from app.core.gee_admission import get_admission_controller
    coordinator = get_admission_controller()
    monkeypatch.setattr(coordinator, '_attempt', lambda *args: [0, 1000, 'concurrency'])
    result = warm(warming, validate_image=True)
    assert result['status'] == 'deferred'
    assert result['reason'] == 'GoogleAdmissionError'
    assert result['admission_reason'] == 'concurrency'
    assert result['failure_phase'] == 'map_create'
    assert result['retry_after'] >= 1
    assert warming.maps == warming.downloads == []


def test_offline_download_admission_is_deferred_after_cached_map(warming, monkeypatch):
    from app.core.gee_admission import get_admission_controller
    coordinator = get_admission_controller()
    warming.redis.set('meta:' + PATH, orjson.dumps({'url': URL, 'date': datetime.now().isoformat()}))
    monkeypatch.setattr(coordinator, '_attempt', lambda *args: [0, 2000, 'rate'])
    result = warm(warming, validate_image=True)
    assert result['status'] == 'deferred'
    assert result['failure_phase'] == 'download'
    assert result['admission_reason'] == 'rate'
    assert warming.maps == warming.downloads == []


def test_offline_429_with_coordinator_failure_is_not_free_deferral(warming, monkeypatch):
    from app.core import gee_admission
    coordinator = gee_admission.get_admission_controller()
    original = coordinator.redis.eval
    def unavailable_pause(script, *args):
        if script == gee_admission._PAUSE:
            raise redis.ConnectionError('unavailable')
        return original(script, *args)
    monkeypatch.setattr(coordinator.redis, 'eval', unavailable_pause)
    warming.http_error = HTTPError(URL, 429, 'quota', {'Retry-After': '30'}, io.BytesIO())
    result = warm(warming, validate_image=True)
    assert result['status'] == 'failed'
    assert result['reason'] == 'EarthEngineRateLimitedError'
    assert result['http_status'] == 429
    assert result['admission_reason'] == 'coordinator_unavailable'
    assert result['failure_phase'] == 'download'
    assert len(warming.downloads) == 1


@pytest.mark.parametrize('color', ['white', 'black'])
@pytest.mark.parametrize('failure', ['admission', 'timeout'])
def test_invalid_tile_is_preserved_when_absence_query_fails(warming, monkeypatch, color, failure):
    from app.core.gee_admission import GoogleAdmissionError
    from app.tasks import offline_campaign
    data = io.BytesIO()
    Image.new('RGB', (256, 256), color).save(data, format='PNG')
    warming.png = data.getvalue()
    def not_admitted(params):
        if failure == 'timeout':
            raise TimeoutError('source timeout')
        raise GoogleAdmissionError(30, 'concurrency')
    monkeypatch.setattr(offline_campaign, 'source_absence_evidence', not_admitted)
    result = warm(warming, validate_image=True)
    assert result['status'] == 'failed'
    assert result['reason'] == 'invalid_tile'
    assert result['failure_phase'] == 'image_validation'
    if failure == 'admission':
        assert result['admission_phase'] == 'absence_check'
        assert result['admission_reason'] == 'concurrency'
    else:
        assert result['secondary_phase'] == 'absence_check'
        assert result['secondary_error'] == 'TimeoutError'
    assert result['diagnostics']['validation_code'] == color + '_image'
    assert result['diagnostics']['content_sha256'] == hashlib.sha256(warming.png).hexdigest()
    assert warming.storage.uploads == []
    assert URL not in str(result)


def test_persistence_confirmation_failure_is_distinct_from_download_validation(warming, monkeypatch):
    from app.cache.sync_cache import SyncTileCache
    monkeypatch.setattr(SyncTileCache, 'confirm_valid_image', lambda *args: False)
    result = warm(warming, validate_image=True)
    assert result['status'] == 'failed'
    assert result['reason'] == 'invalid_tile'
    assert result['failure_phase'] == 'persistence_verify'
    assert result['diagnostics']['validation_code'] == 'persistence_unconfirmed'
    assert len(warming.storage.uploads) == 1
    assert result['diagnostics']['byte_length'] == len(warming.png)


def test_offline_sdk_429_before_internal_retry_is_a_real_failure(warming, monkeypatch):
    import requests
    from googleapiclient.http import HttpRequest
    from app.core import gee_admission
    monkeypatch.setattr(gee_admission, '_sdk_transport_installed', True)
    network_calls = []
    def network(*args, **kwargs):
        network_calls.append(True)
        response = requests.Response()
        response.status_code = 429
        response._content = b'{}'
        response.headers = {'Retry-After': '30'}
        return response
    monkeypatch.setattr(requests.Session, 'request', network)
    request = HttpRequest(gee_admission.GoogleAdmissionTransport(), lambda response, body: body,
                          'https://earthengine.googleapis.com/v1/projects/test/maps')
    request._sleep = lambda seconds: None
    def map_create(*args, **kwargs):
        return gee_admission.call_gee(request.execute, num_retries=1, operation='map', background=True)
    monkeypatch.setattr(warming.operations, '_warm_create_landsat_url', map_create)
    result = warm(warming, validate_image=True)
    assert result['status'] == 'failed'
    assert result['reason'] == 'EarthEngineRateLimitedError'
    assert result['failure_phase'] == 'map_create'
    assert result['admission_reason'] == 'cooldown'
    assert result['http_status'] == 429
    assert len(network_calls) == 1
    assert warming.downloads == []


def test_non_offline_admission_preserves_continue_on_error_after_retry_budget(warming, monkeypatch):
    from app.core.gee_admission import get_admission_controller
    coordinator = get_admission_controller()
    monkeypatch.setattr(coordinator, '_attempt', lambda *args: [0, 1000, 'concurrency'])
    task = warming.operations.cache_warm_tile
    task.push_request(retries=task.max_retries, called_directly=True)
    try:
        result = warm(warming, continue_on_error=True)
    finally:
        task.pop_request()
    assert result['status'] == 'failed'
    assert result['reason'] == 'cache_warm_failed'
    assert result['error_type'] == 'GoogleAdmissionError'
    assert warming.maps == warming.downloads == []


@pytest.mark.parametrize(('exception_name', 'error_type'), [
    ('Timeout', 'TimeoutError'), ('ConnectionError', 'ConnectionError'),
    ('ChunkedEncodingError', 'ConnectionError'), ('SSLError', 'ConnectionError'),
])
def test_offline_sdk_transport_failure_before_admission_rejection_is_not_deferred(
        warming, monkeypatch, exception_name, error_type):
    import requests
    from googleapiclient.http import HttpRequest
    from app.core import gee_admission
    coordinator = gee_admission.get_admission_controller()
    monkeypatch.setattr(gee_admission, '_sdk_transport_installed', True)
    calls = []
    def network(*args, **kwargs):
        calls.append(True)
        monkeypatch.setattr(coordinator, '_attempt', lambda *args: [0, 1000, 'rate'])
        raise getattr(requests.exceptions, exception_name)('simulated source failure')
    monkeypatch.setattr(requests.Session, 'request', network)
    request = HttpRequest(gee_admission.GoogleAdmissionTransport(), lambda response, body: body,
                          'https://earthengine.googleapis.com/v1/projects/test/maps')
    request._sleep = lambda seconds: None
    def map_create(*args, **kwargs):
        return gee_admission.call_gee(request.execute, num_retries=1, operation='map', background=True)
    monkeypatch.setattr(warming.operations, '_warm_create_landsat_url', map_create)
    result = warm(warming, validate_image=True)
    assert result['status'] == 'failed'
    assert result['reason'] == error_type
    assert result['transport_error'] == error_type
    assert result['http_status'] is None
    assert result['failure_phase'] == 'map_create'
    assert result['admission_reason'] == 'rate'
    assert result['retry_after'] == 1
    independent = warm(warming, validate_image=True)
    assert independent['status'] == 'deferred'
    assert independent['http_status'] is None
    assert independent.get('transport_error') is None
    assert len(calls) == 1
    assert warming.downloads == []


def test_black_tile_with_available_scenes_remains_invalid_without_absence_marker(warming, monkeypatch):
    import ee
    from unittest.mock import MagicMock
    from app.tasks import offline_campaign
    collection = MagicMock()
    collection.filterDate.return_value = collection
    collection.filterBounds.return_value = collection
    collection.filter.return_value = collection
    collection.size.return_value.getInfo.return_value = 31
    monkeypatch.setattr(ee, 'ImageCollection', lambda name: collection)
    monkeypatch.setattr(offline_campaign, 'source_absence_evidence', warming.source_absence_evidence)
    monkeypatch.setattr(warming.operations, '_warm_create_landsat_url', lambda *args, **kwargs: URL)
    data = io.BytesIO()
    Image.new('RGB', (256, 256), 'black').save(data, format='PNG')
    warming.png = data.getvalue()
    result = warm(warming, validate_image=True)
    assert result['status'] == 'failed'
    assert result['reason'] == 'invalid_tile'
    assert result['failure_phase'] == 'image_validation'
    assert result['diagnostics']['validation_code'] == 'black_image'
    collection.size.return_value.getInfo.assert_called_once_with()
    assert warming.redis.get('absence:' + KEY) is None
    assert warming.redis.hgetall('tile:' + KEY) == {}
    assert warming.storage.uploads == []
