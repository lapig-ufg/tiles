import importlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import fakeredis
import pytest


def test_startup_waits_for_shared_capacity_without_bypassing_limit(monkeypatch):
    admission = importlib.import_module('app.core.gee_admission')
    coordinator = admission.RedisAdmission(fakeredis.FakeRedis(), admission.AdmissionLimits(
        max_concurrent=1, rate_per_second=20, interactive_reserve=0,
        interactive_rate_reserve=0, wait_seconds=0,
    ))
    monkeypatch.setattr(admission, 'settings', {'GEE_STARTUP_TIMEOUT_SECONDS': 1})
    occupied = coordinator.acquire()
    waiting = threading.Event()

    def start():
        with admission.gee_startup():
            waiting.set()
            lease = coordinator.acquire(background=True)
            lease.release()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(start)
        try:
            assert waiting.wait(1)
            assert not future.done()
            assert coordinator.redis.zcard('gee:{admission}:shared:active') == 1
        finally:
            occupied.release()
        future.result(timeout=2)
    first = coordinator.acquire()
    try:
        with pytest.raises(admission.GoogleAdmissionError):
            coordinator.acquire()
    finally:
        first.release()


def test_startup_has_finite_deadline_and_restores_context(monkeypatch):
    admission = importlib.import_module('app.core.gee_admission')
    monkeypatch.setattr(admission, 'settings', {'GEE_STARTUP_TIMEOUT_SECONDS': .02})
    coordinator = admission.RedisAdmission(fakeredis.FakeRedis(), admission.AdmissionLimits(
        max_concurrent=1, rate_per_second=20, interactive_reserve=0,
        interactive_rate_reserve=0, wait_seconds=0,
    ))
    occupied = coordinator.acquire()
    try:
        with pytest.raises(admission.GoogleAdmissionError):
            with admission.gee_startup():
                coordinator.acquire()
        assert admission.startup_remaining_seconds() is None
    finally:
        occupied.release()


def test_failed_initialization_releases_assignment_and_can_retry(monkeypatch):
    auth = importlib.import_module('app.core.gee_auth')
    pool_module = importlib.import_module('app.core.gee_pool')
    monkeypatch.setattr(auth, '_manager', None)
    monkeypatch.setattr(auth, 'settings', {'TILES_ENV': 'production'})
    pool = Mock()
    pool.acquire.return_value = SimpleNamespace(credentials=object(), name='test')
    monkeypatch.setattr(pool_module.ServiceAccountPool, 'get_instance', lambda: pool)
    initialize = Mock(side_effect=[RuntimeError('initialization failed'), None])
    monkeypatch.setattr(pool_module, 'initialize_gee_transport', initialize)
    with pytest.raises(RuntimeError, match='initialization failed'):
        auth.initialize_earth_engine('test-worker')
    assert auth.get_gee_manager() is None
    pool.release.assert_called_once_with('test-worker')
    try:
        auth.initialize_earth_engine('test-worker')
        assert auth.get_gee_manager() is not None
        assert initialize.call_count == 2
    finally:
        auth.shutdown_earth_engine()


def test_concurrent_initializers_publish_only_one_ready_manager(monkeypatch):
    auth = importlib.import_module('app.core.gee_auth')
    pool_module = importlib.import_module('app.core.gee_pool')
    monkeypatch.setattr(auth, '_manager', None)
    monkeypatch.setattr(auth, 'settings', {'TILES_ENV': 'production'})
    pool = Mock()
    pool.acquire.return_value = SimpleNamespace(credentials=object(), name='test')
    monkeypatch.setattr(pool_module.ServiceAccountPool, 'get_instance', lambda: pool)
    entered, finish = threading.Event(), threading.Event()

    def initialize(credentials):
        entered.set()
        assert finish.wait(2)

    monkeypatch.setattr(pool_module, 'initialize_gee_transport', initialize)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(auth.initialize_earth_engine, 'test-worker')
        try:
            assert entered.wait(1)
            assert auth.get_gee_manager() is None
            second = executor.submit(auth.initialize_earth_engine, 'test-worker')
        finally:
            finish.set()
        first.result(timeout=2)
        second.result(timeout=2)
    try:
        assert pool.acquire.call_count == 1
    finally:
        auth.shutdown_earth_engine()


def test_startup_transport_caps_http_timeout_and_uses_background_budget(monkeypatch):
    admission = importlib.import_module('app.core.gee_admission')
    import requests

    monkeypatch.setattr(admission, 'settings', {'GEE_STARTUP_TIMEOUT_SECONDS': .5})
    coordinator = admission.RedisAdmission(fakeredis.FakeRedis(), admission.AdmissionLimits())
    monkeypatch.setattr(admission, 'get_admission_controller', lambda: coordinator)
    timeouts = []

    def request(session, method, uri, **kwargs):
        timeouts.append(kwargs['timeout'])
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        return response

    monkeypatch.setattr(requests.Session, 'request', request)
    transport = admission.GoogleAdmissionTransport()
    with admission.gee_startup():
        transport.request('https://earthengine.googleapis.com/v1/projects/test/algorithms')
    assert 0 < timeouts[0] <= .5
    assert coordinator.redis.zcard('gee:{admission}:shared:rate_bg') == 1
    assert coordinator.redis.zcard('gee:{admission}:shared:active') == 0
    transport.request('https://earthengine.googleapis.com/v1/projects/test/maps')
    assert timeouts[1] == 60
    assert coordinator.redis.zcard('gee:{admission}:shared:rate_bg') == 1


def test_startup_waits_for_service_account_cooldown(monkeypatch):
    admission = importlib.import_module('app.core.gee_admission')
    pool_module = importlib.import_module('app.core.gee_pool')
    pool = object.__new__(pool_module.ServiceAccountPool)
    pool._redis = Mock()
    pool._accounts = {'test': object()}
    pool._redis.zrange.return_value = [('test', 0)]
    pool._redis.hget.return_value = str(time.time() + .1)
    pool._assign = Mock(return_value='assigned')
    monkeypatch.setattr(pool_module, 'settings', {'GEE_SA_ACQUIRE_TIMEOUT_SECONDS': 0})
    monkeypatch.setattr(admission, 'settings', {'GEE_STARTUP_TIMEOUT_SECONDS': 1})
    with pytest.raises(pool_module.PoolExhaustedError):
        pool.acquire('worker')
    with admission.gee_startup():
        assert pool.acquire('worker') == 'assigned'
    pool._assign.assert_called_once_with('test', 'worker')


def test_celery_does_not_accept_tasks_after_initialization_failure(monkeypatch):
    auth = importlib.import_module('app.core.gee_auth')
    tasks = importlib.import_module('app.tasks.celery_app')
    monkeypatch.setattr(auth, 'initialize_earth_engine', Mock(side_effect=RuntimeError('failed')))
    with pytest.raises(SystemExit):
        tasks.worker_process_init.send(sender=None)


def test_assignment_heartbeat_covers_sdk_initialization(monkeypatch):
    pool_module = importlib.import_module('app.core.gee_pool')
    pool = Mock()
    pool.acquire.return_value = SimpleNamespace(credentials=object(), name='test')
    manager = pool_module.WorkerGEEManager(pool)

    def initialize(credentials):
        assert manager._heartbeat_thread is not None
        assert manager._heartbeat_thread.is_alive()

    monkeypatch.setattr(pool_module, 'initialize_gee_transport', initialize)
    try:
        manager.initialize('test-worker')
    finally:
        manager.shutdown()


def test_celery_keeps_local_tasks_available_when_google_admission_is_paused(monkeypatch):
    auth = importlib.import_module('app.core.gee_auth')
    tasks = importlib.import_module('app.tasks.celery_app')
    admission = importlib.import_module('app.core.gee_admission')
    loop = importlib.import_module('app.tasks._loop')
    monkeypatch.setattr(auth, 'initialize_earth_engine', Mock(
        side_effect=admission.GoogleAdmissionError(900, 'project_restricted')))
    monkeypatch.setattr(loop, 'run_async', lambda coroutine: coroutine.close())
    tasks._init_gee_for_celery_worker()
