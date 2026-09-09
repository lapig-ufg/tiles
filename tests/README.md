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

Fixtures que instalam stubs em `app.*` chamam `reset_app_imports()` no setup
para forçar re-import (ver `test_tile_handlers_propagate_status.py` como
modelo). A fixture `autouse` `_isolamento_de_modulos_app` do `conftest.py`
fotografa as entradas `app.*` de `sys.modules` antes de cada teste e as
restaura depois, de modo que stubs e reimportações de um teste não vazem
para os seguintes (um `patch("app.x.y")` posterior encontraria o stub, e uma
importação tardia ligaria a função a uma classe de exceção reimportada).
