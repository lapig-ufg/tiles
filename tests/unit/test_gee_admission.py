import asyncio
import importlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import fakeredis
import pytest


@pytest.fixture
def admission():
    return importlib.import_module('app.core.gee_admission')


@pytest.fixture
def coordinator(admission):
    client = fakeredis.FakeRedis(server=fakeredis.FakeServer(), decode_responses=True)
    limits = admission.AdmissionLimits(
        max_concurrent=2, rate_per_second=20, interactive_reserve=1,
        interactive_rate_reserve=2, wait_seconds=0, lease_seconds=30,
    )
    return admission.RedisAdmission(client, limits)


def test_two_consumers_share_capacity_and_owner_release(admission, coordinator):
    other = admission.RedisAdmission(coordinator.redis, coordinator.limits)
    first = coordinator.acquire(project='p')
    second = other.acquire(project='p')
    try:
        with pytest.raises(admission.GoogleAdmissionError) as error:
            other.acquire(project='p')
        assert error.value.retry_after > 0
        first.release()
        replacement = other.acquire(project='p')
        first.release()
        with pytest.raises(admission.GoogleAdmissionError):
            coordinator.acquire(project='p')
        replacement.release()
    finally:
        first.release()
        second.release()


def test_background_keeps_capacity_for_interactive(admission, coordinator):
    background = coordinator.acquire(project='p', background=True)
    try:
        with pytest.raises(admission.GoogleAdmissionError):
            coordinator.acquire(project='p', background=True)
        interactive = coordinator.acquire(project='p')
        interactive.release()
    finally:
        background.release()


def test_known_and_unknown_project_share_conservative_ceiling(admission, coordinator):
    first = coordinator.acquire(project='p')
    second = coordinator.acquire()
    try:
        with pytest.raises(admission.GoogleAdmissionError):
            coordinator.acquire(project='another-project')
    finally:
        first.release()
        second.release()


def test_atomic_capacity_under_thread_contention(admission, coordinator):
    release = threading.Event()
    admitted = []
    lock = threading.Lock()

    def work():
        try:
            lease = coordinator.acquire(project='p')
        except admission.GoogleAdmissionError:
            return False
        with lock:
            admitted.append(lease)
        release.wait(3)
        lease.release()
        return True

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(work) for _ in range(12)]
        deadline = time.monotonic() + 2
        while sum(f.done() for f in futures) < 10 and time.monotonic() < deadline:
            time.sleep(.005)
        release.set()
        assert sum(f.result() for f in futures) == 2


def test_release_does_not_refund_rate_or_background_reservation(admission):
    coordinator = admission.RedisAdmission(
        fakeredis.FakeRedis(decode_responses=True),
        admission.AdmissionLimits(max_concurrent=2, rate_per_second=3,
                                  interactive_reserve=1, interactive_rate_reserve=1,
                                  wait_seconds=0),
    )
    coordinator.acquire(background=True).release()
    coordinator.acquire(background=True).release()
    with pytest.raises(admission.GoogleAdmissionError):
        coordinator.acquire(background=True)
    coordinator.acquire().release()
    with pytest.raises(admission.GoogleAdmissionError):
        coordinator.acquire()


def test_expired_owner_cannot_release_successor(admission, coordinator):
    lease = coordinator.acquire(project='p')
    lease._stop.set()
    for key in coordinator.redis.scan_iter('*:active*'):
        coordinator.redis.zadd(key, {lease.token: 0})
    replacement = coordinator.acquire(project='p')
    other = coordinator.acquire(project='p')
    try:
        lease.release()
        with pytest.raises(admission.GoogleAdmissionError):
            coordinator.acquire(project='p')
    finally:
        replacement.release()
        other.release()


def test_429_pause_is_shared_without_blocking_distinct_project(admission, coordinator):
    lease = coordinator.acquire(project='p')
    lease.response(429, '8')
    lease.release()
    other = admission.RedisAdmission(coordinator.redis, coordinator.limits)
    with pytest.raises(admission.GoogleAdmissionError) as error:
        other.acquire(project='p')
    assert 7 <= error.value.retry_after <= 8
    other.acquire(project='q').release()


def test_shorter_retry_after_does_not_shorten_shared_pause(admission, coordinator):
    first = coordinator.acquire(project='p')
    second = coordinator.acquire(project='p')
    first.response(429, '30')
    second.response(429, '1')
    first.release()
    second.release()
    with pytest.raises(admission.GoogleAdmissionError) as error:
        coordinator.acquire(project='p')
    assert error.value.retry_after > 29


@pytest.mark.parametrize('value', ['invalid', '-1', 'nan', 'inf'])
def test_invalid_retry_after_uses_bounded_fallback(admission, coordinator, value):
    lease = coordinator.acquire()
    lease.response(429, value)
    lease.release()
    with pytest.raises(admission.GoogleAdmissionError) as error:
        coordinator.acquire(project='p')
    assert 4 <= error.value.retry_after <= 5


def test_retry_after_http_date(admission, coordinator):
    lease = coordinator.acquire(project='p')
    lease.response(429, format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20)))
    lease.release()
    with pytest.raises(admission.GoogleAdmissionError) as error:
        coordinator.acquire(project='p')
    assert 18 <= error.value.retry_after <= 20


def test_redis_failure_blocks_external_work(admission, coordinator, monkeypatch):
    coordinator.redis.connection_pool.connection_kwargs['server'].connected = False
    monkeypatch.setattr(admission, 'get_admission_controller', lambda: coordinator)
    called = []
    with pytest.raises(admission.GoogleAdmissionError) as error:
        admission.call_gee(lambda: called.append(True))
    assert error.value.reason == 'coordinator_unavailable'
    assert called == []


@pytest.mark.parametrize('changes', [
    {'max_concurrent': 0}, {'rate_per_second': -1}, {'wait_seconds': -1},
    {'lease_seconds': 0}, {'interactive_reserve': 20},
    {'interactive_rate_reserve': 50}, {'wait_seconds': float('nan')},
    {'rate_per_second': 1.5},
])
def test_invalid_limits_are_rejected(admission, changes):
    with pytest.raises(ValueError):
        admission.AdmissionLimits(**changes)


@pytest.mark.asyncio
async def test_async_cancel_releases_after_http_context_closes(admission, coordinator, monkeypatch):
    monkeypatch.setattr(admission, 'get_admission_controller', lambda: coordinator)
    started = asyncio.Event()
    closing = asyncio.Event()
    allow_close = asyncio.Event()

    async def request():
        async with admission.async_google_request(project='p'):
            try:
                started.set()
                await asyncio.Future()
            finally:
                closing.set()
                await allow_close.wait()

    task = asyncio.create_task(request())
    await started.wait()
    task.cancel()
    await closing.wait()
    second = coordinator.acquire(project='p')
    try:
        with pytest.raises(admission.GoogleAdmissionError):
            coordinator.acquire(project='p')
        allow_close.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        coordinator.acquire(project='p').release()
    finally:
        second.release()


@pytest.mark.asyncio
async def test_sdk_lease_survives_cancellation_of_outer_coroutine(admission, coordinator, monkeypatch):
    monkeypatch.setattr(admission, 'get_admission_controller', lambda: coordinator)
    started = threading.Event()
    finish = threading.Event()
    completed = threading.Event()

    def sdk():
        started.set()
        finish.wait(3)

    def invoke():
        try:
            admission.call_gee(sdk, project='p')
        finally:
            completed.set()

    task = asyncio.create_task(asyncio.to_thread(invoke))
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    second = coordinator.acquire(project='p')
    try:
        with pytest.raises(admission.GoogleAdmissionError):
            coordinator.acquire(project='p')
        finish.set()
        await asyncio.to_thread(completed.wait, 2)
        coordinator.acquire(project='p').release()
    finally:
        finish.set()
        second.release()


@pytest.mark.asyncio
async def test_cancel_during_acquire_releases_eventual_grant(admission, coordinator, monkeypatch):
    entered = threading.Event()
    finish = threading.Event()
    original = coordinator._attempt

    def delayed(*args, **kwargs):
        lease = original(*args, **kwargs)
        entered.set()
        finish.wait(3)
        return lease

    monkeypatch.setattr(coordinator, '_attempt', delayed)
    monkeypatch.setattr(admission, 'get_admission_controller', lambda: coordinator)

    async def request():
        async with admission.async_google_request(project='p'):
            pytest.fail('Requisição cancelada não deve iniciar chamada externa')

    task = asyncio.create_task(request())
    await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    first = coordinator.acquire(project='p')
    second = coordinator.acquire(project='p')
    first.release()
    second.release()


def test_live_lease_is_renewed_during_slow_request(admission):
    coordinator = admission.RedisAdmission(
        fakeredis.FakeRedis(decode_responses=True),
        admission.AdmissionLimits(max_concurrent=1, interactive_reserve=0,
                                  lease_seconds=.15, wait_seconds=0),
    )
    lease = coordinator.acquire()
    try:
        time.sleep(.35)
        with pytest.raises(admission.GoogleAdmissionError):
            coordinator.acquire()
    finally:
        lease.release()


def test_pause_expires_and_admission_recovers(admission):
    coordinator = admission.RedisAdmission(
        fakeredis.FakeRedis(decode_responses=True),
        admission.AdmissionLimits(wait_seconds=.2),
    )
    lease = coordinator.acquire(project='p')
    lease.response(429, '.05')
    lease.release()
    started = time.monotonic()
    coordinator.acquire(project='p').release()
    assert .03 <= time.monotonic() - started < .3


def test_sdk_transport_charges_each_retry_and_honors_project_header(admission, coordinator, monkeypatch):
    import requests
    from googleapiclient.http import HttpRequest

    limits = admission.AdmissionLimits(max_concurrent=1, interactive_reserve=0,
                                      wait_seconds=.2)
    coordinator = admission.RedisAdmission(coordinator.redis, limits)
    monkeypatch.setattr(admission, 'get_admission_controller', lambda: coordinator)
    attempts = []

    def network(http, method, uri, data=None, headers=None, **kwargs):
        attempts.append((uri, headers['Authorization']))
        with pytest.raises(admission.GoogleAdmissionError):
            coordinator.acquire(project='billing')
        if len(attempts) == 1:
            return http_response(429, b'{}', {'retry-after': '.01'})
        return http_response(200, b'PNG')

    monkeypatch.setattr(requests.Session, 'request', network)
    request = HttpRequest(
        admission.GoogleAdmissionTransport(), lambda response, body: body,
        'https://earthengine.googleapis.com/v1/projects/resource/maps',
        headers={'X-Goog-User-Project': 'billing', 'Authorization': 'Bearer test-token'},
    )
    request._sleep = lambda seconds: None
    assert request.execute(num_retries=1) == b'PNG'
    assert len(attempts) == 2
    assert coordinator.redis.zcard('gee:{admission}:shared:rate') == 2


def test_sdk_transport_keeps_oauth_outside_earth_engine_budget(admission, coordinator, monkeypatch):
    import requests

    monkeypatch.setattr(admission, 'get_admission_controller', lambda: coordinator)
    monkeypatch.setattr(requests.Session, 'request', lambda *args, **kwargs:
                        http_response(200, b'token-response'))
    response, body = admission.GoogleAdmissionTransport().request('https://oauth2.googleapis.com/token')
    assert body == b'token-response'
    assert coordinator.redis.zcard('gee:{admission}:shared:rate') == 0


def test_installed_transport_avoids_nested_admission(admission, coordinator, monkeypatch):
    import ee
    import requests

    initialized = []
    monkeypatch.setattr(ee, 'Initialize', lambda credentials, **kwargs:
                        initialized.append((credentials, kwargs['http_transport'])), raising=False)
    monkeypatch.setattr(admission, 'get_admission_controller', lambda: coordinator)
    monkeypatch.setattr(requests.Session, 'request', lambda *args, **kwargs:
                        http_response(200, b'PNG'))
    credential = object()
    admission.initialize_gee_transport(credential)
    assert initialized[0][0] is credential
    transport = initialized[0][1]
    result = admission.call_gee(
        transport.request, 'https://earthengine.googleapis.com/v1/projects/p/maps',
        operation='map', background=True,
    )
    assert result[1] == b'PNG'
    assert coordinator.redis.zcard('gee:{admission}:shared:rate') == 1
    assert coordinator.redis.zcard('gee:{admission}:shared:rate_bg') == 1


def http_response(status, content, headers=None):
    import requests

    response = requests.Response()
    response.status_code = status
    response._content = content
    response.headers.update(headers or {})
    return response


def test_quota_error_does_not_rotate_service_account(monkeypatch):
    import ee
    from app.core import gee_auth, gee_pool

    calls = []

    class Manager:
        init_lock = threading.RLock()

        def rotate_on_429(self):
            calls.append('rotation')

    monkeypatch.setattr(gee_auth, 'get_gee_manager', lambda: Manager())

    @gee_pool.gee_retry()
    def compute():
        calls.append('compute')
        raise ee.EEException('429 quota exceeded')

    with pytest.raises(ee.EEException):
        compute()
    assert calls == ['compute']


def test_metrics_observe_failure_after_headers_and_never_use_resource_identifiers(admission, coordinator, monkeypatch):
    from prometheus_client import REGISTRY

    monkeypatch.setattr(admission, 'get_admission_controller', lambda: coordinator)
    labels = {'operation': 'download', 'traffic': 'interactive', 'status': 'error'}
    before = REGISTRY.get_sample_value('gee_external_attempts_total', labels) or 0
    with pytest.raises(TimeoutError):
        with admission.google_request(project='private-project') as lease:
            lease.response(200)
            raise TimeoutError
    assert REGISTRY.get_sample_value('gee_external_attempts_total', labels) == before + 1
    assert REGISTRY.get_sample_value('gee_external_active', {
        'operation': 'download', 'traffic': 'interactive',
    }) == 0
    for metric in REGISTRY.collect():
        if metric.name.startswith('gee_external'):
            for sample in metric.samples:
                assert set(sample.labels) <= {'operation', 'traffic', 'status'}


def test_redis_release_failure_keeps_concession_until_expiry(admission, coordinator):
    lease = coordinator.acquire(project='p')
    server = coordinator.redis.connection_pool.connection_kwargs['server']
    server.connected = False
    lease.release()
    server.connected = True
    other = coordinator.acquire(project='p')
    try:
        with pytest.raises(admission.GoogleAdmissionError):
            coordinator.acquire(project='p')
    finally:
        other.release()


def test_wait_is_bounded_when_capacity_stays_full(admission):
    coordinator = admission.RedisAdmission(
        fakeredis.FakeRedis(decode_responses=True),
        admission.AdmissionLimits(max_concurrent=1, interactive_reserve=0, wait_seconds=.12),
    )
    lease = coordinator.acquire()
    started = time.monotonic()
    try:
        with pytest.raises(admission.GoogleAdmissionError):
            coordinator.acquire()
        assert time.monotonic() - started < .25
    finally:
        lease.release()


@pytest.mark.asyncio
async def test_redis_coordination_does_not_queue_behind_long_sdk_calls(admission, coordinator, monkeypatch):
    monkeypatch.setattr(admission, 'get_admission_controller', lambda: coordinator)
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(executor)
    started = threading.Event()
    finish = threading.Event()

    def slow_sdk():
        started.set()
        finish.wait(2)

    work = loop.run_in_executor(None, slow_sdk)
    while not started.is_set():
        await asyncio.sleep(.001)
    timer = loop.call_later(.3, finish.set)
    before = time.monotonic()
    try:
        async with admission.async_google_request():
            elapsed = time.monotonic() - before
        assert elapsed < .15
    finally:
        timer.cancel()
        finish.set()
        await work


def test_prefork_worker_does_not_inherit_controller_thread_state(admission, monkeypatch):
    import os

    monkeypatch.setattr(os, 'getpid', lambda: 1001)
    first = admission.get_admission_controller()
    monkeypatch.setattr(os, 'getpid', lambda: 1002)
    second = admission.get_admission_controller()
    assert second is not first


def test_sdk_connection_failure_never_retries_inside_one_admission(admission, coordinator, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from googleapiclient.http import HttpRequest

    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            if len(calls) == 1:
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header('Content-Length', '3')
            self.end_headers()
            self.wfile.write(b'PNG')

        def log_message(self, *args):
            pass

    monkeypatch.setattr(admission, 'get_admission_controller', lambda: coordinator)
    monkeypatch.setattr(admission, '_EE_HOSTS', {'127.0.0.1'})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = HttpRequest(
            admission.GoogleAdmissionTransport(), lambda response, body: body,
            f'http://127.0.0.1:{server.server_port}/v1/projects/p/tile',
        )
        request._sleep = lambda seconds: None
        assert request.execute(num_retries=1) == b'PNG'
        assert calls == ['/v1/projects/p/tile', '/v1/projects/p/tile']
        assert coordinator.redis.zcard('gee:{admission}:shared:rate') == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
