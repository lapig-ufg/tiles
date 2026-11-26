import json

import pytest
import requests

from app.modules.bdc.transport import Transport

TOKEN = 'test-inpe-token-with-sufficient-entropy'


@pytest.fixture
def configured(tmp_path, monkeypatch):
    path = tmp_path / 'mounted-inpe-token'
    path.write_text(TOKEN + '\n')
    monkeypatch.delenv('BDC_TOKEN', raising=False)
    monkeypatch.setenv('BDC_INPE_TOKEN_FILE', str(path))
    remote = Transport(tmp_path / 'cache')
    yield remote, path
    remote.session.close()


def response(status=200, body=b'content', headers=None):
    value = requests.Response()
    value.status_code = status
    value._content = body
    value._content_consumed = True
    value.headers = headers or {}
    return value


@pytest.mark.parametrize(('method', 'byte_range'), [('GET', None), ('HEAD', None), ('GET', 'bytes=0-6')])
def test_inpe_secret_is_header_only_including_range(configured, monkeypatch, method, byte_range):
    remote, path = configured
    sent = []
    def network(method, url, **kwargs):
        sent.append((method, url, kwargs))
        return response(206 if byte_range else 200, headers={'Content-Range': 'bytes 0-6/7'} if byte_range else {})
    monkeypatch.setattr(remote.session, 'request', network)
    remote.request('https://data.inpe.br/bdc/example', method, byte_range)
    assert sent[0][2]['headers']['Authorization'] == 'Bearer ' + TOKEN
    assert sent[0][2]['headers'].get('Range') == byte_range
    assert sent[0][2]['allow_redirects'] is False
    assert TOKEN not in sent[0][1]
    assert TOKEN not in json.dumps(remote.events)
    for artifact in remote.root.iterdir():
        assert TOKEN.encode() not in artifact.read_bytes()


def test_other_allowed_host_never_reads_or_receives_inpe_secret(configured, monkeypatch):
    remote, path = configured
    path.unlink()
    sent = []
    def network(*args, **kwargs):
        sent.append(kwargs)
        return response()
    monkeypatch.setattr(remote.session, 'request', network)
    remote.request('https://tiles.lapig.iesa.ufg.br/example')
    assert 'Authorization' not in sent[0]['headers']


def test_process_token_takes_priority_without_reading_missing_file(configured, monkeypatch):
    remote, path = configured
    path.unlink()
    monkeypatch.setenv('BDC_TOKEN', 'process-token')
    sent = []
    def network(*args, **kwargs):
        sent.append(kwargs)
        return response()
    monkeypatch.setattr(remote.session, 'request', network)
    remote.request('https://data.inpe.br/example')
    assert sent[0]['headers']['Authorization'] == 'Bearer process-token'


@pytest.mark.parametrize('failure', ['missing', 'empty', 'invalid'])
def test_invalid_configured_secret_fails_before_sending_or_marking_attempt(configured, monkeypatch, failure):
    remote, path = configured
    if failure == 'missing':
        path.unlink()
    else:
        path.write_text('' if failure == 'empty' else TOKEN + '\nInjected: value')
    monkeypatch.setattr(remote.session, 'request', lambda *args, **kwargs: pytest.fail('invalid secret sent'))
    with pytest.raises(RuntimeError) as error:
        remote.request('https://data.inpe.br/example')
    assert TOKEN not in str(error.value)
    assert str(path) not in str(error.value)
    assert not list(remote.root.glob('*.attempted'))


def test_cached_response_is_available_when_secret_file_disappears(configured, monkeypatch):
    remote, path = configured
    monkeypatch.setattr(remote.session, 'request', lambda *args, **kwargs: response())
    remote.request('https://data.inpe.br/example')
    path.unlink()
    monkeypatch.setattr(remote.session, 'request', lambda *args, **kwargs: pytest.fail('cache hit sent request'))
    assert remote.request('https://data.inpe.br/example')[1] == b'content'


def test_absent_credentials_preserve_anonymous_public_access(tmp_path, monkeypatch):
    monkeypatch.delenv('BDC_TOKEN', raising=False)
    monkeypatch.delenv('BDC_INPE_TOKEN_FILE', raising=False)
    remote = Transport(tmp_path)
    sent = []
    def network(*args, **kwargs):
        sent.append(kwargs)
        return response()
    monkeypatch.setattr(remote.session, 'request', network)
    try:
        remote.request('https://data.inpe.br/example')
        assert 'Authorization' not in sent[0]['headers']
    finally:
        remote.session.close()


def test_reflected_auth_headers_are_not_persisted(configured, monkeypatch):
    remote, path = configured
    monkeypatch.setattr(remote.session, 'request', lambda *args, **kwargs: response(headers={'Authorization': 'Bearer ' + TOKEN, 'Set-Cookie': TOKEN, 'Content-Type': 'image/tiff'}))
    meta, _ = remote.request('https://data.inpe.br/example')
    assert meta['headers'] == {'Content-Type': 'image/tiff'}
    assert TOKEN not in json.dumps(remote.events)
    assert all(TOKEN.encode() not in file.read_bytes() for file in remote.root.iterdir())


@pytest.mark.parametrize('status', [200, 401])
def test_reflected_token_body_is_never_cached_or_reported(configured, monkeypatch, status):
    remote, path = configured
    monkeypatch.setattr(remote.session, 'request', lambda *args, **kwargs: response(status, TOKEN.encode()))
    with pytest.raises(RuntimeError) as error:
        remote.request('https://data.inpe.br/example')
    assert TOKEN not in str(error.value)
    assert TOKEN not in json.dumps(remote.events)
    assert all(TOKEN.encode() not in file.read_bytes() for file in remote.root.iterdir())


def test_transport_does_not_reuse_authorization_for_another_host(configured, monkeypatch):
    remote, path = configured
    sent = []
    def network(*args, **kwargs):
        sent.append(kwargs['headers'])
        return response()
    monkeypatch.setattr(remote.session, 'request', network)
    remote.request('https://data.inpe.br/first')
    remote.request('https://tiles.lapig.iesa.ufg.br/second')
    assert sent == [{'Authorization': 'Bearer ' + TOKEN}, {}]


def test_authenticated_redirect_is_rejected_without_forwarding_token(configured, monkeypatch):
    remote, path = configured
    sent = []
    def network(*args, **kwargs):
        sent.append(kwargs)
        return response(302, headers={'Location': 'https://evil.example/'})
    monkeypatch.setattr(remote.session, 'request', network)
    with pytest.raises(RuntimeError, match='Redirecionamento'):
        remote.request('https://data.inpe.br/example')
    assert len(sent) == 1 and sent[0]['allow_redirects'] is False
