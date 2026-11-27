from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.modules.bdc.read_pool import ReadPool


def test_reader_reuses_connection_and_discards_after_error():
    created, closed = [], []
    @contextmanager
    def factory():
        client = object()
        created.append(client)
        try:
            yield client
        finally:
            closed.append(client)
    pool = ReadPool(2)
    with pool.borrow(factory) as first:
        pass
    with pool.borrow(factory) as second:
        assert first is second
    with pytest.raises(ValueError):
        with pool.borrow(factory):
            raise ValueError('failure')
    assert closed == [first]
    with pool.borrow(factory) as third:
        assert third is not first
    pool.close()
    assert len(created) == len(closed) == 2


def test_concurrent_readers_are_exclusive_and_idle_pool_is_bounded():
    barrier = Barrier(4)
    created, closed = [], []
    @contextmanager
    def factory():
        client = object()
        created.append(client)
        try:
            yield client
        finally:
            closed.append(client)
    pool = ReadPool(2)
    def read(_):
        with pool.borrow(factory) as client:
            barrier.wait(timeout=5)
            return client
    with ThreadPoolExecutor(max_workers=4) as executor:
        clients = list(executor.map(read, range(4)))
    assert len({id(client) for client in clients}) == 4
    assert len(pool.idle) == 2
    assert len(closed) == 2
    pool.close()
    assert len(closed) == 4


def test_fork_discards_inherited_idle_contexts():
    import multiprocessing
    context = multiprocessing.get_context('fork')
    @contextmanager
    def factory():
        yield object()
    pool = ReadPool()
    with pool.borrow(factory):
        pass
    receiver, sender = context.Pipe(duplex=False)
    def child():
        sender.send(len(pool.idle))
        sender.close()
    process = context.Process(target=child)
    process.start()
    sender.close()
    try:
        assert receiver.poll(5)
        assert receiver.recv() == 0
        process.join(5)
        assert process.exitcode == 0
        assert len(pool.idle) == 1
    finally:
        if process.is_alive():
            process.terminate()
            process.join()
        receiver.close()
        pool.close()
