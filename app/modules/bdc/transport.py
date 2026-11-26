import fcntl
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import requests


class ServicePaused(RuntimeError):
    pass


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
    def __init__(self, root, metadata_ttl=None):
        self.metadata_ttl = metadata_ttl
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
        with file_lock(self.root / "transport.lock"):
            meta_path = prefix.with_suffix(".json")
            body_path = prefix.with_suffix(".body")
            paused = self.root / (urlsplit(url).hostname + ".paused")
            refresh = False
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
                if not refresh:
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
            if paused.exists():
                raise ServicePaused(f"Serviço pausado: {paused.read_text()}")
            attempt_path = prefix.with_suffix(".attempted")
            if refresh:
                attempt_path.unlink(missing_ok=True)
            if attempt_path.exists():
                raise RuntimeError(
                    "Tentativa anterior sem resposta íntegra; repetição automática recusada"
                )
            atomic_write(
                attempt_path,
                json.dumps(
                    {"url": url, "method": method, "range": byte_range}
                ).encode(),
            )
            started = time.monotonic()
            response = self.session.request(
                method,
                url,
                headers={"Range": byte_range} if byte_range else {},
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
                    "headers": dict(response.headers),
                    "fetched_at": time.time(),
                }
                if response.status_code in (429, 503):
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
