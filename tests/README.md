# Testes

## Setup

```bash
# Criar venv (se não existir)
python -m venv .venv

# Instalar deps de runtime + dev
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

## Rodar

```bash
# Tudo
.venv/bin/pytest tests/ -v

# Apenas unitários (rápidos, sem Redis/S3)
.venv/bin/pytest tests/unit/ -v

# Apenas integração (ainda usa stubs in-memory, não Redis real)
.venv/bin/pytest tests/integration/ -v

# Um teste específico
.venv/bin/pytest tests/unit/test_tile_error_response.py::TestBasicContract -v
```

## Organização

- `tests/conftest.py` — stubs globais (`ee`, `ee.data`) suficientes para que
  `app.*` carregue sem `earthengine-api` real.
- `tests/unit/` — testes de funções puras (`tile_error_response`,
  `validate_landsat_request`, `is_poisoned`, `_empty_image_with_bands`,
  `_error_png_bytes` com LRU, `_retry_with_mosaic_if_band_missing`).
- `tests/integration/` — FastAPI `TestClient` exercitando handlers reais
  com cache/mongo/S3/slowapi/GEE stubados em memória.

## Pollution de `sys.modules`

Fixtures de integração fazem `sys.modules.pop("app.*", None)` no setup
para forçar re-import. Se você adicionar um teste novo que importa `app.*`,
use a mesma técnica para evitar ver o módulo de outro teste com stubs
diferentes — ver `test_tile_handlers_propagate_status.py` como modelo.
