"""Admissão distribuída das chamadas ao Earth Engine."""
from __future__ import annotations

import asyncio
import hashlib
import math
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache, partial
from urllib.parse import urlsplit

import httplib2
import redis
import requests

from app.core.config import REDIS_URL, logger, settings
from app.core.metrics import (
    gee_admission_rejected_total,
    gee_admission_wait_seconds,
    gee_external_active,
    gee_external_attempts_total,
)


_ACQUIRE = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local token, ttl = ARGV[1], tonumber(ARGV[2])
local concurrency, rate = tonumber(ARGV[3]), tonumber(ARGV[4])
local bg_concurrency, bg_rate = tonumber(ARGV[5]), tonumber(ARGV[6])
local background = ARGV[7] == '1'
for i = 1, #KEYS, 5 do
    redis.call('ZREMRANGEBYSCORE', KEYS[i], '-inf', now)
    redis.call('ZREMRANGEBYSCORE', KEYS[i+1], '-inf', now-1000)
    redis.call('ZREMRANGEBYSCORE', KEYS[i+2], '-inf', now)
    redis.call('ZREMRANGEBYSCORE', KEYS[i+3], '-inf', now-1000)
    local pause = redis.call('PTTL', KEYS[i+4])
    if pause > 0 then return {0, pause, 'cooldown'} end
    if redis.call('ZCARD', KEYS[i]) >= concurrency or
       (background and redis.call('ZCARD', KEYS[i+2]) >= bg_concurrency) then
        return {0, 50, 'concurrency'}
    end
    local rate_key = KEYS[i+1]
    if background and redis.call('ZCARD', KEYS[i+3]) >= bg_rate then
        rate_key = KEYS[i+3]
    elseif redis.call('ZCARD', rate_key) < rate then
        rate_key = nil
    end
    if rate_key then
        local oldest = redis.call('ZRANGE', rate_key, 0, 0, 'WITHSCORES')
        return {0, math.max(1, tonumber(oldest[2])+1000-now), 'rate'}
    end
end
for i = 1, #KEYS, 5 do
    redis.call('ZADD', KEYS[i], now+ttl, token)
    redis.call('PEXPIRE', KEYS[i], ttl)
    redis.call('ZADD', KEYS[i+1], now, token)
    redis.call('PEXPIRE', KEYS[i+1], 1000)
    if background then
        redis.call('ZADD', KEYS[i+2], now+ttl, token)
        redis.call('PEXPIRE', KEYS[i+2], ttl)
        redis.call('ZADD', KEYS[i+3], now, token)
        redis.call('PEXPIRE', KEYS[i+3], 1000)
    end
end
return {1, 0, 'admitted'}
"""

_RELEASE = """
for i = 1, #KEYS, 5 do
    redis.call('ZREM', KEYS[i], ARGV[1])
    redis.call('ZREM', KEYS[i+2], ARGV[1])
end
return 1
"""

_RENEW = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local ttl = tonumber(ARGV[2])
for i = 1, #KEYS, 5 do
    local expires = redis.call('ZSCORE', KEYS[i], ARGV[1])
    if not expires or tonumber(expires) <= now then return 0 end
end
for i = 1, #KEYS, 5 do
    redis.call('ZADD', KEYS[i], now+ttl, ARGV[1])
    redis.call('PEXPIRE', KEYS[i], ttl)
    if redis.call('ZSCORE', KEYS[i+2], ARGV[1]) then
        redis.call('ZADD', KEYS[i+2], now+ttl, ARGV[1])
        redis.call('PEXPIRE', KEYS[i+2], ttl)
    end
end
return 1
"""

_PAUSE = """
local duration = tonumber(ARGV[1])
if redis.call('PTTL', KEYS[1]) < duration then
    redis.call('SET', KEYS[1], '1', 'PX', duration)
end
return 1
"""


class GoogleAdmissionError(RuntimeError):
    def __init__(self, retry_after: float, reason: str = 'saturated'):
        self.retry_after = max(.001, float(retry_after))
        self.reason = reason
        super().__init__('Orçamento de chamadas ao Earth Engine indisponível')


@dataclass(frozen=True)
class AdmissionLimits:
    max_concurrent: int = 20
    rate_per_second: int = 50
    interactive_reserve: int = 15
    interactive_rate_reserve: int = 40
    wait_seconds: float = 8
    background_wait_seconds: float = 2
    lease_seconds: float = 60
    cooldown_seconds: float = 5

    def __post_init__(self):
        for name in ('max_concurrent', 'rate_per_second', 'interactive_reserve',
                     'interactive_rate_reserve'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f'{name} deve ser inteiro')
        if not 0 <= self.interactive_reserve < self.max_concurrent:
            raise ValueError('Reserva interativa incompatível com a concorrência')
        if not 0 <= self.interactive_rate_reserve < self.rate_per_second:
            raise ValueError('Reserva interativa incompatível com a taxa')
        for name in ('wait_seconds', 'background_wait_seconds', 'lease_seconds', 'cooldown_seconds'):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0 or (name not in ('wait_seconds', 'background_wait_seconds') and value == 0):
                raise ValueError(f'{name} inválido')


def _retry_after(value, fallback: float) -> float:
    if value is not None:
        try:
            seconds = float(value)
        except (ValueError, TypeError):
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                seconds = max(0, (date - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                seconds = fallback
        if math.isfinite(seconds) and seconds >= 0:
            return max(.001, seconds)
    return fallback


def _labels(operation, background):
    return {
        'operation': operation if operation in {'download', 'map', 'compute', 'sdk'} else 'other',
        'traffic': 'background' if background else 'interactive',
    }


class RedisAdmission:
    def __init__(self, redis_client, limits: AdmissionLimits):
        self.redis = redis_client
        self.limits = limits
        self._pending = threading.BoundedSemaphore(32)

    def _wait_seconds(self, background):
        if background:
            return min(self.limits.background_wait_seconds, self.limits.wait_seconds)
        return self.limits.wait_seconds

    def _keys(self, project):
        # O teto comum inclui chamadas sem projeto comprovado e evita somar sua cota à de REST.
        budgets = ['shared']
        if project:
            budgets.append(hashlib.sha256(str(project).encode()).hexdigest())
        return [f'gee:{{admission}}:{budget}:{suffix}' for budget in budgets
                for suffix in ('active', 'rate', 'active_bg', 'rate_bg', 'pause')]

    def acquire(self, *, project=None, operation='download', background=False):
        keys = self._keys(project)
        token = uuid.uuid4().hex
        labels = _labels(operation, background)
        if not self._pending.acquire(blocking=False):
            gee_admission_rejected_total.labels(**labels, reason='local_queue').inc()
            raise GoogleAdmissionError(1, 'local_queue')
        started = time.monotonic()
        deadline = _startup_deadline.get()
        if deadline is None:
            deadline = started + self._wait_seconds(background)
        try:
            while True:
                startup_remaining_seconds()
                result = self._attempt(keys, token, labels, background)
                if isinstance(result, GoogleLease):
                    return result
                time.sleep(self._delay(result, deadline))
        except GoogleAdmissionError as exc:
            gee_admission_rejected_total.labels(**labels, reason=exc.reason).inc()
            raise
        finally:
            self._pending.release()
            gee_admission_wait_seconds.labels(**labels).observe(time.monotonic() - started)

    def _attempt(self, keys, token, labels, background):
        limits = self.limits
        try:
            result = self.redis.eval(
                _ACQUIRE, len(keys), *keys, token, math.ceil(limits.lease_seconds * 1000),
                limits.max_concurrent, limits.rate_per_second,
                limits.max_concurrent - limits.interactive_reserve,
                limits.rate_per_second - limits.interactive_rate_reserve, int(background),
            )
        except redis.RedisError as exc:
            raise GoogleAdmissionError(1, 'coordinator_unavailable') from exc
        return GoogleLease(self, keys, token, labels) if result[0] else result

    @staticmethod
    def _delay(result, deadline):
        _, delay_ms, reason = result
        remaining = deadline - time.monotonic()
        delay = max(.001, delay_ms / 1000)
        if isinstance(reason, bytes):
            reason = reason.decode('ascii')
        if remaining <= 0 or delay > remaining:
            raise GoogleAdmissionError(delay, reason)
        return min(delay, .05, remaining) if reason == 'concurrency' else min(delay, remaining)

    async def acquire_async(self, *, project=None, operation='download', background=False):
        labels = _labels(operation, background)
        if not self._pending.acquire(blocking=False):
            gee_admission_rejected_total.labels(**labels, reason='local_queue').inc()
            raise GoogleAdmissionError(1, 'local_queue')
        started = time.monotonic()
        deadline = started + self._wait_seconds(background)
        keys, token = self._keys(project), uuid.uuid4().hex
        try:
            while True:
                result = await _finish_thread(self._attempt, keys, token, labels, background)
                if isinstance(result, GoogleLease):
                    return result
                await asyncio.sleep(self._delay(result, deadline))
        except GoogleAdmissionError as exc:
            gee_admission_rejected_total.labels(**labels, reason=exc.reason).inc()
            raise
        finally:
            self._pending.release()
            gee_admission_wait_seconds.labels(**labels).observe(time.monotonic() - started)


class GoogleLease:
    def __init__(self, controller, keys, token, labels):
        self.controller = controller
        self.keys = keys
        self.token = token
        self.labels = labels
        self.status = 'ok'
        self.retry_after = 0
        self._released = False
        self._release_lock = threading.Lock()
        self._stop = threading.Event()
        gee_external_active.labels(**labels).inc()
        self._heartbeat = threading.Thread(target=self._renew, daemon=True, name='gee-admission-lease')
        self._heartbeat.start()

    def _renew(self):
        interval = self.controller.limits.lease_seconds / 3
        while not self._stop.wait(interval):
            try:
                renewed = self.controller.redis.eval(
                    _RENEW, len(self.keys), *self.keys, self.token,
                    math.ceil(self.controller.limits.lease_seconds * 1000),
                )
                if not renewed:
                    logger.error('Concessão de chamada ao Earth Engine expirada durante execução')
                    return
            except redis.RedisError:
                logger.warning('Renovação de concessão ao Earth Engine indisponível')

    def response(self, status: int, retry_after=None):
        self.status = '429' if status == 429 else f'{status // 100}xx'
        if status == 429:
            delay = _retry_after(retry_after, self.controller.limits.cooldown_seconds)
            self.retry_after = delay
            try:
                self.controller.redis.eval(_PAUSE, 1, self.keys[-1], math.ceil(delay * 1000))
            except redis.RedisError as exc:
                raise GoogleAdmissionError(delay, 'coordinator_unavailable') from exc

    async def aresponse(self, status: int, retry_after=None):
        await _finish_thread(self.response, status, retry_after)

    def release(self):
        with self._release_lock:
            if self._released:
                return
            self._released = True
            self._stop.set()
            try:
                self.controller.redis.eval(_RELEASE, len(self.keys), *self.keys, self.token)
            except redis.RedisError:
                logger.warning('Liberação de concessão ao Earth Engine pendente de expiração')
            finally:
                gee_external_active.labels(**self.labels).dec()
                gee_external_attempts_total.labels(**self.labels, status=self.status).inc()


def get_admission_controller():
    return _controller_for_process(os.getpid())


@lru_cache(maxsize=1)
def _controller_for_process(pid):
    limits = AdmissionLimits(**{
        field: settings.get('GEE_ADMISSION_' + field.upper(), default)
        for field, default in vars(AdmissionLimits()).items()
    })
    client = redis.Redis.from_url(
        REDIS_URL, decode_responses=True, socket_connect_timeout=.25,
        socket_timeout=.25, retry_on_timeout=False,
    )
    return RedisAdmission(client, limits)


async def _finish_thread(function, *args, **kwargs):
    # Cancelar a espera não interrompe a thread nem desfaz uma concessão já adquirida no Redis.
    task = asyncio.get_running_loop().run_in_executor(
        _redis_executor_for_process(os.getpid()), partial(function, *args, **kwargs),
    )
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        if isinstance(result, GoogleLease):
            await _finish_thread(result.release)
        raise asyncio.CancelledError
    return result


@contextmanager
def google_request(*, project=None, operation='download', background=False):
    lease = get_admission_controller().acquire(
        project=project, operation=operation, background=background,
    )
    try:
        yield lease
    except BaseException:
        if lease.status in {'ok', '2xx'}:
            lease.status = 'error'
        raise
    finally:
        lease.release()


@asynccontextmanager
async def async_google_request(*, project=None, operation='download', background=False):
    lease = await get_admission_controller().acquire_async(
        project=project, operation=operation, background=background,
    )
    try:
        yield lease
    except BaseException as exc:
        if lease.status in {'ok', '2xx'}:
            lease.status = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'error'
        raise
    finally:
        await _finish_thread(lease.release)


def call_gee(function, *args, project=None, operation='map', background=False, **kwargs):
    if _sdk_transport_installed:
        token = _sdk_operation.set((operation, background))
        try:
            return function(*args, **kwargs)
        finally:
            _sdk_operation.reset(token)
    with google_request(project=project, operation=operation, background=background):
        return function(*args, **kwargs)


_startup_deadline = ContextVar('gee_startup_deadline', default=None)


def startup_remaining_seconds():
    deadline = _startup_deadline.get()
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise GoogleAdmissionError(1, 'startup_timeout')
    return remaining


@contextmanager
def gee_startup():
    timeout = float(settings.get('GEE_STARTUP_TIMEOUT_SECONDS', 120))
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('GEE_STARTUP_TIMEOUT_SECONDS deve ser positivo e finito')
    deadline_token = _startup_deadline.set(time.monotonic() + timeout)
    operation_token = _sdk_operation.set(('sdk', True))
    try:
        yield
    finally:
        _sdk_operation.reset(operation_token)
        _startup_deadline.reset(deadline_token)


_sdk_transport_installed = False
_sdk_operation = ContextVar('gee_operation', default=('sdk', False))
_EE_HOSTS = {'earthengine.googleapis.com', 'earthengine-highvolume.googleapis.com',
             'earthengine.google.com'}


@lru_cache(maxsize=1)
def _redis_executor_for_process(pid):
    return ThreadPoolExecutor(max_workers=8, thread_name_prefix='gee-admission')


def project_from_url(url):
    parsed = urlsplit(url)
    if parsed.hostname not in _EE_HOSTS:
        return None
    parts = parsed.path.split('/')
    for index, part in enumerate(parts[:-1]):
        if part == 'projects' and parts[index + 1]:
            return parts[index + 1]
    return None


class GoogleAdmissionTransport(httplib2.Http):
    def __init__(self):
        timeout = float(settings.get('GEE_HTTP_TIMEOUT_SECONDS', 60))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('GEE_HTTP_TIMEOUT_SECONDS deve ser positivo e finito')
        super().__init__(timeout=timeout)

    def request(self, uri, method='GET', body=None, headers=None,
                redirections=httplib2.DEFAULT_MAX_REDIRECTS, connection_type=None, **kwargs):
        if urlsplit(uri).hostname not in _EE_HOSTS:
            return self._request_once(uri, method, body, headers, **kwargs)
        project = next((value for key, value in (headers or {}).items()
                        if key.lower() == 'x-goog-user-project'), None) or project_from_url(uri)
        operation, background = _sdk_operation.get()
        with google_request(project=project, operation=operation, background=background) as lease:
            response, content = self._request_once(uri, method, body, headers, **kwargs)
            lease.response(response.status, response.get('retry-after'))
            return response, content

    def _request_once(self, uri, method, body, headers, **kwargs):
        # A admissão deve observar cada envio; retries e redirects do transporte não são permitidos.
        remaining = startup_remaining_seconds()
        timeout = min(self.timeout, remaining) if remaining is not None else self.timeout
        with requests.Session() as session:
            adapter = requests.adapters.HTTPAdapter(max_retries=0)
            session.mount('http://', adapter)
            session.mount('https://', adapter)
            try:
                with session.request(method, uri, data=body, headers=headers, timeout=timeout,
                                     allow_redirects=False, **kwargs) as response:
                    response_headers = dict(response.headers)
                    response_headers['status'] = response.status_code
                    return httplib2.Response(response_headers), response.content
            except requests.exceptions.Timeout as exc:
                raise TimeoutError('Prazo HTTP do Earth Engine excedido') from exc
            except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as exc:
                raise ConnectionError('Falha de conexão ao Earth Engine') from exc


def initialize_gee_transport(credentials):
    import ee

    global _sdk_transport_installed
    ee.Initialize(credentials, http_transport=GoogleAdmissionTransport())
    _sdk_transport_installed = True
