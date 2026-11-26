from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import mongomock
import pytest

PARAMS = dict(collection='landsat-2', lon=-49.4, lat=-16.7, radius_m=1000,
              start='2024-01-01', end='2024-01-04', criterion='cloud')


@pytest.fixture
def store():
    from app.modules.bdc.selection_jobs import SelectionJobStore
    return SelectionJobStore(mongomock.MongoClient().tiles)


def test_concurrent_idempotency_and_shared_parameters_survive_new_store(store):
    from app.modules.bdc.selection_jobs import SelectionJobStore, IdempotencyConflict
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda _: store.submit(PARAMS, 'same-request'), range(12)))
    assert len({job['_id'] for job in jobs}) == 1
    assert store.jobs.count_documents({}) == 1
    restarted = SelectionJobStore(store.db)
    assert restarted.submit(PARAMS, 'another-request')['_id'] == jobs[0]['_id']
    with pytest.raises(IdempotencyConflict):
        restarted.submit(dict(PARAMS, radius_m=2000), 'same-request')


def test_claim_fencing_recovery_and_complete_results_are_immutable(store):
    job = store.submit(PARAMS, 'request')
    token = store.claim(job['_id'])
    assert token and store.claim(job['_id']) is None
    store.jobs.update_one({'_id': job['_id']}, {'$set': {'lease_until': datetime.now(timezone.utc) - timedelta(seconds=1)}})
    replacement = store.claim(job['_id'])
    assert replacement and replacement != token
    result = dict(complete=True, best_local_id='a', best_global_id='b', rows=[{'id': 'a', 'eligible': True}, {'id': 'b', 'eligible': True}], evaluated=2, eligible=2)
    assert not store.finish(job['_id'], token, result=result)
    assert store.finish(job['_id'], replacement, result=result)
    assert store.submit(PARAMS, 'again', retry=True)['state'] == 'ready'
    assert store.claim(job['_id']) is None


def test_partial_result_cannot_be_published(store):
    job = store.submit(PARAMS, 'request')
    token = store.claim(job['_id'])
    with pytest.raises(ValueError):
        store.finish(job['_id'], token, result={'complete': False, 'best_local_id': 'a'})
    assert store.get(job['_id'])['state'] == 'running'


def test_global_admission_is_shared_across_instances_and_releases(store):
    from app.modules.bdc.selection_jobs import SelectionJobStore, OriginBusy
    other = SelectionJobStore(store.db)
    with store.origin_admission(), other.origin_admission():
        with pytest.raises(OriginBusy):
            with SelectionJobStore(store.db).origin_admission():
                pass
    with other.origin_admission():
        pass


def test_origin_pause_is_shared_and_prevents_network_budget(store):
    from app.modules.bdc.selection_jobs import SelectionJobStore
    from app.modules.bdc.transport import ServicePaused
    store.pause_origin()
    with pytest.raises(ServicePaused):
        with SelectionJobStore(store.db).origin_admission():
            pass


def test_retry_requires_explicit_intent_and_only_recovers_retryable_errors(store):
    job = store.submit(PARAMS, 'request')
    token = store.claim(job['_id'])
    store.finish(job['_id'], token, error={'code': 'read_failed', 'retryable': True})
    assert store.submit(PARAMS, 'request')['state'] == 'failed'
    assert store.submit(PARAMS, 'request', retry=True)['state'] == 'pending'
    token = store.claim(job['_id'])
    store.finish(job['_id'], token, error={'code': 'candidate_limit', 'retryable': False})
    assert store.submit(PARAMS, 'request', retry=True)['state'] == 'failed'


def test_explicit_retry_epoch_is_atomic_and_worker_propagates_authorization(store):
    from app.tasks.bdc_selection import process_selection
    job = store.submit(PARAMS, 'request')
    token = store.claim(job['_id'])
    store.finish(job['_id'], token, error={'code': 'read_failed', 'retryable': True})
    with ThreadPoolExecutor(max_workers=4) as pool:
        attempts = list(pool.map(lambda _: store.submit(PARAMS, 'request', retry=True), range(4)))
    assert {attempt['retry_epoch'] for attempt in attempts} == {1}
    observed = []
    class Service:
        def select_images(self, params, check, *, retry_token=None):
            observed.append(retry_token)
            return dict(complete=True, best_local_id=None, best_global_id=None, rows=[], eligible=0, evaluated=0)
    assert process_selection(store, job['_id'], Service())['state'] == 'no_image'
    assert observed and isinstance(observed[0], str) and len(observed[0]) == 64


@pytest.mark.parametrize('failure', ['timeout', 'http_500'])
def test_transport_explicit_retry_preserves_cache_and_consumes_authorization_once(tmp_path, monkeypatch, failure):
    import requests
    from app.modules.bdc.transport import Transport
    calls = []
    healthy = [False]
    def request(*args, **kwargs):
        calls.append(True)
        if not healthy[0] and failure == 'timeout':
            raise requests.exceptions.Timeout('simulated timeout')
        response = requests.Response()
        response.status_code = 200 if healthy[0] else 500
        response._content = b'cached content'
        response._content_consumed = True
        return response
    monkeypatch.setattr(requests.Session, 'request', request)
    url = 'https://data.inpe.br/example'
    def attempt(retry_token=None):
        remote = Transport(tmp_path, retry_token=retry_token)
        try:
            return remote.request(url)[1]
        finally:
            remote.session.close()
    with pytest.raises(Exception):
        attempt()
    assert len(calls) == 1
    with pytest.raises(Exception):
        attempt('first-explicit-retry')
    assert len(calls) == 2
    with pytest.raises(Exception):
        attempt('first-explicit-retry')
    with pytest.raises(Exception):
        attempt()
    assert len(calls) == 2
    healthy[0] = True
    assert attempt('second-explicit-retry') == b'cached content'
    assert len(calls) == 3
    assert attempt('third-explicit-retry') == b'cached content'
    assert attempt() == b'cached content'
    assert len(calls) == 3


def test_explicit_retry_does_not_override_origin_pause(tmp_path, monkeypatch):
    import requests
    from app.modules.bdc.transport import Transport, ServicePaused
    calls = []
    def request(*args, **kwargs):
        calls.append(True)
        response = requests.Response()
        response.status_code = 503
        response._content = b'paused'
        response._content_consumed = True
        return response
    monkeypatch.setattr(requests.Session, 'request', request)
    for retry_token in (None, 'explicit-retry'):
        remote = Transport(tmp_path, retry_token=retry_token)
        try:
            with pytest.raises(ServicePaused):
                remote.request('https://data.inpe.br/example')
        finally:
            remote.session.close()
    assert len(calls) == 1


def test_expired_metadata_refresh_does_not_repeat_failed_attempt_automatically(tmp_path, monkeypatch):
    import requests
    from app.modules.bdc import transport
    clock = [1000]
    calls = []
    healthy = [True]
    monkeypatch.setattr(transport.time, 'time', lambda: clock[0])
    def request(*args, **kwargs):
        calls.append(True)
        if not healthy[0]:
            raise requests.exceptions.Timeout('simulated refresh timeout')
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"features": []}'
        response._content_consumed = True
        return response
    monkeypatch.setattr(requests.Session, 'request', request)
    url = 'https://data.inpe.br/bdc/stac/v1/search'
    remote = transport.Transport(tmp_path, metadata_ttl=10)
    try:
        remote.request(url)
        clock[0] += 11
        healthy[0] = False
        with pytest.raises(requests.exceptions.Timeout):
            remote.request(url)
        with pytest.raises(RuntimeError):
            remote.request(url)
        assert len(calls) == 2
        healthy[0] = True
        retry = transport.Transport(tmp_path, metadata_ttl=10, retry_token='explicit-refresh-retry')
        try:
            assert retry.request(url)[1] == b'{"features": []}'
        finally:
            retry.session.close()
        assert len(calls) == 3
    finally:
        remote.session.close()


def test_worker_explicit_retry_recovers_actual_service_transport_timeout(tmp_path, store, monkeypatch):
    from contextlib import contextmanager
    import requests
    from app.modules.bdc import selection_jobs
    from app.modules.bdc.service import BdcService
    from app.tasks.bdc_selection import process_selection
    @contextmanager
    def coordinator():
        yield store
    monkeypatch.setattr(selection_jobs, 'selection_store', coordinator)
    calls = []
    def request(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise requests.exceptions.Timeout('simulated source timeout')
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"features": [], "links": []}'
        response._content_consumed = True
        return response
    monkeypatch.setattr(requests.Session, 'request', request)
    service = BdcService(tmp_path)
    job = store.submit(PARAMS, 'request')
    assert process_selection(store, job['_id'], service)['state'] == 'failed'
    assert process_selection(store, job['_id'], service)['state'] == 'failed'
    assert len(calls) == 1
    store.submit(PARAMS, 'request', retry=True)
    assert process_selection(store, job['_id'], service)['state'] == 'no_image'
    assert len(calls) == 2


class Catalog:
    def __init__(self, duplicate=False, overflow_day=False):
        self.calls = []
        self.duplicate = duplicate
        self.overflow_day = overflow_day

    def json(self, url):
        query = parse_qs(urlsplit(url).query)
        start, end = [value[:10] for value in query['datetime'][0].split('/')]
        self.calls.append((start, end))
        if start != end or self.overflow_day:
            return {'features': [{'id': str(i)} for i in range(61)], 'links': []}
        return {'features': [{'id': 'shared' if self.duplicate else start,
                              'properties': {'datetime': start + 'T12:00:00Z', 'eo:cloud_cover': 1}}], 'links': []}


def test_partition_covers_every_day_and_deduplicates_before_evaluation(monkeypatch):
    from app.modules.bdc import local_quality as quality
    remote = Catalog(duplicate=True)
    evaluated = []
    def read(remote, item, *args):
        evaluated.append(item['id'])
        return dict(eligible=True, cloud_pct=0, unusable_pct=0, valid_pct=100)
    monkeypatch.setattr(quality, 'read_quality', read)
    result = quality.compare_partitioned(
        remote, PARAMS['collection'], PARAMS['lon'], PARAMS['lat'], PARAMS['radius_m'], PARAMS['start'], PARAMS['end'])
    assert result['complete'] and result['evaluated'] == 1 and evaluated == ['shared']
    assert {(start, end) for start, end in remote.calls if start == end} == {
        (f'2024-01-0{i}', f'2024-01-0{i}') for i in range(1, 5)}


def test_single_day_overflow_is_failure_without_any_quality_reads(monkeypatch):
    from app.modules.bdc import local_quality as quality
    monkeypatch.setattr(quality, 'read_quality', lambda *args: pytest.fail('partial evaluation'))
    with pytest.raises(quality.CandidateLimitExceeded):
        quality.compare_partitioned(Catalog(overflow_day=True), 'landsat-2', -49, -16, 1000, '2024-01-01', '2024-01-04')


def test_deadline_after_last_read_never_returns_partial_success(monkeypatch):
    from app.modules.bdc import local_quality as quality
    clock = [0]
    monkeypatch.setattr(quality.time, 'monotonic', lambda: clock[0])
    def read(*args):
        clock[0] = 11
        return dict(eligible=True, cloud_pct=0, unusable_pct=0)
    monkeypatch.setattr(quality, 'read_quality', read)
    with pytest.raises(quality.SelectionDeadlineExceeded):
        quality.compare_partitioned(Catalog(), 'landsat-2', -49, -16, 1000, '2024-01-01', '2024-01-01', max_seconds=10)


def test_partitioned_ranking_equals_integral_ranking_without_global_prefilter(monkeypatch):
    from app.modules.bdc import local_quality as quality
    items = [dict(id=f'item-{n:03}', properties={'datetime': f'2024-01-0{n % 4 + 1}T12:00:00Z',
                                               'eo:cloud_cover': n}) for n in range(80)]
    class Remote:
        def json(self, url):
            interval = parse_qs(urlsplit(url).query)['datetime'][0]
            first, last = [part[:10] for part in interval.split('/')]
            return {'features': [item for item in reversed(items) if first <= item['properties']['datetime'][:10] <= last]}
    def read(remote, item, *args):
        n = int(item['id'].split('-')[1])
        return dict(eligible=True, cloud_pct=80 - n, unusable_pct=80 - n, valid_pct=100)
    monkeypatch.setattr(quality, 'read_quality', read)
    partitioned = quality.compare_partitioned(Remote(), 'landsat-2', -49, -16, 1000, '2024-01-01', '2024-01-04')
    monkeypatch.setattr(quality, 'search_candidates', lambda *args, **kwargs: items)
    integral = quality.compare(None, 'landsat-2', -49, -16, 1000, '2024-01-01', '2024-01-04')
    assert partitioned['best_local_id'] == integral['best_local_id'] == 'item-079'
    assert partitioned['best_global_id'] == integral['best_global_id'] == 'item-000'
    assert partitioned['rows'] == integral['rows']


def test_any_failed_partition_rejects_whole_selection(monkeypatch):
    from app.modules.bdc import local_quality as quality
    remote = Catalog()
    original = remote.json
    def broken(url):
        if '2024-01-03T00' in url:
            raise OSError('origin unavailable')
        return original(url)
    remote.json = broken
    monkeypatch.setattr(quality, 'read_quality', lambda *args: pytest.fail('partial selection'))
    with pytest.raises(OSError):
        quality.compare_partitioned(remote, 'landsat-2', -49, -16, 1000, '2024-01-01', '2024-01-04')


def test_authenticated_job_api_is_idempotent_and_does_not_leak_internal_fields(store):
    from contextlib import contextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.modules.bdc.auth import verify_bdc_service
    from app.modules.bdc.selection_jobs import create_selection_router
    @contextmanager
    def factory():
        yield store
    calls = []
    app = FastAPI()
    app.include_router(create_selection_router(factory, calls.append), prefix='/api/bdc')
    client = TestClient(app)
    payload = dict(PARAMS, idempotency_key='request')
    assert client.post('/api/bdc/selection-jobs', json=payload).status_code == 401
    app.dependency_overrides[verify_bdc_service] = lambda: {'type': 'admin'}
    first = client.post('/api/bdc/selection-jobs', json=payload)
    assert first.status_code == 202 and first.json()['state'] == 'pending'
    job_id = first.json()['id']
    assert client.post('/api/bdc/selection-jobs', json=payload).json()['id'] == job_id
    assert calls == [job_id]
    status = client.get('/api/bdc/selection-jobs/' + job_id)
    assert status.status_code == 200 and status.headers['cache-control'] == 'no-store'
    assert not {'lease_until', 'lease_token', 'idempotency_key'} & status.json().keys()
    assert client.post('/api/bdc/selection-jobs', json=dict(payload, lon=10)).status_code == 409
    assert client.post('/api/bdc/selection-jobs', json=dict(payload, end='2026-01-01')).status_code == 422
    assert client.post('/api/bdc/selection-jobs', json=dict(payload, source_url='https://evil')).status_code == 422
    app.dependency_overrides.clear()
    assert client.get('/api/bdc/selection-jobs/' + job_id).status_code == 401


def test_worker_publishes_only_complete_results_and_never_repeats_terminal_job(store):
    from app.tasks.bdc_selection import process_selection
    job = store.submit(PARAMS, 'request')
    class Service:
        def select_images(self, params, check):
            check()
            return dict(complete=True, best_local_id='a', rows=[{'id': 'a', 'eligible': True}], eligible=1, evaluated=1)
    service = Service()
    assert process_selection(store, job['_id'], service)['state'] == 'ready'
    service.select_images = lambda *args: pytest.fail('terminal job repeated')
    assert process_selection(store, job['_id'], service)['state'] == 'ready'


@pytest.mark.parametrize(('exception', 'code'), [
    (TimeoutError('private source URL'), 'deadline_exceeded'),
    (OSError('private source URL'), 'read_failed'),
])
def test_worker_failure_has_no_partial_result_or_sensitive_message(store, exception, code):
    from app.tasks.bdc_selection import process_selection
    job = store.submit(PARAMS, 'request')
    class Service:
        def select_images(self, params, check):
            raise exception
    result = process_selection(store, job['_id'], Service())
    assert result['state'] == 'failed' and result['result'] is None
    assert result['error']['code'] == code
    assert 'private' not in str(result)


def test_catalog_profiles_share_the_same_definitions_as_acquisition_profiles():
    from app.modules.bdc.profiles import collection_summary, profiles
    for collection, item in [
        ({'id': 'landsat-2'}, {'id': 'scene', 'assets': {b: {} for b in ['red', 'green', 'blue', 'nir08', 'swir16']}}),
        ({'id': 'S2_L2A-1'}, {'id': 'scene_N0511_example', 'assets': {b: {} for b in ['B04', 'B03', 'B02', 'B08', 'B8A', 'B11']}}),
    ]:
        declared = collection_summary(collection)['profiles']
        actual = profiles(collection, item)
        assert declared and {p['id'] for p in declared} == {p['id'] for p in actual}
        for expected in actual:
            available = next(p for p in declared if p['id'] == expected['id'])
            assert {k: available[k] for k in ('label', 'bands', 'min', 'max', 'gamma')} == {
                k: expected[k] for k in ('label', 'bands', 'min', 'max', 'gamma')}
    assert all(p['offset'] is None for p in declared)


def test_denied_origin_admission_does_not_poison_transport_attempts(tmp_path, store):
    from app.modules.bdc.transport import Transport
    from app.modules.bdc.selection_jobs import OriginBusy
    remote = Transport(tmp_path, origin_admission=store.origin_admission)
    with store.origin_admission(), store.origin_admission():
        with pytest.raises(OriginBusy):
            remote.request('https://data.inpe.br/example')
    assert not list(tmp_path.glob('*.attempted'))
    remote.session.close()


def test_range_bridge_preserves_source_exception_instead_of_raster_error():
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from app.modules.bdc.transport import RangeBridge, ServicePaused
    class Remote:
        def request(self, *args):
            raise ServicePaused('paused')
    with pytest.raises(ServicePaused):
        with RangeBridge(Remote()) as bridge:
            try:
                urlopen(Request(bridge.url('https://data.inpe.br/file.tif'), headers={'Range': 'bytes=0-10'}))
            except HTTPError:
                raise OSError('raster read failed')


def test_claimed_job_rejects_mismatched_row_counts_even_when_marked_complete(store):
    job = store.submit(PARAMS, 'request')
    token = store.claim(job['_id'])
    with pytest.raises(ValueError):
        store.finish(job['_id'], token, result=dict(complete=True, rows=[], evaluated=2, eligible=1,
                                                   best_local_id='missing', best_global_id=None))
    assert store.get(job['_id'])['result'] is None


def test_dispatch_failure_is_recoverable_without_creating_another_job(store):
    job = store.submit(PARAMS, 'request')
    assert store.reserve_dispatch(job['_id'])
    assert not store.reserve_dispatch(job['_id'])
    store.dispatch_failed(job['_id'])
    assert store.reserve_dispatch(job['_id'])
    assert [row['_id'] for row in store.recoverable()] == [job['_id']]


def test_worker_no_eligible_candidate_is_distinct_from_failed_read(store):
    from app.tasks.bdc_selection import process_selection
    job = store.submit(PARAMS, 'request')
    class Service:
        def select_images(self, params, check):
            return dict(complete=True, best_local_id=None, best_global_id=None,
                        rows=[{'id': 'a', 'eligible': False}], eligible=0, evaluated=1)
    result = process_selection(store, job['_id'], Service())
    assert result['state'] == 'no_image' and result['result']['complete']
    assert result['error'] is None


def test_worker_local_admission_defers_without_remote_failure(store):
    from app.tasks.bdc_selection import process_selection
    from app.modules.bdc.selection_jobs import OriginBusy
    job = store.submit(PARAMS, 'request')
    class Service:
        def select_images(self, params, check):
            raise OriginBusy()
    result = process_selection(store, job['_id'], Service())
    assert result['state'] == 'pending' and result['error'] is None and result['result'] is None
    assert store.get(job['_id'])['attempts'] == 0


def test_worker_paused_origin_never_calls_service_or_retries_automatically(store):
    from app.tasks.bdc_selection import process_selection
    job = store.submit(PARAMS, 'request')
    store.pause_origin()
    class Service:
        def select_images(self, params, check):
            pytest.fail('paused origin called')
    result = process_selection(store, job['_id'], Service())
    assert result['state'] == 'failed'
    assert result['error'] == {'code': 'origin_paused', 'retryable': True}
    assert store.submit(PARAMS, 'request')['state'] == 'failed'
    assert store.submit(PARAMS, 'request', retry=True)['state'] == 'pending'
    assert process_selection(store, job['_id'], Service())['state'] == 'failed'


def test_origin_slots_are_recoverable_and_stale_owner_cannot_release_successor(store):
    from app.modules.bdc.selection_jobs import OriginBusy
    with store.origin_admission():
        store.origin.update_many({'token': {'$exists': True}}, {'$set': {'lease_until': datetime.now(timezone.utc) - timedelta(seconds=1)}})
        successor = store.origin_admission()
        successor.__enter__()
    try:
        with store.origin_admission():
            with pytest.raises(OriginBusy):
                with store.origin_admission():
                    pass
    finally:
        successor.__exit__(None, None, None)


def test_malformed_catalog_never_becomes_no_image():
    from app.modules.bdc.local_quality import compare_partitioned, IncompleteCatalogError
    class Remote:
        def json(self, url):
            return {'error': 'unavailable'}
    with pytest.raises(IncompleteCatalogError):
        compare_partitioned(Remote(), 'landsat-2', -49, -16, 1000, '2024-01-01', '2024-01-04')


def test_catalog_reported_total_prevents_silent_truncation():
    from app.modules.bdc.local_quality import compare_partitioned, IncompleteCatalogError
    class Remote:
        def json(self, url):
            return {'features': [], 'numberMatched': 1}
    with pytest.raises(IncompleteCatalogError):
        compare_partitioned(Remote(), 'landsat-2', -49, -16, 1000, '2024-01-01', '2024-01-01')


def test_service_result_contains_only_acquisition_metadata_not_asset_urls(monkeypatch, tmp_path):
    from contextlib import contextmanager
    from app.modules.bdc.service import BdcService
    service = BdcService(tmp_path)
    class Remote:
        def json(self, url):
            return {'features': [{'id': 'without-mask', 'properties': {'datetime': '2024-01-01T12:00:00Z'},
                                   'links': [{'href': 'https://private.example/secret'}]}]}
    @contextmanager
    def remote(*, retry_token=None):
        yield Remote()
    monkeypatch.setattr(service, 'remote', remote)
    result = service.select_images(PARAMS, lambda: None)
    assert result['complete'] and result['best_local_id'] is None
    assert result['rows'][0]['datetime'] == '2024-01-01T12:00:00Z'
    assert result['rows'][0]['profiles'] == []
    assert 'secret' not in str(result) and 'item' not in result['rows'][0]


def test_bdc_service_auth_is_scoped_and_fails_closed(monkeypatch, store):
    import importlib
    from contextlib import contextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.modules.bdc import selection_jobs
    auth = importlib.import_module('app.modules.bdc.auth')
    @contextmanager
    def factory():
        yield store
    app = FastAPI()
    app.include_router(selection_jobs.create_selection_router(factory, lambda _: None))
    client = TestClient(app)
    token = 'test-service-token-with-sufficient-entropy'
    monkeypatch.setattr(auth, 'service_token', lambda: None)
    payload = dict(PARAMS, idempotency_key='service')
    response = client.post('/selection-jobs', json=payload, headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 503 and response.json()['detail']['code'] == 'bdc_service_auth_unconfigured'
    monkeypatch.setattr(auth, 'service_token', lambda: token)
    assert client.post('/selection-jobs', json=payload, headers={'Authorization': 'Bearer incorrect'}).status_code == 401
    response = client.post('/selection-jobs', json=payload, headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 202
    assert client.get('/selection-jobs/' + response.json()['id'], headers={'Authorization': 'Bearer ' + token}).status_code == 200
    assert token not in response.text


def test_origin_503_pauses_other_replicas_without_outbound_retry(tmp_path, store, monkeypatch):
    import requests
    from app.modules.bdc.transport import Transport, ServicePaused
    calls = []
    def request(*args, **kwargs):
        calls.append(True)
        response = requests.Response()
        response.status_code = 503
        response._content = b'upstream unavailable'
        response._content_consumed = True
        return response
    monkeypatch.setattr(requests.Session, 'request', request)
    first = Transport(tmp_path / 'first', origin_admission=store.origin_admission)
    second = Transport(tmp_path / 'second', origin_admission=store.origin_admission)
    try:
        for remote in (first, second):
            with pytest.raises(ServicePaused):
                remote.request('https://data.inpe.br/example')
        assert len(calls) == 1
        assert not list((tmp_path / 'second').glob('*.attempted'))
    finally:
        first.session.close()
        second.session.close()


def test_cached_response_remains_available_during_origin_pause(tmp_path, store, monkeypatch):
    import requests
    from app.modules.bdc.transport import Transport
    calls = []
    def request(*args, **kwargs):
        calls.append(True)
        response = requests.Response()
        response.status_code = 200
        response._content = b'cached content'
        response._content_consumed = True
        return response
    monkeypatch.setattr(requests.Session, 'request', request)
    remote = Transport(tmp_path, origin_admission=store.origin_admission)
    try:
        assert remote.request('https://data.inpe.br/example')[1] == b'cached content'
        store.pause_origin()
        assert remote.request('https://data.inpe.br/example')[1] == b'cached content'
        assert len(calls) == 1
    finally:
        remote.session.close()


def test_service_cached_catalog_does_not_depend_on_origin_coordinator(tmp_path, monkeypatch):
    import requests
    from app.modules.bdc import selection_jobs
    from app.modules.bdc.catalog import STAC
    from app.modules.bdc.service import BdcService
    from app.modules.bdc.transport import Transport
    def request(*args, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"collections": [{"id": "landsat-2"}]}'
        response._content_consumed = True
        return response
    monkeypatch.setattr(requests.Session, 'request', request)
    cached = Transport(tmp_path / 'http')
    try:
        cached.request(STAC + '/collections')
    finally:
        cached.session.close()
    monkeypatch.setattr(selection_jobs, 'selection_store', lambda: pytest.fail('cached read contacted MongoDB'))
    catalog = BdcService(tmp_path).catalog()
    assert catalog['collections'][0]['id'] == 'landsat-2'
    assert catalog['collections'][0]['profiles']


def test_service_token_accepts_mounted_secret_and_process_value_has_priority(tmp_path, monkeypatch):
    from app.modules.bdc.auth import service_token
    path = tmp_path / 'bdc-secret'
    path.write_text('test-mounted-token-with-sufficient-entropy\n')
    monkeypatch.delenv('BDC_SERVICE_TOKEN', raising=False)
    monkeypatch.setenv('BDC_SERVICE_TOKEN_FILE', str(path))
    assert service_token() == 'test-mounted-token-with-sufficient-entropy'
    monkeypatch.setenv('BDC_SERVICE_TOKEN', 'test-process-token-with-sufficient-entropy')
    assert service_token() == 'test-process-token-with-sufficient-entropy'
    monkeypatch.delenv('BDC_SERVICE_TOKEN')
    path.unlink()
    assert service_token() is None


def test_explicit_origin_resume_releases_old_replica_pause_and_failed_attempt(tmp_path, store, monkeypatch):
    import requests
    from app.modules.bdc.transport import Transport, ServicePaused
    calls = []
    def request(*args, **kwargs):
        calls.append(True)
        response = requests.Response()
        response.status_code = 503 if len(calls) == 1 else 200
        response._content = b'content'
        response._content_consumed = True
        return response
    monkeypatch.setattr(requests.Session, 'request', request)
    remote = Transport(tmp_path, origin_admission=store.origin_admission, origin_resumed=store.origin_resumed_since)
    try:
        with pytest.raises(ServicePaused):
            remote.request('https://data.inpe.br/example')
        store.resume_origin()
        assert remote.request('https://data.inpe.br/example')[1] == b'content'
        assert len(calls) == 2 and not (tmp_path / 'data.inpe.br.paused').exists()
    finally:
        remote.session.close()


def test_resume_command_only_releases_origin_without_requeueing_jobs(store, monkeypatch, capsys):
    from contextlib import contextmanager
    from app.tasks import bdc_selection
    @contextmanager
    def factory():
        yield store
    job = store.submit(PARAMS, 'request')
    token = store.claim(job['_id'])
    store.finish(job['_id'], token, error={'code': 'origin_paused', 'retryable': True})
    store.pause_origin()
    monkeypatch.setattr(bdc_selection, 'selection_store', factory)
    bdc_selection.main(['resume-origin'])
    store.check_origin()
    assert store.get(job['_id'])['state'] == 'failed'
    assert 'liberada' in capsys.readouterr().out
