import fcntl
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException


@contextmanager
def admission(root, count=2):
    root = Path(root) / "admission"
    root.mkdir(parents=True, exist_ok=True)
    handle = None
    for slot in range(count):
        candidate = (root / f"{slot}.lock").open("a")
        try:
            fcntl.flock(candidate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            handle = candidate
            break
        except BlockingIOError:
            candidate.close()
    if handle is None:
        raise HTTPException(
            503,
            "BDC ocupado. Aguarde as leituras em andamento.",
            headers={"Retry-After": "2", "Cache-Control": "no-store"},
        )
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
