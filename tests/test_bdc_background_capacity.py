from contextlib import ExitStack, contextmanager

import mongomock
import pytest

from app.modules.bdc import selection_jobs
from app.modules.bdc.service import BdcService


@pytest.mark.parametrize('slots', [2, 4, 6, 8])
def test_background_preserves_one_global_slot_for_interactive_requests(monkeypatch, slots):
    monkeypatch.setattr(selection_jobs, 'ORIGIN_SLOTS', slots)
    database = mongomock.MongoClient().tiles
    with ExitStack() as stack:
        for _ in range(slots - max(1, slots // 4)):
            stack.enter_context(selection_jobs.SelectionJobStore(database).origin_admission(background=True))
        with pytest.raises(selection_jobs.OriginBusy):
            with selection_jobs.SelectionJobStore(database).origin_admission(background=True):
                pass
        for _ in range(max(1, slots // 4)):
            stack.enter_context(selection_jobs.SelectionJobStore(database).origin_admission())
        with pytest.raises(selection_jobs.OriginBusy):
            with selection_jobs.SelectionJobStore(database).origin_admission():
                pass
    with selection_jobs.SelectionJobStore(database).origin_admission(background=True):
        pass


@pytest.mark.parametrize('background', [False, True])
def test_service_propagates_workload_category_to_origin_admission(tmp_path, monkeypatch, background):
    calls = []

    class Store:
        @contextmanager
        def origin_admission(self, background=False):
            calls.append(background)
            yield lambda: None

    @contextmanager
    def factory():
        yield Store()

    monkeypatch.setattr(selection_jobs, 'selection_store', factory)
    service = BdcService(tmp_path, background=background)
    with service.remote() as remote:
        with remote.origin_admission():
            pass
    assert calls == [background]
