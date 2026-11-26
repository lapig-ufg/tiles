import math
import threading
from contextlib import contextmanager

import redis

from app.cache.errors import TileLockUnavailable
from app.core.config import logger


class SyncTileLease:
    def __init__(self, lock, key):
        self.lock = lock
        self.key = key
        self.lost = threading.Event()

    def ensure_owned(self):
        try:
            if self.lost.is_set() or not self.lock.owned():
                raise TileLockUnavailable('Concessão de geração perdida')
        except redis.RedisError as exc:
            raise TileLockUnavailable('Coordenador de cache indisponível') from exc


@contextmanager
def sync_tile_lock(redis_client, key, *, wait_timeout=30, lease_seconds=60):
    if any(not math.isfinite(value) or value <= 0 for value in (wait_timeout, lease_seconds)):
        raise ValueError('Prazos do lock devem ser positivos e finitos')
    lock = redis_client.lock(
        'lock:' + key, timeout=lease_seconds, sleep=min(.05, wait_timeout),
        blocking_timeout=wait_timeout, thread_local=False,
    )
    try:
        acquired = lock.acquire()
    except redis.RedisError as exc:
        raise TileLockUnavailable('Coordenador de cache indisponível') from exc
    if not acquired:
        raise TileLockUnavailable('Prazo de espera pelo lock excedido')
    lease = SyncTileLease(lock, key)
    stop = threading.Event()

    def renew():
        while not stop.wait(lease_seconds / 3):
            try:
                lock.reacquire()
            except redis.RedisError:
                lease.lost.set()
                return

    heartbeat = threading.Thread(target=renew, name='cache-sync-lock', daemon=True)
    heartbeat.start()
    try:
        lease.ensure_owned()
        yield lease
        lease.ensure_owned()
    finally:
        stop.set()
        heartbeat.join(timeout=1)
        try:
            lock.release()
        except redis.RedisError:
            logger.warning('Liberação do lock de cache pendente de expiração')
