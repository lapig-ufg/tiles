from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
import requests

from app.modules.bdc.transport import Transport


def response(body):
    result = requests.Response()
    result.status_code = 200
    result._content = body
    result._content_consumed = True
    return result


def fetch(root, url):
    remote = Transport(root)
    try:
        return remote.request(url)[1]
    finally:
        remote.session.close()


def test_distinct_resources_download_concurrently_with_shared_cache(tmp_path, monkeypatch):
    network = Barrier(2)

    def request(self, method, url, **kwargs):
        network.wait(timeout=2)
        return response(url.encode())

    monkeypatch.setattr(requests.Session, 'request', request)
    urls = ['https://data.inpe.br/first.tif', 'https://data.inpe.br/second.tif']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda url: fetch(tmp_path, url), urls))
    assert results == [url.encode() for url in urls]


@pytest.mark.parametrize('failure', [False, True])
def test_same_resource_downloads_once_and_preserves_failed_attempt(tmp_path, monkeypatch, failure):
    start = Barrier(2)
    entered = Event()
    release = Event()
    calls = []

    def request(self, method, url, **kwargs):
        calls.append(url)
        entered.set()
        assert release.wait(timeout=2)
        if failure:
            raise requests.exceptions.Timeout('indisponível')
        return response(b'complete content')

    def run():
        start.wait(timeout=2)
        try:
            return fetch(tmp_path, 'https://data.inpe.br/same.tif')
        except Exception as error:
            return type(error)

    monkeypatch.setattr(requests.Session, 'request', request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        assert entered.wait(timeout=2)
        release.set()
        results = [future.result() for future in futures]
    assert len(calls) == 1
    if failure:
        assert set(results) == {requests.exceptions.Timeout, RuntimeError}
        with pytest.raises(RuntimeError):
            fetch(tmp_path, 'https://data.inpe.br/same.tif')
    else:
        assert results == [b'complete content', b'complete content']
        assert fetch(tmp_path, 'https://data.inpe.br/same.tif') == b'complete content'
    assert len(calls) == 1
