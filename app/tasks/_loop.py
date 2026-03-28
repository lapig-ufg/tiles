"""
Event loop persistente por worker process Celery (prefork).

Problema resolvido:
    Cada task criava um novo asyncio event loop, inicializava o
    HybridTileCache (singleton), e depois fechava o loop.  Na task
    seguinte, o singleton achava que já estava inicializado, mas suas
    conexões Redis/S3/MongoDB estavam bound ao loop fechado — causando
    ``RuntimeError: Event loop is closed`` em cascata.

Solução:
    Um único event loop por processo worker, criado na primeira chamada
    e reutilizado até o processo morrer (via max-tasks-per-child ou
    SIGTERM).  O ``worker_process_shutdown`` do Celery chama
    ``close_worker_loop()`` para limpeza.
"""
import asyncio
import threading

_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def get_worker_loop() -> asyncio.AbstractEventLoop:
    """Retorna (ou cria) o event loop persistente deste processo."""
    global _loop
    if _loop is not None and not _loop.is_closed():
        return _loop
    with _lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_loop)
    return _loop


def run_async(coro):
    """Executa uma coroutine no event loop persistente do worker."""
    loop = get_worker_loop()
    return loop.run_until_complete(coro)


def close_worker_loop():
    """Fecha o event loop do worker (chamado em worker_process_shutdown)."""
    global _loop
    if _loop is not None and not _loop.is_closed():
        _loop.close()
    _loop = None