import fcntl
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import requests


class ServicePaused(RuntimeError):
    pass


class InpeCredentialError(RuntimeError):
    pass


def inpe_token():
    token = os.environ.get('BDC_TOKEN')
    if token is None:
        path = os.environ.get('BDC_INPE_TOKEN_FILE')
        if path is None:
            return None
        try:
            with Path(path).open() as secret:
                token = secret.read(16385).strip()
        except (OSError, UnicodeError):
            raise InpeCredentialError('Credencial INPE configurada não está disponível') from None
    if not token or len(token) > 16384 or not re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', token):
        raise InpeCredentialError('Credencial INPE configurada é inválida')
    return token


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        path.name + f".{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def validate_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"data.inpe.br", "tiles.lapig.iesa.ufg.br"}
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("URL remota não permitida")
    return url


class Transport:
    def __init__(self, root, metadata_ttl=None, origin_admission=None, origin_resumed=None, retry_token=None):
        self.origin_admission = origin_admission or nullcontext
        self.origin_resumed = origin_resumed
        self.metadata_ttl = metadata_ttl
        self.retry_token = retry_token
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.events = []

    def request(self, url, method="GET", byte_range=None):
        validate_url(url)
        if method not in {"GET", "HEAD"}:
            raise ValueError("Método não permitido")
        if byte_range and not re.fullmatch(r"bytes=\d+-\d+", byte_range):
            raise ValueError("Intervalo Range inválido")
        key = digest([method, url, byte_range])
        prefix = self.root / key
        with file_lock(prefix.with_suffix(".lock")):
            meta_path = prefix.with_suffix(".json")
            body_path = prefix.with_suffix(".body")
            paused = self.root / (urlsplit(url).hostname + ".paused")
            retry_path = prefix.with_suffix('.retry-' + digest(self.retry_token)) if self.retry_token else None
            retry_allowed = retry_path is not None and not retry_path.exists()
            refresh = False
            refresh_generation = None
            refresh_path = prefix.with_suffix('.refresh-attempted')
            if meta_path.exists() and body_path.exists():
                meta = json.loads(meta_path.read_text())
                refresh = (
                    self.metadata_ttl is not None
                    and url.startswith("https://data.inpe.br/bdc/stac/")
                    and method == "GET"
                    and meta["status"] < 400
                    and time.time() - meta.get("fetched_at", 0) > self.metadata_ttl
                    and not paused.exists()
                )
                if refresh:
                    refresh_generation = digest(meta)
                if not refresh and not (meta['status'] >= 400 and retry_allowed):
                    self.events.append(
                        {
                            "cache": True,
                            "method": method,
                            "range": byte_range,
                            "url": url,
                        }
                    )
                    if meta["status"] >= 400:
                        raise RuntimeError(
                            f"Resposta persistida HTTP {meta['status']}: {url}"
                        )
                    return meta, body_path.read_bytes()
            with file_lock(paused.with_suffix(".pause.lock")):
                if paused.exists() and self.origin_resumed:
                    previous = json.loads(paused.read_text())
                    if previous.get('status') in (429, 503) and self.origin_resumed(previous.get('fetched_at', 0)):
                        failed_key = digest([previous['method'], previous['url'], previous.get('range')])
                        (self.root / (failed_key + '.attempted')).unlink(missing_ok=True)
                        paused.unlink()
                if paused.exists():
                    raise ServicePaused(f"Serviço pausado: {paused.read_text()}")
            attempt_path = prefix.with_suffix(".attempted")
            refresh_allowed = refresh and (not refresh_path.exists() or refresh_path.read_text() != refresh_generation)
            if attempt_path.exists() and not retry_allowed and not refresh_allowed:
                raise RuntimeError(
                    "Tentativa anterior sem resposta íntegra; repetição automática recusada"
                )
            with self.origin_admission() as check:
                if check:
                    check()
                token = inpe_token() if urlsplit(url).hostname == 'data.inpe.br' else None
                headers = {"Range": byte_range} if byte_range else {}
                if token:
                    headers['Authorization'] = 'Bearer ' + token
                atomic_write(
                    attempt_path,
                    json.dumps({"url": url, "method": method, "range": byte_range}).encode(),
                )
                if retry_path is not None:
                    atomic_write(retry_path, b'1')
                if refresh_generation is not None:
                    atomic_write(refresh_path, refresh_generation.encode())
                started = time.monotonic()
                response = self.session.request(
                    method,
                    url,
                    headers=headers,
                    timeout=(10, 60),
                    allow_redirects=False,
                    stream=True,
                )
                with response:
                    meta = {
                        "url": url,
                        "method": method,
                        "range": byte_range,
                        "status": response.status_code,
                        "headers": {name: value for name, value in response.headers.items()
                                    if name.lower() not in {'authorization', 'proxy-authorization', 'set-cookie', 'cookie'}
                                    and not (token and token in str(value))},
                        "fetched_at": time.time(),
                    }
                    if response.status_code in (429, 503):
                        with file_lock(paused.with_suffix(".pause.lock")):
                            atomic_write(paused, json.dumps(meta).encode())
                        raise ServicePaused(f"HTTP {response.status_code}: {url}")
                    if 300 <= response.status_code < 400:
                        raise RuntimeError("Redirecionamento remoto recusado")
                    if (
                        byte_range
                        and response.status_code < 400
                        and response.status_code != 206
                    ):
                        raise RuntimeError(
                            "Servidor ignorou Range; download integral recusado"
                        )
                    body = response.content
                    if token and token.encode() in body:
                        raise InpeCredentialError('Resposta INPE contém credencial refletida')
                    if check:
                        check()
                    if byte_range and response.status_code == 206:
                        requested = re.fullmatch(r"bytes=(\d+)-(\d+)", byte_range)
                        returned = re.fullmatch(
                            r"bytes (\d+)-(\d+)/(\d+)",
                            response.headers.get("Content-Range", ""),
                        )
                        if (
                            not returned
                            or int(returned[1]) != int(requested[1])
                            or int(returned[2]) > int(requested[2])
                            or len(body) != int(returned[2]) - int(returned[1]) + 1
                        ):
                            raise RuntimeError("Resposta Range inconsistente")
                    meta.update(seconds=time.monotonic() - started, bytes=len(body))
                    atomic_write(body_path, body)
                    atomic_write(meta_path, json.dumps(meta).encode())
                    self.events.append(dict(meta, cache=False))
                    if response.status_code >= 400:
                        raise RuntimeError(f"HTTP {response.status_code}: {url}")
                    return meta, body

    def json(self, url):
        return json.loads(self.request(url)[1])


class RangeBridge:
    def __init__(self, transport):
        self.transport = transport
        self.urls = {}
        self.errors = []
        self.failures = []

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self.forward("HEAD")

            def do_GET(self):
                self.forward("GET")

            def forward(self, method):
                url = owner.urls.get(self.path)
                if url is None:
                    self.send_error(404)
                    return
                byte_range = self.headers.get("Range")
                if method == "GET" and not byte_range:
                    self.send_error(400, "Range required")
                    return
                try:
                    meta, body = owner.transport.request(url, method, byte_range)
                    self.send_response(meta["status"])
                    for name, value in meta["headers"].items():
                        if name.lower() in {
                            "content-length",
                            "content-range",
                            "content-type",
                            "accept-ranges",
                        }:
                            self.send_header(name, value)
                    self.end_headers()
                    if method == "GET":
                        self.wfile.write(body)
                except Exception as exc:
                    owner.errors.append(str(exc))
                    owner.failures.append(exc)
                    self.send_error(502)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()
        return self

    def url(self, remote):
        validate_url(remote)
        path = "/" + digest(remote) + ".tif"
        self.urls[path] = remote
        return f"http://127.0.0.1:{self.server.server_port}{path}"

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        if args[0] is not None and self.failures:
            raise self.failures[-1]
