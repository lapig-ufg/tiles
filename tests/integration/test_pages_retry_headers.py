import ast
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.testclient import TestClient


def test_pages_can_read_retry_after_from_capacity_response():
    from app.utils.cors import allow_origins, origin_regex

    app = FastAPI()
    tree = ast.parse(Path('main.py').read_text())
    middleware = next(node for node in tree.body if isinstance(node, ast.Expr)
                      and isinstance(node.value, ast.Call) and node.value.args
                      and isinstance(node.value.args[0], ast.Name)
                      and node.value.args[0].id == 'CORSMiddleware')
    exec(compile(ast.Module(body=[middleware], type_ignores=[]), 'main.py', 'exec'), {
        'app': app, 'CORSMiddleware': CORSMiddleware,
        'allow_origins': allow_origins, 'origin_regex': origin_regex,
    })

    @app.get('/tile')
    def tile():
        return Response(status_code=503, headers={'Retry-After': '5', 'X-Error-Reason': 'gee_busy'})

    response = TestClient(app).get('/tile', headers={'Origin': 'https://lapig-ufg.github.io'})
    assert response.headers['access-control-allow-origin'] == 'https://lapig-ufg.github.io'
    exposed = {header.strip().lower() for header in response.headers['access-control-expose-headers'].split(',')}
    assert {'retry-after', 'x-error-reason'} <= exposed
    assert response.headers['retry-after'] == '5'
