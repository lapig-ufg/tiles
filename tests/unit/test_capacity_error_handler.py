import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cache.errors import TileLockUnavailable
from app.core.gee_admission import GoogleAdmissionError


@pytest.mark.parametrize('error, reason, retry_after', [
    (GoogleAdmissionError(2.2), 'gee_busy', '3'),
    (GoogleAdmissionError(.1), 'gee_busy', '1'),
    (TileLockUnavailable('coordenador interno'), 'cache_busy', '1'),
])
def test_capacidade_indisponivel_tem_resposta_temporaria_sem_cache(error, reason, retry_after):
    from app.core.errors import capacity_exception_handler
    app = FastAPI()
    app.add_exception_handler(type(error), capacity_exception_handler)

    @app.get('/resource')
    async def resource():
        raise error

    with TestClient(app) as client:
        response = client.get('/resource')
    assert response.status_code == 503
    assert response.headers['Retry-After'] == retry_after
    assert response.headers['X-Error-Reason'] == reason
    assert 'no-store' in response.headers['Cache-Control']
    assert response.json() == {'detail': reason}
