import atexit
import os
from contextlib import contextmanager
from threading import Lock


class ReadPool:
    def __init__(self, capacity=8):
        self.capacity = capacity
        self._reset()
        os.register_at_fork(after_in_child=self._reset)
        atexit.register(self.close)

    def _reset(self):
        # Conexões herdadas não podem ser compartilhadas entre processos Celery.
        self.lock = Lock()
        self.idle = []
        self.factory = None

    def close(self):
        with self.lock:
            idle, self.idle = self.idle, []
        for context, client in idle:
            context.__exit__(None, None, None)

    @contextmanager
    def borrow(self, factory):
        with self.lock:
            if self.factory is not factory:
                for context, client in self.idle:
                    context.__exit__(None, None, None)
                self.idle = []
                self.factory = factory
            if self.idle:
                context, client = self.idle.pop()
            else:
                context = factory()
                client = context.__enter__()
        try:
            yield client
        except BaseException as error:
            context.__exit__(type(error), error, error.__traceback__)
            raise
        else:
            with self.lock:
                keep = self.factory is factory and len(self.idle) < self.capacity
                if keep:
                    self.idle.append((context, client))
            if not keep:
                context.__exit__(None, None, None)


read_pool = ReadPool()
