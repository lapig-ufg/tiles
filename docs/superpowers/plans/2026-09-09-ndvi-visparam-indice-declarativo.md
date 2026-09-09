# Plano de implementação: visparam NDVI com índice declarativo

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Disponibilizar NDVI como visparam para Sentinel-2 (`tvi-ndvi`) e Landsat (`landsat-tvi-ndvi`) no serviço de tiles, com legenda no modal de comparação de imagens do TVI.

**Architecture:** O modelo `VisParam` ganha os campos opcionais `palette` e `index`; um auxiliar único (`resolve_visualization`) calcula a banda derivada e monta os parâmetros finais para o Earth Engine, chamado pelos quatro construtores de camada imediatamente antes do registro do mapa. As capabilities passam a publicar `legend` em `visparam_details`, e o modal do TVI desenha a barra de cores a partir desse objeto.

**Tech Stack:** Python 3.12, FastAPI, Pydantic 2.7, earthengine-api, Motor/MongoDB, Celery, pytest 9 com pytest-asyncio 1.3 (modo estrito); AngularJS 1.5.8 e node:test no TVI.

**Spec:** `docs/superpowers/specs/2026-09-09-ndvi-visparam-indice-declarativo-design.md` (repositório tiles)

## Global Constraints

- Dois repositórios: tiles em `/home/tharles/projects_lapig/tiles` (Tarefas 1 a 9 e 11) e TVI em `/home/tharles/projects_lapig/tvi` (Tarefa 10). Cada comando indica o diretório.
- Commits somente com autorização explícita do usuário. Sem autorização, deixar as alterações no working tree e informar. Mensagens no formato `tipo(escopo): descrição`, sem trailers de ferramenta, sem `Co-Authored-By`, sem emojis.
- Código sem marcadores de geração automática, sem rastros de mudança em comentários, sem `TODO`/`FIXME`. Comentários apenas quando essenciais.
- Idioma dos comentários e docstrings: o predominante em cada arquivo. `app/models/vis_params.py`, `app/utils/capabilities.py` e `scripts/migrate_vis_params.py` estão em inglês; `app/visualization/visParam.py`, `app/visualization/indices.py` (novo) e todos os testes em português.
- Nomes fixos: visparams `tvi-ndvi` e `landsat-tvi-ndvi`; rótulo `NDVI`; banda derivada `NDVI`; faixa `-0.2` a `0.9`; paleta `NDVI_PALETTE` com nove cores: `#a52a2a`, `#c4813e`, `#e6c26b`, `#fff2a8`, `#d9ef8b`, `#a6d96a`, `#66bd63`, `#1a9850`, `#006837`.
- Bandas do NDVI: Sentinel-2 `B8`/`B4` mapeadas para `NIR`/`RED`; Landsat `SR_B4`/`SR_B3` em `LT05` e `LE07`, `SR_B5`/`SR_B4` em `LC08` e `LC09`.
- `gamma` nunca é enviado ao Earth Engine quando há `index`.
- Testes do tiles: `.venv/bin/pytest tests/unit/ -q` a partir de `/home/tharles/projects_lapig/tiles`. Testes assíncronos exigem `@pytest.mark.asyncio`.
- Testes do TVI: `cd /home/tharles/projects_lapig/tvi/src/server && npm test`.
- Nenhuma dependência nova em nenhum dos repositórios.

---

## Mapa de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `app/models/vis_params.py` | Modelo Pydantic dos documentos; ganha `IndexConfig`, `palette`, `gamma` opcional e validadores |
| `app/visualization/indices.py` (novo) | `resolve_visualization(image, vis)`: aplica o índice e monta o dicionário final para o Earth Engine |
| `app/visualization/vis_params_db.py` | Conversão dos documentos MongoDB; passa a usar `exclude_none` |
| `app/visualization/visParam.py` | Dicionário fixo; ganha `NDVI_PALETTE`, `tvi-ndvi` e `landsat-tvi-ndvi` |
| `app/api/layers.py` | Três construtores chamam `resolve_visualization` antes de `getMapId` |
| `app/tasks/cache_operations.py` | Duas réplicas de pré-aquecimento chamam `resolve_visualization` |
| `app/utils/capabilities.py` | `build_legend` e publicação de `legend` em `visparam_details`; fallback fixo com NDVI |
| `scripts/migrate_vis_params.py` | `build_documents` e `upsert_missing`; rótulo explícito; inserção apenas do que falta |
| `docs/VIS_PARAMS_API.md`, `docs/CAPABILITIES_API.md`, `docs/LAYERS_API_GUIDE.md`, `docs/CHANGELOG.md` | Documentação |
| `tvi/src/client/controllers/mosaic-dialog.js` | `getLegend` e `getLegendGradient` |
| `tvi/src/client/views/mosaic-dialog.tpl.html` | Bloco de legenda sob cada rótulo de camada e estilos |
| `tvi/src/server/test/mosaicDialogLegend.test.js` | Teste do controlador e guarda estática do template |

---

### Task 1: Modelo `VisParam` com índice declarativo

**Files:**
- Modify: `app/models/vis_params.py:1-41`
- Test: `tests/unit/test_vis_params_model.py`

**Interfaces:**
- Consumes: nada.
- Produces: `IndexConfig(type: Literal["normalized_difference"], bands: List[str] (2 itens), name: str = "NDVI")`; `VisParam` com `gamma: Optional[...] = None`, `palette: Optional[List[str]] = None`, `index: Optional[IndexConfig] = None`. Após validação, `min` e `max` são sempre listas de `float`.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_vis_params_model.py`:

```python
"""Modelo VisParam com índice declarativo (campos palette e index)."""
import pytest
from pydantic import ValidationError

from app.models.vis_params import IndexConfig, VisParam

NDVI_INDEX = {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"}
PALETTE = ["#a52a2a", "#006837"]


def test_documento_atual_sem_indice_continua_valido():
    vp = VisParam(bands=["SWIR1", "REDEDGE4", "RED"], min="600, 700, 400",
                  max="4300, 5400, 2800", gamma="1.1")
    assert vp.min == [600.0, 700.0, 400.0]
    assert vp.gamma == 1.1
    assert vp.palette is None
    assert vp.index is None


def test_gamma_ausente_e_valido():
    vp = VisParam(bands=["B4", "B3", "B2"], min=[0, 0, 0], max=[3000, 3000, 3000])
    assert vp.gamma is None


def test_indice_com_paleta_e_faixa_unica():
    vp = VisParam(bands=["NIR", "RED"], min="-0.2", max="0.9", palette=PALETTE, index=NDVI_INDEX)
    assert vp.index.name == "NDVI"
    assert vp.index.bands == ["NIR", "RED"]
    assert vp.min == [-0.2]
    assert vp.max == [0.9]
    assert vp.palette == PALETTE


def test_rejeita_banda_do_indice_fora_de_bands():
    with pytest.raises(ValidationError, match="index bands"):
        VisParam(bands=["NIR"], min=[-0.2], max=[0.9], index=NDVI_INDEX)


def test_rejeita_paleta_sem_indice():
    with pytest.raises(ValidationError, match="palette requires index"):
        VisParam(bands=["B4", "B3", "B2"], min=[0, 0, 0], max=[1, 1, 1], palette=PALETTE)


def test_rejeita_faixa_multivalor_com_indice():
    with pytest.raises(ValidationError, match="single value"):
        VisParam(bands=["NIR", "RED"], min=[-0.2, 0], max=[0.9, 1], index=NDVI_INDEX)


def test_indice_exige_exatamente_duas_bandas():
    with pytest.raises(ValidationError):
        IndexConfig(type="normalized_difference", bands=["NIR"])


def test_tipo_de_indice_desconhecido_e_rejeitado():
    with pytest.raises(ValidationError):
        IndexConfig(type="evi", bands=["NIR", "RED"])
```

- [ ] **Step 2: Executar o teste para confirmar a falha**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_vis_params_model.py -q`
Expected: FAIL com `ImportError: cannot import name 'IndexConfig'`.

- [ ] **Step 3: Implementar o modelo**

Em `app/models/vis_params.py`, substituir o bloco de imports e a classe `VisParam` (linhas 4 a 41) por:

```python
from datetime import datetime
from typing import List, Optional, Dict, Any, Union, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class BandConfig(BaseModel):
    """Configuration for band selection and mapping"""
    original_bands: List[str] = Field(..., description="Original band names (e.g., B4, B8A)")
    mapped_bands: Optional[List[str]] = Field(None, description="Mapped band names (e.g., RED, SWIR1)")


class IndexConfig(BaseModel):
    """Single band derived from two input bands and rendered with a palette"""
    type: Literal["normalized_difference"] = Field(..., description="Index formula")
    bands: List[str] = Field(..., min_length=2, max_length=2,
                             description="Input bands in formula order (e.g., [NIR, RED])")
    name: str = Field("NDVI", description="Name of the derived band")


class VisParam(BaseModel):
    """Visualization parameters for a single configuration"""
    bands: List[str] = Field(..., description="Band names that must exist in the image")
    min: Union[str, List[float]] = Field(..., description="Minimum values for each band")
    max: Union[str, List[float]] = Field(..., description="Maximum values for each band")
    gamma: Optional[Union[str, float, List[float]]] = Field(None, description="Gamma correction value(s)")
    palette: Optional[List[str]] = Field(None, description="CSS colors for single-band index rendering")
    index: Optional[IndexConfig] = Field(None, description="Derived index rendered instead of the raw bands")

    @field_validator('min', 'max', mode='before')
    @classmethod
    def parse_string_values(cls, v):
        """Convert comma-separated strings to lists"""
        if isinstance(v, str):
            return [float(x.strip()) for x in v.split(',')]
        return v

    @field_validator('gamma', mode='before')
    @classmethod
    def parse_gamma(cls, v):
        """Ensure gamma is numeric"""
        if isinstance(v, str):
            return float(v)
        return v

    @model_validator(mode='after')
    def validate_index(self):
        if self.index is None:
            if self.palette is not None:
                raise ValueError("palette requires index")
            return self
        missing = [b for b in self.index.bands if b not in self.bands]
        if missing:
            raise ValueError(f"index bands {missing} must be listed in bands")
        for key in ("min", "max"):
            values = getattr(self, key)
            if isinstance(values, list) and len(values) != 1:
                raise ValueError(f"{key} must have a single value when index is set")
        return self
```

O restante do arquivo (`SatelliteVisParam`, `VisParamDocument`, mapeamentos) permanece igual.

- [ ] **Step 4: Executar os testes**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_vis_params_model.py -q`
Expected: `8 passed`.

- [ ] **Step 5: Confirmar que a suíte unitária continua verde**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/ -q`
Expected: todos aprovados (nenhuma falha nova).

- [ ] **Step 6: Commit (com autorização do usuário)**

```bash
cd /home/tharles/projects_lapig/tiles
git add app/models/vis_params.py tests/unit/test_vis_params_model.py
git commit -m "feat(tiles): modelo de visparam aceita índice declarativo e paleta"
```

---

### Task 2: Auxiliar `resolve_visualization`

**Files:**
- Create: `app/visualization/indices.py`
- Test: `tests/unit/test_indices.py`

**Interfaces:**
- Consumes: dicionários de visualização já convertidos (`bands`, `min`, `max`, opcionalmente `gamma`, `palette`, `index`).
- Produces: `resolve_visualization(image, vis: dict) -> tuple[Any, dict]`. Sem `index`: `(image, vis_limpo)` com apenas as chaves `bands`, `min`, `max`, `gamma`, `palette`, `opacity` não nulas. Com `index`: `(banda_derivada, {"bands": [name], "min", "max", "palette"?})`. `ValueError` para tipo de índice desconhecido.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_indices.py`:

```python
"""resolve_visualization: aplica o índice declarado no visparam antes do registro do mapa."""
from unittest.mock import patch

import pytest

from app.visualization import indices

NDVI_VIS = {
    "bands": ["NIR", "RED"],
    "min": "-0.2",
    "max": "0.9",
    "gamma": "1.0",
    "palette": ["#a52a2a", "#006837"],
    "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"},
}


def test_sem_indice_devolve_a_mesma_imagem_e_remove_chaves_nulas():
    image = object()
    out_image, vis = indices.resolve_visualization(
        image, {"bands": ["B4"], "min": "0", "max": "1", "gamma": None, "index": None, "palette": None}
    )
    assert out_image is image
    assert vis == {"bands": ["B4"], "min": "0", "max": "1"}


def test_sem_indice_preserva_gamma_informado():
    _, vis = indices.resolve_visualization(object(), {"bands": ["B4"], "min": "0", "max": "1", "gamma": "1.2"})
    assert vis["gamma"] == "1.2"


def test_com_indice_calcula_a_diferenca_normalizada_e_renomeia():
    image = object()
    with patch.object(indices, "ee") as ee_mock:
        derived = ee_mock.Image.return_value.normalizedDifference.return_value.rename.return_value
        out_image, vis = indices.resolve_visualization(image, NDVI_VIS)
    ee_mock.Image.assert_called_once_with(image)
    ee_mock.Image.return_value.normalizedDifference.assert_called_once_with(["NIR", "RED"])
    ee_mock.Image.return_value.normalizedDifference.return_value.rename.assert_called_once_with("NDVI")
    assert out_image is derived
    assert vis == {"bands": ["NDVI"], "min": "-0.2", "max": "0.9", "palette": ["#a52a2a", "#006837"]}


def test_com_indice_descarta_gamma():
    with patch.object(indices, "ee"):
        _, vis = indices.resolve_visualization(object(), NDVI_VIS)
    assert "gamma" not in vis


def test_indice_sem_paleta_nao_emite_chave_palette():
    with patch.object(indices, "ee"):
        _, vis = indices.resolve_visualization(object(), {**NDVI_VIS, "palette": None})
    assert "palette" not in vis


def test_nome_padrao_da_banda_derivada_e_ndvi():
    with patch.object(indices, "ee"):
        _, vis = indices.resolve_visualization(
            object(), {**NDVI_VIS, "index": {"type": "normalized_difference", "bands": ["NIR", "RED"]}}
        )
    assert vis["bands"] == ["NDVI"]


def test_tipo_desconhecido_levanta_value_error():
    with pytest.raises(ValueError, match="evi"):
        indices.resolve_visualization(object(), {**NDVI_VIS, "index": {"type": "evi", "bands": ["NIR", "RED"]}})
```

- [ ] **Step 2: Executar o teste para confirmar a falha**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_indices.py -q`
Expected: FAIL com `ImportError: cannot import name 'indices'`.

- [ ] **Step 3: Criar o módulo**

Criar `app/visualization/indices.py`:

```python
"""Índices espectrais declarados nos visparams (campo ``index``).

Um visparam com ``index`` descreve uma banda derivada de duas bandas de
entrada e renderizada com paleta. O cálculo acontece num único ponto, aqui,
e os construtores de camada apenas trocam a imagem e o dicionário de
visualização antes de registrar o mapa no Earth Engine.
"""
import ee

_EE_VIS_KEYS = ("bands", "min", "max", "gamma", "palette", "opacity")


def resolve_visualization(image, vis: dict) -> tuple:
    """Devolve ``(imagem, vis_ee)`` prontos para ``getMapId``.

    Sem ``index``: a imagem original e ``vis`` restrito às chaves que o
    Earth Engine conhece, sem valores nulos. Com ``index``: a banda derivada
    no lugar das bandas de entrada e ``vis_ee`` com ``bands``, ``min``,
    ``max`` e ``palette``; ``gamma`` é descartado porque o Earth Engine não o
    aceita junto com paleta.
    """
    index = vis.get("index")
    if not index:
        return image, {k: v for k, v in vis.items() if k in _EE_VIS_KEYS and v is not None}

    index_type = index.get("type")
    if index_type != "normalized_difference":
        raise ValueError(f"tipo de índice não suportado: {index_type}")

    name = index.get("name") or "NDVI"
    derived = ee.Image(image).normalizedDifference(list(index["bands"])).rename(name)
    vis_ee = {"bands": [name], "min": vis["min"], "max": vis["max"]}
    if vis.get("palette"):
        vis_ee["palette"] = list(vis["palette"])
    return derived, vis_ee
```

- [ ] **Step 4: Executar os testes**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_indices.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Commit (com autorização do usuário)**

```bash
cd /home/tharles/projects_lapig/tiles
git add app/visualization/indices.py tests/unit/test_indices.py
git commit -m "feat(tiles): auxiliar que aplica índice declarado ao visparam"
```

---

### Task 3: Conversão dos documentos sem chaves nulas

**Files:**
- Modify: `app/visualization/vis_params_db.py:118-135` (método `get_landsat_vis_params`) e `app/visualization/vis_params_db.py:153-190` (função `get_visparams_dict`)
- Test: `tests/unit/test_vis_params_dict.py`

**Interfaces:**
- Consumes: `VisParam` da Tarefa 1.
- Produces: `get_visparams_dict()` e `get_landsat_vis_params_async()` devolvem dicionários sem `gamma` quando ausente e com `index` (dict) e `palette` (lista) quando presentes.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_vis_params_dict.py`:

```python
"""Conversão dos documentos de visparam para o formato consumido pelos construtores."""
import pytest

from app.models.vis_params import VisParamDocument
from app.visualization import vis_params_db

PALETTE = ["#a52a2a", "#006837"]


def _doc_s2_ndvi():
    return VisParamDocument(
        _id="tvi-ndvi", name="tvi-ndvi", display_name="NDVI", category="sentinel2",
        band_config={"original_bands": ["B8", "B4"], "mapped_bands": ["NIR", "RED"]},
        vis_params={
            "bands": ["NIR", "RED"], "min": [-0.2], "max": [0.9], "palette": PALETTE,
            "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"},
        },
    )


def _doc_landsat_ndvi():
    return VisParamDocument(
        _id="landsat-tvi-ndvi", name="landsat-tvi-ndvi", display_name="NDVI", category="landsat",
        satellite_configs=[{
            "collection_id": "LANDSAT/LC08/C02/T1_L2",
            "vis_params": {
                "bands": ["SR_B5", "SR_B4"], "min": [-0.2], "max": [0.9], "palette": PALETTE,
                "index": {"type": "normalized_difference", "bands": ["SR_B5", "SR_B4"], "name": "NDVI"},
            },
        }],
    )


def _doc_s2_rgb():
    return VisParamDocument(
        _id="tvi-rgb", name="tvi-rgb", display_name="RGB", category="sentinel2",
        band_config={"original_bands": ["B4", "B3", "B2"]},
        vis_params={"bands": ["B4", "B3", "B2"], "min": "200, 300, 700", "max": "3000, 2500, 2300", "gamma": "1.35"},
    )


@pytest.fixture
def manager():
    m = vis_params_db.vis_params_manager
    saved_cache, saved_flag = dict(m._cache), m._initialized
    m._cache = {"tvi-ndvi": _doc_s2_ndvi(), "landsat-tvi-ndvi": _doc_landsat_ndvi(), "tvi-rgb": _doc_s2_rgb()}
    m._initialized = True
    yield m
    m._cache, m._initialized = saved_cache, saved_flag


@pytest.mark.asyncio
async def test_sentinel_propaga_index_e_palette_sem_gamma(manager):
    result = await vis_params_db.get_visparams_dict()
    vis = result["tvi-ndvi"]["visparam"]
    assert vis["index"] == {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"}
    assert vis["palette"] == PALETTE
    assert vis["min"] == "-0.2"
    assert vis["max"] == "0.9"
    assert "gamma" not in vis
    assert result["tvi-ndvi"]["select"] == (["B8", "B4"], ["NIR", "RED"])


@pytest.mark.asyncio
async def test_sentinel_sem_indice_mantem_gamma_e_nao_emite_chaves_nulas(manager):
    result = await vis_params_db.get_visparams_dict()
    vis = result["tvi-rgb"]["visparam"]
    assert vis["gamma"] == "1.35"
    assert "index" not in vis
    assert "palette" not in vis


@pytest.mark.asyncio
async def test_landsat_por_colecao_sem_gamma_none(manager):
    vis = await vis_params_db.get_landsat_vis_params_async("landsat-tvi-ndvi", "LANDSAT/LC08/C02/T1_L2")
    assert vis["bands"] == ["SR_B5", "SR_B4"]
    assert vis["index"]["name"] == "NDVI"
    assert vis["palette"] == PALETTE
    assert "gamma" not in vis


@pytest.mark.asyncio
async def test_landsat_no_dicionario_geral_sem_gamma_none(manager):
    result = await vis_params_db.get_visparams_dict()
    vis = result["landsat-tvi-ndvi"]["visparam"]["LANDSAT/LC08/C02/T1_L2"]
    assert "gamma" not in vis
    assert vis["index"]["bands"] == ["SR_B5", "SR_B4"]
```

- [ ] **Step 2: Executar o teste para confirmar a falha**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_vis_params_dict.py -q`
Expected: FAIL nos testes de NDVI porque `gamma` aparece como a string `"None"` (`assert "gamma" not in vis`).

- [ ] **Step 3: Usar `exclude_none` nas três conversões**

Em `app/visualization/vis_params_db.py`, método `get_landsat_vis_params`, trocar:

```python
                landsat_params = sat_config.vis_params.model_dump()
```

por:

```python
                landsat_params = sat_config.vis_params.model_dump(exclude_none=True)
```

Na função `get_visparams_dict`, trocar:

```python
            vis_params = doc.vis_params.model_dump()
```

por:

```python
            vis_params = doc.vis_params.model_dump(exclude_none=True)
```

e, no ramo Landsat da mesma função, trocar:

```python
                landsat_vis_params = sat_config.vis_params.model_dump()
```

por:

```python
                landsat_vis_params = sat_config.vis_params.model_dump(exclude_none=True)
```

Os laços de conversão de `min`, `max` e `gamma` em string permanecem como estão: já testam `if key in ...` e agora só encontram chaves presentes.

- [ ] **Step 4: Executar os testes**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_vis_params_dict.py tests/unit/test_vis_params_model.py -q`
Expected: `12 passed`.

- [ ] **Step 5: Commit (com autorização do usuário)**

```bash
cd /home/tharles/projects_lapig/tiles
git add app/visualization/vis_params_db.py tests/unit/test_vis_params_dict.py
git commit -m "fix(tiles): conversão de visparams ignora campos nulos do documento"
```

---

### Task 4: NDVI no dicionário fixo e na validação Landsat

**Files:**
- Modify: `app/visualization/visParam.py:1-53`
- Modify: `tests/unit/test_validate_landsat_request.py:21`
- Test: `tests/unit/test_visparam_ndvi_entries.py`

**Interfaces:**
- Consumes: `VisParam` da Tarefa 1 (usado só no teste para validar as entradas).
- Produces: `NDVI_PALETTE: list[str]` (9 cores); `VISPARAMS["tvi-ndvi"]` com chaves `display_name`, `select`, `visparam`; `VISPARAMS["landsat-tvi-ndvi"]` com `display_name` e `visparam` por coleção. `KNOWN_LANDSAT_VISPARAMS` passa a conter `landsat-tvi-ndvi` sem alteração em `validation.py`.

- [ ] **Step 1: Escrever os testes que falham**

Criar `tests/unit/test_visparam_ndvi_entries.py`:

```python
"""Entradas NDVI do dicionário fixo: coerentes com o modelo e aceitas na validação Landsat."""
from app.models.vis_params import VisParam
from app.visualization.validation import KNOWN_LANDSAT_VISPARAMS
from app.visualization.visParam import NDVI_PALETTE, VISPARAMS, get_landsat_vis_params

LANDSAT_NIR_RED = {
    "LANDSAT/LT05/C02/T1_L2": ["SR_B4", "SR_B3"],
    "LANDSAT/LE07/C02/T1_L2": ["SR_B4", "SR_B3"],
    "LANDSAT/LC08/C02/T1_L2": ["SR_B5", "SR_B4"],
    "LANDSAT/LC09/C02/T1_L2": ["SR_B5", "SR_B4"],
}


def test_paleta_de_nove_cores():
    assert len(NDVI_PALETTE) == 9
    assert NDVI_PALETTE[0] == "#a52a2a"
    assert NDVI_PALETTE[-1] == "#006837"


def test_tvi_ndvi_valida_no_modelo():
    entry = VISPARAMS["tvi-ndvi"]
    vp = VisParam(**entry["visparam"])
    assert entry["display_name"] == "NDVI"
    assert entry["select"] == (["B8", "B4"], ["NIR", "RED"])
    assert vp.bands == ["NIR", "RED"]
    assert vp.index.bands == ["NIR", "RED"]
    assert vp.index.name == "NDVI"
    assert vp.min == [-0.2]
    assert vp.max == [0.9]
    assert vp.palette == NDVI_PALETTE
    assert vp.gamma is None


def test_landsat_tvi_ndvi_por_colecao():
    assert VISPARAMS["landsat-tvi-ndvi"]["display_name"] == "NDVI"
    for collection, bands in LANDSAT_NIR_RED.items():
        vis = get_landsat_vis_params("landsat-tvi-ndvi", collection)
        vp = VisParam(**vis)
        assert vp.bands == bands, collection
        assert vp.index.bands == bands, collection
        assert vp.min == [-0.2]
        assert vp.max == [0.9]
        assert vp.palette == NDVI_PALETTE
        assert "gamma" not in vis


def test_landsat_tvi_ndvi_aceito_na_validacao():
    assert "landsat-tvi-ndvi" in KNOWN_LANDSAT_VISPARAMS


def test_entradas_existentes_nao_mudaram():
    assert VISPARAMS["tvi-red"]["visparam"]["bands"] == ["REDEDGE4", "SWIR1", "RED"]
    assert "display_name" not in VISPARAMS["tvi-red"]
```

Em `tests/unit/test_validate_landsat_request.py`, linha 21, trocar:

```python
KNOWN_VISPARAMS = {"landsat-tvi-true", "landsat-tvi-false", "landsat-tvi-agri"}
```

por:

```python
KNOWN_VISPARAMS = {"landsat-tvi-true", "landsat-tvi-false", "landsat-tvi-agri", "landsat-tvi-ndvi"}
```

- [ ] **Step 2: Executar os testes para confirmar a falha**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_visparam_ndvi_entries.py tests/unit/test_validate_landsat_request.py -q`
Expected: FAIL com `ImportError: cannot import name 'NDVI_PALETTE'` e, no teste parametrizado, `invalid_visparam` para `landsat-tvi-ndvi`.

- [ ] **Step 3: Acrescentar as entradas ao dicionário fixo**

Em `app/visualization/visParam.py`, inserir antes de `VISPARAMS = {` (linha 1):

```python
# Rampa de marrom (solo exposto) a verde (vegetação densa) para índices de vegetação.
NDVI_PALETTE = [
    "#a52a2a", "#c4813e", "#e6c26b", "#fff2a8", "#d9ef8b",
    "#a6d96a", "#66bd63", "#1a9850", "#006837",
]


def _ndvi_landsat(nir: str, red: str) -> dict:
    return {
        "bands": [nir, red],
        "min": [-0.2],
        "max": [0.9],
        "palette": NDVI_PALETTE,
        "index": {"type": "normalized_difference", "bands": [nir, red], "name": "NDVI"},
    }


```

Dentro de `VISPARAMS`, após a entrada `"tvi-rgb": {...},` (linha 28) e antes de `'landsat-tvi-true'`, inserir:

```python
    "tvi-ndvi": {
        "display_name": "NDVI",
        "select": (["B8", "B4"], ["NIR", "RED"]),
        "visparam": {
            "bands": ["NIR", "RED"],
            "min": "-0.2",
            "max": "0.9",
            "palette": NDVI_PALETTE,
            "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"},
        },
    },
```

Após a entrada `'landsat-tvi-false': {...}` (última do dicionário, linha 52), inserir antes do `}` que fecha `VISPARAMS`:

```python
    'landsat-tvi-ndvi': {
        "display_name": "NDVI",
        "visparam": {
            'LANDSAT/LT05/C02/T1_L2': _ndvi_landsat('SR_B4', 'SR_B3'),
            'LANDSAT/LE07/C02/T1_L2': _ndvi_landsat('SR_B4', 'SR_B3'),
            'LANDSAT/LC08/C02/T1_L2': _ndvi_landsat('SR_B5', 'SR_B4'),
            'LANDSAT/LC09/C02/T1_L2': _ndvi_landsat('SR_B5', 'SR_B4'),
        }
    },
```

Confirmar que a entrada `'landsat-tvi-false'` termina com vírgula após o `}` de fechamento.

- [ ] **Step 4: Executar os testes**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_visparam_ndvi_entries.py tests/unit/test_validate_landsat_request.py -q`
Expected: todos aprovados (5 novos mais os existentes, incluindo o parametrizado com quatro visparams).

- [ ] **Step 5: Commit (com autorização do usuário)**

```bash
cd /home/tharles/projects_lapig/tiles
git add app/visualization/visParam.py tests/unit/test_visparam_ndvi_entries.py tests/unit/test_validate_landsat_request.py
git commit -m "feat(tiles): visparams NDVI para Sentinel-2 e Landsat no dicionário fixo"
```

---

### Task 5: Construtores de camada em `layers.py`

**Files:**
- Modify: `app/api/layers.py:36-38` (imports), `app/api/layers.py:160-168` (`_create_s2_layer_sync`), `app/api/layers.py:313-315` (`_create_landsat_layer_sync`), `app/api/layers.py:449-451` (`_create_landsat_layer_with_params`)
- Test: `tests/unit/test_layers_index_visualization.py`

**Interfaces:**
- Consumes: `resolve_visualization(image, vis)` da Tarefa 2.
- Produces: os três construtores registram a banda derivada quando o visparam tem `index`; assinaturas inalteradas.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_layers_index_visualization.py`:

```python
"""Construtores de camada renderizam visparams de índice (banda derivada + paleta).

A fixture replica os stubs de `test_landsat_band_fallback.py`: o módulo
`app.api.layers` importa cache, rate limiter e loaders pesados que não fazem
parte do que se testa aqui.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

PALETTE = ["#a52a2a", "#006837"]
S2_NDVI = {
    "select": (["B8", "B4"], ["NIR", "RED"]),
    "visparam": {
        "bands": ["NIR", "RED"], "min": "-0.2", "max": "0.9", "palette": PALETTE,
        "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"},
    },
}
S2_RED = {
    "select": (["B4", "B8A", "B11"], ["RED", "REDEDGE4", "SWIR1"]),
    "visparam": {"gamma": "1.1", "max": "5400, 4300, 2800", "min": "700, 600, 400",
                 "bands": ["REDEDGE4", "SWIR1", "RED"]},
}
LANDSAT_NDVI = {
    "bands": ["SR_B5", "SR_B4"], "min": "-0.2", "max": "0.9", "palette": PALETTE,
    "index": {"type": "normalized_difference", "bands": ["SR_B5", "SR_B4"], "name": "NDVI"},
}
DATES = {"dtStart": "2020-06-01", "dtEnd": "2020-10-30"}


@pytest.fixture
def layers_module():
    from tests.conftest import reset_app_imports
    reset_app_imports()

    cache_mod = type(sys)("app.cache.cache")

    async def _noop(*a, **k): return None
    async def _none(*a, **k): return None
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_lock(*_a, **_k):
        yield True

    cache_mod.aget_png = _none
    cache_mod.aset_png = _noop
    cache_mod.aget_meta = _none
    cache_mod.aset_meta = _noop
    cache_mod.adelete_meta = _noop
    cache_mod.atile_lock = _fake_lock
    cache_mod.PNG_TTL = 30 * 24 * 3600
    cache_mod.PNG_TTL_HISTORICAL = 365 * 24 * 3600
    sys.modules["app.cache.cache"] = cache_mod

    rate_limiter_mod = type(sys)("app.middleware.rate_limiter")
    def _pt(*_a, **_k):
        def deco(fn): return fn
        return deco
    rate_limiter_mod.limit_sentinel = _pt
    rate_limiter_mod.limit_landsat = _pt
    sys.modules["app.middleware.rate_limiter"] = rate_limiter_mod

    visParam_mod = type(sys)("app.visualization.visParam")
    visParam_mod.VISPARAMS = {
        "landsat-tvi-true": {"visparam": {}},
        "landsat-tvi-false": {"visparam": {}},
        "landsat-tvi-agri": {"visparam": {}},
        "landsat-tvi-ndvi": {"visparam": {}},
    }
    visParam_mod.NDVI_PALETTE = PALETTE
    visParam_mod.get_landsat_collection = lambda y: "LANDSAT/LC08/C02/T1_L2"
    visParam_mod.get_landsat_vis_params = lambda *a, **k: dict(LANDSAT_NDVI)
    sys.modules["app.visualization.visParam"] = visParam_mod

    vpdb = type(sys)("app.visualization.vis_params_db")
    async def _vp(*a, **k): return dict(LANDSAT_NDVI)
    vpdb.get_landsat_vis_params_async = _vp
    vpdb.vis_params_manager = object()
    vpdb.get_visparams_dict = lambda: {}
    vpdb.get_landsat_collection = lambda y: "LANDSAT/LC08/C02/T1_L2"
    sys.modules["app.visualization.vis_params_db"] = vpdb

    vpl = type(sys)("app.visualization.vis_params_loader")
    vpl.VISPARAMS = {}
    vpl.get_VISPARAMS_sync = lambda: {}
    async def _vps(*a, **k): return {}
    vpl.get_visparams = _vps
    vpl.get_landsat_vis_params = lambda *a, **k: dict(LANDSAT_NDVI)
    vpl.get_landsat_collection = lambda year: "LANDSAT/LC08/C02/T1_L2"
    sys.modules["app.visualization.vis_params_loader"] = vpl

    caps = type(sys)("app.utils.capabilities")
    caps.get_capabilities_provider = lambda: type("P", (), {
        "get_capabilities": staticmethod(lambda *a, **k: {"collections": []}),
    })()
    sys.modules["app.utils.capabilities"] = caps

    svc_tile = type(sys)("app.services.tile")
    svc_tile.tile2goehashBBOX = lambda x, y, z: ({"w": -50, "s": -10, "e": -49, "n": -9}, "abc")
    sys.modules["app.services.tile"] = svc_tile

    gee_pool = type(sys)("app.core.gee_pool")
    gee_pool.gee_retry = lambda *a, **k: (lambda fn: fn)
    sys.modules["app.core.gee_pool"] = gee_pool

    http_util = type(sys)("app.utils.http")
    async def _hgb(url, **k): return b""
    http_util.http_get_bytes = _hgb
    class _EarthEngineRateLimitedError(Exception):
        def __init__(self, message: str = "", sa_name=None):
            super().__init__(message)
            self.sa_name = sa_name
    http_util.EarthEngineRateLimitedError = _EarthEngineRateLimitedError
    sys.modules["app.utils.http"] = http_util

    ee_tile_fetch_mod = type(sys)("app.utils.ee_tile_fetch")
    async def _fetch_tile(*_a, **_k): return b""
    ee_tile_fetch_mod.fetch_tile_with_rotation = _fetch_tile
    sys.modules["app.utils.ee_tile_fetch"] = ee_tile_fetch_mod

    from app.api import layers
    return layers


def _capture_getmapid(captured):
    def fake(params):
        captured.update(params)
        return {"tile_fetcher": type("T", (), {"url_format": "https://ee/{x}/{y}/{z}"})()}
    return fake


def test_s2_com_indice_registra_banda_derivada_e_paleta(layers_module):
    import ee
    captured = {}
    with patch.object(ee.data, "getMapId", side_effect=_capture_getmapid(captured)):
        url = layers_module._create_s2_layer_sync(None, DATES, S2_NDVI)
    assert url == "https://ee/{x}/{y}/{z}"
    assert captured["bands"] == ["NDVI"]
    assert captured["palette"] == PALETTE
    assert captured["min"] == "-0.2"
    assert captured["max"] == "0.9"
    assert "gamma" not in captured
    assert "index" not in captured


def test_s2_sem_indice_mantem_parametros_originais(layers_module):
    import ee
    captured = {}
    with patch.object(ee.data, "getMapId", side_effect=_capture_getmapid(captured)):
        layers_module._create_s2_layer_sync(None, DATES, S2_RED)
    assert captured["bands"] == ["REDEDGE4", "SWIR1", "RED"]
    assert captured["gamma"] == "1.1"
    assert "palette" not in captured


def test_landsat_with_params_com_indice_registra_banda_derivada(layers_module):
    import ee
    captured = {}
    with patch.object(ee.data, "getMapId", side_effect=_capture_getmapid(captured)):
        url = layers_module._create_landsat_layer_with_params(None, DATES, dict(LANDSAT_NDVI), "BEST_IMAGE")
    assert url == "https://ee/{x}/{y}/{z}"
    assert captured["bands"] == ["NDVI"]
    assert captured["palette"] == PALETTE
    assert "index" not in captured


def test_landsat_sync_com_indice_registra_banda_derivada(layers_module):
    import ee
    captured = {}
    with patch.object(ee.data, "getMapId", side_effect=_capture_getmapid(captured)):
        url = layers_module._create_landsat_layer_sync(None, DATES, "landsat-tvi-ndvi", "MOSAIC")
    assert url == "https://ee/{x}/{y}/{z}"
    assert captured["bands"] == ["NDVI"]
    assert captured["min"] == "-0.2"
    assert captured["palette"] == PALETTE
```

- [ ] **Step 2: Executar o teste para confirmar a falha**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_layers_index_visualization.py -q`
Expected: FAIL em `assert captured["bands"] == ["NDVI"]` (os construtores ainda enviam as bandas de entrada e a chave `index`).

- [ ] **Step 3: Chamar o auxiliar nos três construtores**

Em `app/api/layers.py`, após a linha `from app.visualization.validation import validate_landsat_request` (linha 36), inserir:

```python
from app.visualization.indices import resolve_visualization
```

Em `_create_s2_layer_sync`, trocar:

```python
    best = s2.mosaic()
    map_id = ee.data.getMapId({"image": best, **vis["visparam"]})
    return map_id["tile_fetcher"].url_format
```

por:

```python
    best = s2.mosaic()
    image, vis_ee = resolve_visualization(best, vis["visparam"])
    map_id = ee.data.getMapId({"image": image, **vis_ee})
    return map_id["tile_fetcher"].url_format
```

Em `_create_landsat_layer_sync`, trocar:

```python
    try:
        map_id = ee.data.getMapId({"image": landsat, **vis})
        return map_id["tile_fetcher"].url_format
    except ee.EEException as e:
        return _retry_with_mosaic_if_band_missing(
            _create_landsat_layer_sync, e, composite_mode,
```

por:

```python
    try:
        image, vis_ee = resolve_visualization(landsat, vis)
        map_id = ee.data.getMapId({"image": image, **vis_ee})
        return map_id["tile_fetcher"].url_format
    except ee.EEException as e:
        return _retry_with_mosaic_if_band_missing(
            _create_landsat_layer_sync, e, composite_mode,
```

Em `_create_landsat_layer_with_params`, trocar:

```python
    try:
        map_id = ee.data.getMapId({"image": landsat, **vis})
        return map_id["tile_fetcher"].url_format
    except ee.EEException as e:
        return _retry_with_mosaic_if_band_missing(
            _create_landsat_layer_with_params, e, composite_mode,
```

por:

```python
    try:
        image, vis_ee = resolve_visualization(landsat, vis)
        map_id = ee.data.getMapId({"image": image, **vis_ee})
        return map_id["tile_fetcher"].url_format
    except ee.EEException as e:
        return _retry_with_mosaic_if_band_missing(
            _create_landsat_layer_with_params, e, composite_mode,
```

- [ ] **Step 4: Executar os testes do módulo e os que já cobriam os construtores**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_layers_index_visualization.py tests/unit/test_landsat_band_fallback.py tests/unit/test_empty_image_with_bands.py tests/integration/ -q`
Expected: todos aprovados.

- [ ] **Step 5: Commit (com autorização do usuário)**

```bash
cd /home/tharles/projects_lapig/tiles
git add app/api/layers.py tests/unit/test_layers_index_visualization.py
git commit -m "feat(tiles): construtores de camada renderizam visparams de índice"
```

---

### Task 6: Réplicas de pré-aquecimento em `cache_operations.py`

**Files:**
- Modify: `app/tasks/cache_operations.py:22` (import), `app/tasks/cache_operations.py:691-731` (`_warm_create_landsat_url` e `_warm_create_s2_url`)
- Test: `tests/unit/test_warm_index_visualization.py`

**Interfaces:**
- Consumes: `resolve_visualization(image, vis)` da Tarefa 2.
- Produces: as duas réplicas registram a banda derivada nos dois ramos (pool de SAs e credencial da campanha); assinaturas inalteradas.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_warm_index_visualization.py`:

```python
"""Pré-aquecimento com visparam de índice: banda derivada e paleta chegam ao registro do mapa.

Mesma técnica de `test_warm_tvi_credential.py`: `ops` importado na coleta e
colaboradores trocados com `patch.object` e `patch.dict(sys.modules, ...)`.
"""
import sys
import types
from unittest.mock import MagicMock, patch

import app.tasks.cache_operations as ops

CRED = {"access_token": "at", "project_id": "p", "expires_at": 9999999999999}
DATES = {"dtStart": "2020-01-01", "dtEnd": "2020-12-31"}
PALETTE = ["#a52a2a", "#006837"]


def _loader_stub():
    mod = types.ModuleType("app.visualization.vis_params_loader")
    mod.get_VISPARAMS_sync = lambda: {
        "tvi-ndvi": {
            "select": (["B8", "B4"], ["NIR", "RED"]),
            "visparam": {
                "bands": ["NIR", "RED"], "min": "-0.2", "max": "0.9", "palette": PALETTE,
                "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"},
            },
        },
    }
    return mod


def _vis_param_stub():
    mod = types.ModuleType("app.visualization.visParam")
    mod.get_landsat_collection = lambda year: "LANDSAT/LC08/C02/T1_L2"
    mod.get_landsat_vis_params = lambda name, collection: {
        "bands": ["SR_B5", "SR_B4"], "min": [-0.2], "max": [0.9], "palette": PALETTE,
        "index": {"type": "normalized_difference", "bands": ["SR_B5", "SR_B4"], "name": "NDVI"},
    }
    return mod


def _pool_getmapid(captured):
    def fake(params):
        captured.update(params)
        return {"tile_fetcher": type("T", (), {"url_format": "pool-url"})()}
    return fake


def test_warm_s2_ndvi_com_credencial_envia_banda_derivada_e_paleta():
    with patch.dict(sys.modules, {"app.visualization.vis_params_loader": _loader_stub()}), \
         patch.object(ops, "get_campaign_token", return_value=CRED), \
         patch.object(ops, "create_map_with_credential",
                      return_value="https://ee/s2/{z}/{x}/{y}") as mock_create:
        url = ops._warm_create_s2_url(MagicMock(), DATES, "tvi-ndvi", tvi_campaign_id="c1")
    assert url == "https://ee/s2/{z}/{x}/{y}"
    _, vis, _ = mock_create.call_args.args
    assert vis == {"bands": ["NDVI"], "min": "-0.2", "max": "0.9", "palette": PALETTE}


def test_warm_s2_ndvi_pelo_pool_envia_banda_derivada():
    captured = {}
    with patch.dict(sys.modules, {"app.visualization.vis_params_loader": _loader_stub()}), \
         patch.object(ops.ee.data, "getMapId", side_effect=_pool_getmapid(captured)):
        url = ops._warm_create_s2_url(MagicMock(), DATES, "tvi-ndvi")
    assert url == "pool-url"
    assert captured["bands"] == ["NDVI"]
    assert captured["palette"] == PALETTE
    assert "gamma" not in captured
    assert "index" not in captured


def test_warm_landsat_ndvi_pelo_pool_converte_faixa_e_envia_banda_derivada():
    captured = {}
    with patch.dict(sys.modules, {"app.visualization.visParam": _vis_param_stub()}), \
         patch.object(ops.ee.data, "getMapId", side_effect=_pool_getmapid(captured)):
        url = ops._warm_create_landsat_url(MagicMock(), DATES, "landsat-tvi-ndvi", "MOSAIC")
    assert url == "pool-url"
    assert captured["bands"] == ["NDVI"]
    assert captured["min"] == "-0.2"
    assert captured["max"] == "0.9"
    assert captured["palette"] == PALETTE
    assert "index" not in captured


def test_warm_landsat_ndvi_com_credencial_envia_banda_derivada():
    with patch.dict(sys.modules, {"app.visualization.visParam": _vis_param_stub()}), \
         patch.object(ops, "get_campaign_token", return_value=CRED), \
         patch.object(ops, "create_map_with_credential",
                      return_value="https://ee/tpl/{z}/{x}/{y}") as mock_create:
        ops._warm_create_landsat_url(MagicMock(), DATES, "landsat-tvi-ndvi", "MOSAIC", tvi_campaign_id="c1")
    _, vis, _ = mock_create.call_args.args
    assert vis == {"bands": ["NDVI"], "min": [-0.2], "max": [0.9], "palette": PALETTE}
```

- [ ] **Step 2: Executar o teste para confirmar a falha**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_warm_index_visualization.py -q`
Expected: FAIL em `assert vis == {...}` e `assert captured["bands"] == ["NDVI"]`.

- [ ] **Step 3: Chamar o auxiliar nas duas réplicas**

Em `app/tasks/cache_operations.py`, após `from app.utils.ee_maps import create_map_with_credential` (linha 22), inserir:

```python
from app.visualization.indices import resolve_visualization
```

Substituir integralmente a função `_warm_create_landsat_url`:

```python
@gee_retry()
def _warm_create_landsat_url(geom, dates, visparam_name, composite_mode, tvi_campaign_id=None):
    """Gera URL Landsat EE — réplica simplificada de _create_landsat_layer_sync.

    Com ``tvi_campaign_id``, o mapa é registrado com a credencial do
    responsável GEE da campanha (tvi-api) em vez da SA do pool.
    """
    image, vis = _build_landsat_image(geom, dates, visparam_name, composite_mode)

    if tvi_campaign_id:
        credential = get_campaign_token(tvi_campaign_id)
        cred_image, vis_ee = resolve_visualization(ee.Image(image), vis)
        return create_map_with_credential(cred_image, vis_ee, credential)

    for key in ("min", "max", "gamma"):
        if isinstance(vis.get(key), list):
            vis[key] = ",".join(map(str, vis[key]))
    image, vis_ee = resolve_visualization(image, vis)
    map_id = ee.data.getMapId({"image": image, **vis_ee})
    return map_id["tile_fetcher"].url_format
```

Substituir integralmente a função `_warm_create_s2_url`:

```python
@gee_retry()
def _warm_create_s2_url(geom, dates, visparam_name, tvi_campaign_id=None):
    """Gera URL S2 EE — réplica simplificada de _create_s2_layer_sync."""
    from app.visualization.vis_params_loader import get_VISPARAMS_sync

    visparams = get_VISPARAMS_sync()
    vis = visparams.get(visparam_name, visparams.get("tvi-red", {}))

    s2 = (ee.ImageCollection("COPERNICUS/S2_HARMONIZED")
          .filterDate(dates["dtStart"], dates["dtEnd"])
          .filterBounds(geom)
          .sort("CLOUDY_PIXEL_PERCENTAGE", False)
          .select(*vis.get("select", ["B4", "B3", "B2"])))
    best = s2.mosaic()
    image, vis_ee = resolve_visualization(best, vis.get("visparam", {}))

    if tvi_campaign_id:
        credential = get_campaign_token(tvi_campaign_id)
        return create_map_with_credential(image, vis_ee, credential)

    map_id = ee.data.getMapId({"image": image, **vis_ee})
    return map_id["tile_fetcher"].url_format
```

- [ ] **Step 4: Executar os testes de pré-aquecimento**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_warm_index_visualization.py tests/unit/test_warm_tvi_credential.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Commit (com autorização do usuário)**

```bash
cd /home/tharles/projects_lapig/tiles
git add app/tasks/cache_operations.py tests/unit/test_warm_index_visualization.py
git commit -m "feat(tiles): pré-aquecimento renderiza visparams de índice"
```

---

### Task 7: `legend` nas capabilities e no fallback fixo

**Files:**
- Modify: `app/utils/capabilities.py:1-12` (imports e helpers de módulo), `app/utils/capabilities.py:157-169` (`param_info`), `app/utils/capabilities.py:232-276` (`_get_hardcoded_capabilities`)
- Test: `tests/unit/test_capabilities_legend.py`

**Interfaces:**
- Consumes: `VISPARAMS` da Tarefa 4 (para o fallback).
- Produces: `build_legend(vis_params: Optional[dict]) -> Optional[dict]` com chaves `label` (str), `min` (float), `max` (float), `palette` (list[str]); cada item de `visparam_details` ganha `legend` apenas quando o documento tem `index`.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_capabilities_legend.py`:

```python
"""Legenda dos visparams de índice publicada em visparam_details."""
import sys
from unittest.mock import patch

import pytest

from app.utils import capabilities as caps
from app.visualization import visParam as real_visparam

PALETTE = ["#a52a2a", "#006837"]
NDVI_INDEX = {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"}


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return list(self._docs)


class _Collection:
    def __init__(self, docs):
        self._docs = docs

    def find(self, _query):
        return _Cursor(self._docs)


class _Db:
    def __init__(self, docs):
        self.vis_params = _Collection(docs)


DOCS = [
    {"name": "tvi-red", "display_name": "TVI Red", "category": "sentinel2", "active": True,
     "vis_params": {"bands": ["REDEDGE4", "SWIR1", "RED"], "min": [700, 600, 400],
                    "max": [5400, 4300, 2800], "gamma": 1.1}},
    {"name": "tvi-ndvi", "display_name": "NDVI", "category": "sentinel2", "active": True,
     "vis_params": {"bands": ["NIR", "RED"], "min": [-0.2], "max": [0.9], "palette": PALETTE,
                    "index": NDVI_INDEX}},
    {"name": "landsat-tvi-ndvi", "display_name": "NDVI", "category": "landsat", "active": True,
     "satellite_configs": [{
         "collection_id": "LANDSAT/LC08/C02/T1_L2",
         "vis_params": {"bands": ["SR_B5", "SR_B4"], "min": "-0.2", "max": "0.9", "palette": PALETTE,
                        "index": {"type": "normalized_difference", "bands": ["SR_B5", "SR_B4"],
                                  "name": "NDVI"}},
     }]},
]


def _details(capabilities, satellite):
    coll = next(c for c in capabilities["collections"] if c["satellite"] == satellite)
    return {d["name"]: d for d in coll["visparam_details"]}


def test_build_legend_com_faixa_em_lista():
    legend = caps.build_legend({"min": [-0.2], "max": [0.9], "palette": PALETTE, "index": NDVI_INDEX})
    assert legend == {"label": "NDVI", "min": -0.2, "max": 0.9, "palette": PALETTE}


def test_build_legend_com_faixa_em_string():
    legend = caps.build_legend({"min": "-0.2", "max": "0.9", "palette": PALETTE, "index": NDVI_INDEX})
    assert legend["min"] == -0.2
    assert legend["max"] == 0.9


def test_build_legend_sem_indice_devolve_none():
    assert caps.build_legend({"bands": ["B4"], "min": [0], "max": [1]}) is None
    assert caps.build_legend(None) is None


@pytest.mark.asyncio
async def test_capabilities_dinamicas_incluem_legend_apenas_nos_indices():
    provider = caps.CapabilitiesProvider()
    with patch.object(caps, "get_database", return_value=_Db(DOCS)):
        result = await provider.get_capabilities()
    sentinel = _details(result, "sentinel")
    landsat = _details(result, "landsat")
    assert "legend" not in sentinel["tvi-red"]
    assert sentinel["tvi-ndvi"]["legend"] == {"label": "NDVI", "min": -0.2, "max": 0.9, "palette": PALETTE}
    assert landsat["landsat-tvi-ndvi"]["legend"] == {"label": "NDVI", "min": -0.2, "max": 0.9, "palette": PALETTE}


def test_fallback_fixo_lista_ndvi_com_legend():
    # Outras suítes substituem app.visualization.visParam em sys.modules por stubs
    # e não restauram; o fallback importa o módulo sob demanda.
    with patch.dict(sys.modules, {"app.visualization.visParam": real_visparam}):
        result = caps.CapabilitiesProvider()._get_hardcoded_capabilities()
    sentinel_coll = next(c for c in result["collections"] if c["satellite"] == "sentinel")
    landsat_coll = next(c for c in result["collections"] if c["satellite"] == "landsat")
    assert "tvi-ndvi" in sentinel_coll["visparam"]
    assert "landsat-tvi-ndvi" in landsat_coll["visparam"]
    sentinel = _details(result, "sentinel")
    landsat = _details(result, "landsat")
    assert sentinel["tvi-ndvi"]["display_name"] == "NDVI"
    assert sentinel["tvi-ndvi"]["legend"]["label"] == "NDVI"
    assert sentinel["tvi-ndvi"]["legend"]["min"] == -0.2
    assert landsat["landsat-tvi-ndvi"]["legend"]["max"] == 0.9
    assert len(landsat["landsat-tvi-ndvi"]["legend"]["palette"]) == 9
    assert "legend" not in sentinel["tvi-red"]
```

- [ ] **Step 2: Executar o teste para confirmar a falha**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_capabilities_legend.py -q`
Expected: FAIL com `AttributeError: module 'app.utils.capabilities' has no attribute 'build_legend'`.

- [ ] **Step 3: Implementar `build_legend` e publicar `legend`**

Em `app/utils/capabilities.py`, após `logger = logging.getLogger(__name__)` (linha 10) e antes de `class CapabilitiesProvider`, inserir:

```python
def _first_number(value) -> Optional[float]:
    """Accepts the stored forms of min/max: list, comma-separated string or number."""
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, str):
        value = value.split(",")[0].strip()
    if value is None or value == "":
        return None
    return float(value)


def build_legend(vis_params: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Legend metadata for single-band index visualizations, None otherwise."""
    if not vis_params or not vis_params.get("index"):
        return None
    index = vis_params["index"]
    return {
        "label": index.get("name") or "NDVI",
        "min": _first_number(vis_params.get("min")),
        "max": _first_number(vis_params.get("max")),
        "palette": list(vis_params.get("palette") or []),
    }


def _legend_for_document(vp: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if vp.get("vis_params"):
        return build_legend(vp["vis_params"])
    configs = vp.get("satellite_configs") or []
    if configs:
        return build_legend(configs[0].get("vis_params"))
    return None
```

No método `get_capabilities`, trocar o bloco:

```python
                for vp in vis_params_list:
                    param_info = {
                        "name": vp["name"],
                        "display_name": vp.get("display_name", vp["name"]),
                        "description": vp.get("description", ""),
                        "tags": vp.get("tags", [])
                    }

                    if vp.get("category") in ["sentinel", "sentinel2"]:
```

por:

```python
                for vp in vis_params_list:
                    param_info = {
                        "name": vp["name"],
                        "display_name": vp.get("display_name", vp["name"]),
                        "description": vp.get("description", ""),
                        "tags": vp.get("tags", [])
                    }
                    legend = _legend_for_document(vp)
                    if legend:
                        param_info["legend"] = legend

                    if vp.get("category") in ["sentinel", "sentinel2"]:
```

Substituir integralmente o método `_get_hardcoded_capabilities`:

```python
    def _get_hardcoded_capabilities(self) -> Dict[str, Any]:
        """Fallback to hardcoded capabilities"""
        from app.visualization.visParam import VISPARAMS

        current_year = datetime.now().year
        s2_ndvi_legend = build_legend(VISPARAMS["tvi-ndvi"]["visparam"])
        landsat_ndvi_legend = build_legend(VISPARAMS["landsat-tvi-ndvi"]["visparam"]["LANDSAT/LC08/C02/T1_L2"])
        return {
            "collections": [
                {
                    "name": "s2_harmonized",
                    "display_name": "Sentinel-2 Harmonized",
                    "satellite": "sentinel",
                    "visparam": ["tvi-green", "tvi-red", "tvi-rgb", "tvi-ndvi"],
                    "visparam_details": [
                        {"name": "tvi-green", "display_name": "TVI Green", "description": "SWIR1/REDEDGE4/RED"},
                        {"name": "tvi-red", "display_name": "TVI Red", "description": "REDEDGE4/SWIR1/RED"},
                        {"name": "tvi-rgb", "display_name": "RGB", "description": "Standard RGB"},
                        {"name": "tvi-ndvi", "display_name": "NDVI",
                         "description": "Normalized difference vegetation index (B8, B4)",
                         "legend": s2_ndvi_legend}
                    ],
                    "period": ["WET", "DRY", "MONTH"],
                    "year": list(range(2017, current_year + 1)),
                    "collections": ["COPERNICUS/S2_HARMONIZED"],
                    "cloud_filter": {"property": "CLOUDY_PIXEL_PERCENTAGE", "max_value": 20}
                },
                {
                    "name": "landsat",
                    "display_name": "Landsat Collection",
                    "satellite": "landsat",
                    "visparam": ["landsat-tvi-true", "landsat-tvi-agri", "landsat-tvi-false", "landsat-tvi-ndvi"],
                    "visparam_details": [
                        {"name": "landsat-tvi-true", "display_name": "True Color", "description": "Natural color RGB"},
                        {"name": "landsat-tvi-agri", "display_name": "Agriculture",
                         "description": "False color for vegetation"},
                        {"name": "landsat-tvi-false", "display_name": "False Color",
                         "description": "Standard false color"},
                        {"name": "landsat-tvi-ndvi", "display_name": "NDVI",
                         "description": "Normalized difference vegetation index (NIR, RED)",
                         "legend": landsat_ndvi_legend}
                    ],
                    "months": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"],
                    "year": list(range(1985, current_year + 1)),
                    "period": ["WET", "DRY", "MONTH"],
                    "cloud_filter": {"property": "CLOUD_COVER", "max_value": 20}
                }
            ],
            "metadata": {
                "last_updated": datetime.utcnow().isoformat(),
                "version": "1.0",
                "source": "hardcoded"
            }
        }
```

- [ ] **Step 4: Executar os testes**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_capabilities_legend.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Commit (com autorização do usuário)**

```bash
cd /home/tharles/projects_lapig/tiles
git add app/utils/capabilities.py tests/unit/test_capabilities_legend.py
git commit -m "feat(tiles): capabilities publicam legenda dos visparams de índice"
```

---

### Task 8: Semente idempotente com rótulo explícito

**Files:**
- Modify: `scripts/migrate_vis_params.py:23-101` (função `migrate_vis_params`, laço de documentos e inserção)
- Test: `tests/unit/test_migrate_vis_params.py`

**Interfaces:**
- Consumes: `VISPARAMS` da Tarefa 4; modelos da Tarefa 1.
- Produces: `build_documents(visparams: dict) -> list[dict]` (documentos prontos para gravação, com `display_name` explícito quando a entrada o define) e `async upsert_missing(collection, documents) -> tuple[int, int]` (`(inseridos, existentes)`), ambos usados por `migrate_vis_params()`.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/unit/test_migrate_vis_params.py`:

```python
"""Semente dos visparams: rótulo explícito, propagação do índice e idempotência."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "migrate_vis_params.py"
PALETTE = ["#a52a2a", "#006837"]

VISPARAMS = {
    "tvi-ndvi": {
        "display_name": "NDVI",
        "select": (["B8", "B4"], ["NIR", "RED"]),
        "visparam": {
            "bands": ["NIR", "RED"], "min": "-0.2", "max": "0.9", "palette": PALETTE,
            "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"},
        },
    },
    "tvi-rgb": {
        "select": ["B4", "B3", "B2"],
        "visparam": {"min": "200, 300, 700", "max": "3000, 2500, 2300", "bands": ["B4", "B3", "B2"], "gamma": "1.35"},
    },
    "landsat-tvi-ndvi": {
        "display_name": "NDVI",
        "visparam": {
            "LANDSAT/LC08/C02/T1_L2": {
                "bands": ["SR_B5", "SR_B4"], "min": [-0.2], "max": [0.9], "palette": PALETTE,
                "index": {"type": "normalized_difference", "bands": ["SR_B5", "SR_B4"], "name": "NDVI"},
            },
        },
    },
}


def _load_script():
    spec = importlib.util.spec_from_file_location("migrate_vis_params", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, upserted_id):
        self.upserted_id = upserted_id


class _Collection:
    def __init__(self):
        self.docs = {}

    async def update_one(self, flt, update, upsert=False):
        _id = flt["_id"]
        if _id in self.docs:
            return _Result(None)
        self.docs[_id] = dict(update["$setOnInsert"])
        return _Result(_id)


def test_build_documents_usa_display_name_explicito_e_propaga_indice():
    script = _load_script()
    docs = {d["_id"]: d for d in script.build_documents(VISPARAMS)}
    assert docs["tvi-ndvi"]["display_name"] == "NDVI"
    assert docs["tvi-ndvi"]["category"] == "sentinel2"
    assert docs["tvi-ndvi"]["band_config"] == {"original_bands": ["B8", "B4"], "mapped_bands": ["NIR", "RED"]}
    assert docs["tvi-ndvi"]["vis_params"]["index"]["name"] == "NDVI"
    assert docs["tvi-ndvi"]["vis_params"]["palette"] == PALETTE
    assert docs["tvi-ndvi"]["vis_params"]["min"] == [-0.2]
    assert docs["tvi-rgb"]["display_name"] == "Tvi Rgb"
    assert docs["landsat-tvi-ndvi"]["category"] == "landsat"
    assert docs["landsat-tvi-ndvi"]["display_name"] == "NDVI"
    assert docs["landsat-tvi-ndvi"]["satellite_configs"][0]["vis_params"]["index"]["bands"] == ["SR_B5", "SR_B4"]


@pytest.mark.asyncio
async def test_upsert_missing_nao_sobrescreve_existentes():
    script = _load_script()
    collection = _Collection()
    docs = script.build_documents(VISPARAMS)
    assert await script.upsert_missing(collection, docs) == (3, 0)
    collection.docs["tvi-ndvi"]["display_name"] = "NDVI ajustado em produção"
    assert await script.upsert_missing(collection, docs) == (0, 3)
    assert collection.docs["tvi-ndvi"]["display_name"] == "NDVI ajustado em produção"
```

- [ ] **Step 2: Executar o teste para confirmar a falha**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_migrate_vis_params.py -q`
Expected: FAIL com `AttributeError: module 'migrate_vis_params' has no attribute 'build_documents'`.

- [ ] **Step 3: Extrair `build_documents` e `upsert_missing`**

Em `scripts/migrate_vis_params.py`, substituir o trecho da função `migrate_vis_params` que vai de `documents = []` (linha 35) até `print(f"Inserted {len(result.inserted_ids)} visualization parameter documents")` (linha 101) por:

```python
    documents = build_documents(VISPARAMS)
    inserted, existing = await upsert_missing(collection, documents)
    print(f"Visualization parameters: {inserted} inserted, {existing} already present")
```

Inserir, antes de `async def migrate_vis_params():` (linha 23), as duas funções:

```python
def build_documents(visparams: dict) -> list:
    """Build vis_params documents from the hardcoded VISPARAMS structure."""
    documents = []

    for vis_name, config in visparams.items():
        display_name = config.get("display_name") or vis_name.replace('-', ' ').title()

        if vis_name.startswith('landsat'):
            satellite_configs = []
            for collection_id, params in config['visparam'].items():
                satellite_configs.append(SatelliteVisParam(
                    collection_id=collection_id,
                    vis_params=VisParam(**params)
                ))

            doc = VisParamDocument(
                _id=vis_name,
                name=vis_name,
                display_name=display_name,
                description=f"Landsat visualization parameters for {display_name}",
                category='landsat',
                satellite_configs=satellite_configs,
                tags=['landsat', 'multispectral']
            )
        else:
            band_config = None
            if 'select' in config:
                select_data = config['select']
                if isinstance(select_data, tuple) and len(select_data) == 2:
                    band_config = BandConfig(
                        original_bands=select_data[0],
                        mapped_bands=select_data[1]
                    )
                elif isinstance(select_data, list):
                    band_config = BandConfig(
                        original_bands=select_data,
                        mapped_bands=None
                    )

            doc = VisParamDocument(
                _id=vis_name,
                name=vis_name,
                display_name=display_name,
                description=f"Sentinel-2 visualization parameters for {display_name}",
                category='sentinel2',
                band_config=band_config,
                vis_params=VisParam(**config['visparam']),
                tags=['sentinel2', 'multispectral']
            )

        documents.append(doc.model_dump(by_alias=True))

    return documents


async def upsert_missing(collection, documents) -> tuple:
    """Insert only the documents whose _id is absent; never overwrite existing ones."""
    inserted = 0
    existing = 0
    for doc in documents:
        result = await collection.update_one(
            {"_id": doc["_id"]},
            {"$setOnInsert": doc},
            upsert=True
        )
        if result.upserted_id is not None:
            inserted += 1
        else:
            existing += 1
    return inserted, existing


```

Remover da função `migrate_vis_params` o comentário e a linha `# await collection.delete_many({})` (linhas 32 e 33), que deixaram de fazer sentido com a inserção seletiva. O restante da função (mapeamentos Landsat e Sentinel, índices e resumo) permanece igual.

- [ ] **Step 4: Executar os testes**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/test_migrate_vis_params.py -q`
Expected: `2 passed`.

- [ ] **Step 5: Executar a suíte unitária completa**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/pytest tests/unit/ -q`
Expected: todos aprovados.

- [ ] **Step 6: Commit (com autorização do usuário)**

```bash
cd /home/tharles/projects_lapig/tiles
git add scripts/migrate_vis_params.py tests/unit/test_migrate_vis_params.py
git commit -m "feat(tiles): semente de visparams idempotente com rótulo explícito"
```

---

### Task 9: Documentação do tiles

**Files:**
- Modify: `docs/VIS_PARAMS_API.md:44-70` e `docs/VIS_PARAMS_API.md:365-376`
- Modify: `docs/CAPABILITIES_API.md:32-46`
- Modify: `docs/LAYERS_API_GUIDE.md:56`, `docs/LAYERS_API_GUIDE.md:143-211`
- Modify: `docs/CHANGELOG.md:10-16`

**Interfaces:**
- Consumes: contratos das Tarefas 1, 7 e 8.
- Produces: documentação alinhada ao comportamento entregue.

- [ ] **Step 1: `docs/VIS_PARAMS_API.md`**

Após o bloco `**Body (Sentinel-2):** ... ` da seção "### 3. Criar Novo Parâmetro" (o JSON que termina em `"active": true\n}` seguido de três crases) e antes de `**Body (Landsat):**`, inserir:

````markdown
**Body (índice de banda única, por exemplo NDVI):**
```json
{
  "name": "tvi-ndvi",
  "display_name": "NDVI",
  "description": "Índice de vegetação por diferença normalizada (B8, B4)",
  "category": "sentinel2",
  "band_config": {
    "original_bands": ["B8", "B4"],
    "mapped_bands": ["NIR", "RED"]
  },
  "vis_params": {
    "bands": ["NIR", "RED"],
    "min": [-0.2],
    "max": [0.9],
    "palette": ["#a52a2a", "#c4813e", "#e6c26b", "#fff2a8", "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850", "#006837"],
    "index": {"type": "normalized_difference", "bands": ["NIR", "RED"], "name": "NDVI"}
  },
  "tags": ["sentinel2", "index", "ndvi"],
  "active": true
}
```

Com `index`, `bands` lista as bandas que precisam existir na imagem; a banda derivada (`index.name`) é calculada no servidor e renderizada com `palette`. `gamma` é ignorado nesse caso. Para Landsat, cada item de `satellite_configs` recebe o mesmo formato com as bandas da coleção (`SR_B4`/`SR_B3` em TM e ETM+, `SR_B5`/`SR_B4` em OLI).

````

Na seção "### VisParam" de "## Estrutura dos Dados", substituir o bloco TypeScript por:

````markdown
### VisParam
```typescript
{
  bands: string[]       // Bandas que precisam existir na imagem
  min: number[]        // Valores mínimos para cada banda (um valor quando há index)
  max: number[]        // Valores máximos para cada banda (um valor quando há index)
  gamma?: number       // Correção gamma (ignorada quando há index)
  palette?: string[]   // Cores CSS; exige index
  index?: IndexConfig  // Banda derivada renderizada no lugar das bandas
}
```

### IndexConfig
```typescript
{
  type: "normalized_difference"  // (bands[0] - bands[1]) / (bands[0] + bands[1])
  bands: string[]                // Exatamente duas bandas, contidas em VisParam.bands
  name: string                   // Nome da banda derivada (padrão "NDVI")
}
```
````

- [ ] **Step 2: `docs/CAPABILITIES_API.md`**

No exemplo de resposta da seção "### 1. Get All Capabilities", trocar:

```json
      "visparam": ["tvi-green", "tvi-red", "tvi-rgb"],
      "visparam_details": [
        {
          "name": "tvi-green",
          "display_name": "TVI Green",
          "description": "SWIR1/REDEDGE4/RED",
          "tags": ["vegetation", "analysis"]
        }
      ],
```

por:

```json
      "visparam": ["tvi-green", "tvi-red", "tvi-rgb", "tvi-ndvi"],
      "visparam_details": [
        {
          "name": "tvi-green",
          "display_name": "TVI Green",
          "description": "SWIR1/REDEDGE4/RED",
          "tags": ["vegetation", "analysis"]
        },
        {
          "name": "tvi-ndvi",
          "display_name": "NDVI",
          "description": "Normalized difference vegetation index (B8, B4)",
          "tags": ["sentinel2", "index", "ndvi"],
          "legend": {
            "label": "NDVI",
            "min": -0.2,
            "max": 0.9,
            "palette": ["#a52a2a", "#c4813e", "#e6c26b", "#fff2a8", "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850", "#006837"]
          }
        }
      ],
```

Após o bloco JSON dessa seção (antes de "### 2. Legacy Capabilities"), inserir:

```markdown
`legend` is present only for single-band index visualizations (documents with `vis_params.index`). Clients use it to draw a color bar: `palette` from `min` (left) to `max` (right), labeled with `label`.

```

- [ ] **Step 3: `docs/LAYERS_API_GUIDE.md`**

Na seção "### 1. Sentinel-2 Harmonized", trocar a linha:

```markdown
- `visparam`: Nome da visualização (ex: `"tvi-red"`, `"ndvi"`, `"rgb"`)
```

por:

```markdown
- `visparam`: Nome da visualização (ex: `"tvi-red"`, `"tvi-rgb"`, `"tvi-ndvi"`)
```

Substituir integralmente a seção "### Estrutura do Documento" (do título até o fim do bloco de código que antecede "### Adicionando Nova Visualização") por:

````markdown
### Estrutura do Documento

```javascript
{
  "_id": "tvi-ndvi",
  "name": "tvi-ndvi",
  "display_name": "NDVI",
  "description": "Índice de vegetação por diferença normalizada (B8, B4)",
  "category": "sentinel2",  // ou "landsat"
  "active": true,
  "tags": ["sentinel2", "index", "ndvi"],

  // Sentinel-2: bandas originais e nomes mapeados usados em vis_params.bands
  "band_config": {
    "original_bands": ["B8", "B4"],
    "mapped_bands": ["NIR", "RED"]
  },

  // Parâmetros de visualização para o Google Earth Engine
  "vis_params": {
    "bands": ["NIR", "RED"],      // bandas que precisam existir na imagem
    "min": [-0.2],
    "max": [0.9],
    "palette": ["#a52a2a", "#c4813e", "#e6c26b", "#fff2a8", "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850", "#006837"],
    "index": {                    // opcional: banda derivada renderizada com a paleta
      "type": "normalized_difference",
      "bands": ["NIR", "RED"],
      "name": "NDVI"
    }
  },

  // Landsat: um bloco vis_params por coleção (substitui band_config e vis_params)
  "satellite_configs": [
    {
      "collection_id": "LANDSAT/LC08/C02/T1_L2",
      "vis_params": {
        "bands": ["SR_B5", "SR_B4"],
        "min": [-0.2],
        "max": [0.9],
        "palette": ["#a52a2a", "#c4813e", "#e6c26b", "#fff2a8", "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850", "#006837"],
        "index": {"type": "normalized_difference", "bands": ["SR_B5", "SR_B4"], "name": "NDVI"}
      }
    }
  ],

  "created_at": ISODate("2026-09-09T00:00:00Z"),
  "updated_at": ISODate("2026-09-09T00:00:00Z")
}
```

Composições de bandas (por exemplo `tvi-red`) usam apenas `bands`, `min`, `max` e `gamma`. Visualizações de índice acrescentam `index` e `palette`; `gamma` é ignorado nelas. O nome do visparam faz parte da chave de cache dos tiles, portanto um novo documento nunca colide com tiles já gerados.

````

Substituir o exemplo da seção "### Adicionando Nova Visualização" por:

````markdown
### Adicionando Nova Visualização

Pela API administrativa (`POST /api/vis-params/`, ver `VIS_PARAMS_API.md`) ou pelo script de semente, que insere apenas os documentos ausentes:

```bash
.venv/bin/python scripts/migrate_vis_params.py
```

As capabilities são recalculadas em até cinco minutos (cache) ou imediatamente após `GET /api/capabilities/admin/refresh`.

````

- [ ] **Step 4: `docs/CHANGELOG.md`**

Na seção `## [Unreleased]`, subseção `### Added`, inserir como primeiras linhas:

```markdown
- NDVI visualization for Sentinel-2 (`tvi-ndvi`) and Landsat (`landsat-tvi-ndvi`)
- Declarative single-band indexes in vis_params (`index`, `palette`) applied by `resolve_visualization`
- `legend` metadata in capabilities `visparam_details` for index visualizations
- Idempotent vis_params seeding (`upsert_missing`) with explicit `display_name`
```

- [ ] **Step 5: Conferir a renderização dos blocos de código**

Run: `cd /home/tharles/projects_lapig/tiles && grep -c '```' docs/VIS_PARAMS_API.md docs/CAPABILITIES_API.md docs/LAYERS_API_GUIDE.md`
Expected: contagem par em cada arquivo (todos os blocos fechados).

- [ ] **Step 6: Commit (com autorização do usuário)**

```bash
cd /home/tharles/projects_lapig/tiles
git add docs/VIS_PARAMS_API.md docs/CAPABILITIES_API.md docs/LAYERS_API_GUIDE.md docs/CHANGELOG.md
git commit -m "docs(tiles): documenta visparams de índice, NDVI e legenda nas capabilities"
```

---

### Task 10: Legenda no modal de comparação do TVI

**Files:**
- Modify: `/home/tharles/projects_lapig/tvi/src/client/controllers/mosaic-dialog.js:108-114` (após `getVisparamDisplayName`)
- Modify: `/home/tharles/projects_lapig/tvi/src/client/views/mosaic-dialog.tpl.html:15-25` (bloco `.layer-labels`) e `:151-166` (estilos de `.left-layer-info`/`.right-layer-info`)
- Test: `/home/tharles/projects_lapig/tvi/src/server/test/mosaicDialogLegend.test.js`

**Interfaces:**
- Consumes: `visparam_details[].legend` publicado pelo tiles (Tarefa 7), repassado sem alteração por `src/server/controllers/capabilities.js`.
- Produces: `$scope.getLegend(satellite: 'landsat' | 'sentinel') -> legend | null` e `$scope.getLegendGradient(legend) -> string` (CSS `linear-gradient`).

- [ ] **Step 1: Escrever o teste que falha**

Criar `/home/tharles/projects_lapig/tvi/src/server/test/mosaicDialogLegend.test.js`:

```javascript
/**
 * Testes da legenda de índice no modal de comparação de imagens
 * (controllers/mosaic-dialog.js e views/mosaic-dialog.tpl.html).
 *
 * Contexto (2026-09): o serviço de tiles passou a publicar visparams de
 * índice (NDVI) com um objeto `legend` (rótulo, mínimo, máximo e paleta) em
 * `visparam_details`. Uma imagem de banda única com paleta não é
 * interpretável sem a barra de cores, então o modal desenha a legenda do
 * lado cujo visparam selecionado a possui.
 *
 * Cobertura:
 *   - `getLegend` devolve a legenda do visparam selecionado de cada lado e
 *     `null` quando o visparam não tem legenda ou os detalhes não chegaram.
 *   - `getLegendGradient` monta o gradiente CSS a partir da paleta.
 *   - Guarda estática do template: bloco de legenda nos dois lados, ligado
 *     às funções acima.
 *
 * Execução:
 *   cd src/server && npm test
 *   ou
 *   node --test src/server/test/mosaicDialogLegend.test.js
 */

'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const CLIENT_DIR = path.join(__dirname, '..', '..', 'client');
const CONTROLLER_PATH = path.join(CLIENT_DIR, 'controllers', 'mosaic-dialog.js');
const TEMPLATE_PATH = path.join(CLIENT_DIR, 'views', 'mosaic-dialog.tpl.html');

const NDVI_LEGEND = {
    label: 'NDVI',
    min: -0.2,
    max: 0.9,
    palette: ['#a52a2a', '#fff2a8', '#006837']
};

function carregarController() {
    const registro = {};
    const contexto = vm.createContext({
        Application: {
            controller: function (nome, fn) { registro[nome] = fn; }
        }
    });
    vm.runInContext(fs.readFileSync(CONTROLLER_PATH, 'utf8'), contexto, { filename: CONTROLLER_PATH });
    assert.ok(registro.MosaicDialogController, 'MosaicDialogController não registrado');
    return registro.MosaicDialogController;
}

function criarTimeoutFalso() {
    const pendentes = [];
    const $timeout = function (fn) {
        const tarefa = { fn: fn, cancelada: false };
        pendentes.push(tarefa);
        return tarefa;
    };
    $timeout.cancel = function (tarefa) {
        if (tarefa) tarefa.cancelada = true;
    };
    $timeout.flush = function () {
        const lote = pendentes.splice(0, pendentes.length);
        lote.forEach(function (t) { if (!t.cancelada) t.fn(); });
    };
    return $timeout;
}

function intervalo(inicio, fim) {
    const anos = [];
    for (let a = inicio; a <= fim; a++) anos.push(a);
    return anos;
}

function capabilities() {
    return [
        {
            name: 's2_harmonized',
            satellite: 'sentinel',
            visparam: ['tvi-red', 'tvi-ndvi'],
            visparam_details: [
                { name: 'tvi-red', display_name: 'TVI Red' },
                { name: 'tvi-ndvi', display_name: 'NDVI', legend: NDVI_LEGEND }
            ],
            year: intervalo(2017, 2026)
        },
        {
            name: 'landsat',
            satellite: 'landsat',
            visparam: ['landsat-tvi-true', 'landsat-tvi-ndvi'],
            visparam_details: [
                { name: 'landsat-tvi-true', display_name: 'True Color' },
                { name: 'landsat-tvi-ndvi', display_name: 'NDVI', legend: NDVI_LEGEND }
            ],
            year: intervalo(1985, 2026)
        }
    ];
}

function instanciar(caps) {
    const controller = carregarController();
    const $timeout = criarTimeoutFalso();
    const $scope = { $on: function () {} };
    controller(
        $scope,
        { dismiss: function () {} },
        [],
        { year: 2023, bounds: null },
        { lon: -49.2, lat: -16.6 },
        { zoomLevel: 13 },
        caps === undefined ? capabilities() : caps,
        'DRY',
        { has: function () { return false; } },
        $timeout
    );
    return { $scope: $scope, $timeout: $timeout };
}

test('visparam inicial sem legenda: getLegend devolve null nos dois lados', () => {
    const { $scope } = instanciar();
    assert.equal($scope.getLegend('sentinel'), null);
    assert.equal($scope.getLegend('landsat'), null);
});

test('selecionar o NDVI de cada lado expõe a legenda correspondente', () => {
    const { $scope, $timeout } = instanciar();

    $scope.selectSentinelVisparam('tvi-ndvi');
    $scope.selectLandsatVisparam('landsat-tvi-ndvi');
    $timeout.flush();

    assert.deepEqual($scope.getLegend('sentinel'), NDVI_LEGEND);
    assert.deepEqual($scope.getLegend('landsat'), NDVI_LEGEND);
    assert.equal($scope.rightMapConfig.visparam, 'tvi-ndvi');
    assert.equal($scope.leftMapConfig.visparam, 'landsat-tvi-ndvi');
});

test('voltar a um visparam de composição remove a legenda', () => {
    const { $scope, $timeout } = instanciar();
    $scope.selectSentinelVisparam('tvi-ndvi');
    $timeout.flush();
    assert.ok($scope.getLegend('sentinel'));

    $scope.selectSentinelVisparam('tvi-red');
    $timeout.flush();
    assert.equal($scope.getLegend('sentinel'), null);
});

test('sem capabilities, getLegend devolve null sem lançar', () => {
    const { $scope } = instanciar([]);
    assert.equal($scope.getLegend('sentinel'), null);
    assert.equal($scope.getLegend('landsat'), null);
});

test('legenda com paleta de uma cor é ignorada', () => {
    const caps = capabilities();
    caps[0].visparam_details[1].legend = { label: 'NDVI', min: 0, max: 1, palette: ['#000'] };
    const { $scope, $timeout } = instanciar(caps);
    $scope.selectSentinelVisparam('tvi-ndvi');
    $timeout.flush();
    assert.equal($scope.getLegend('sentinel'), null);
});

test('getLegendGradient monta o gradiente CSS a partir da paleta', () => {
    const { $scope } = instanciar();
    assert.equal(
        $scope.getLegendGradient(NDVI_LEGEND),
        'linear-gradient(to right, #a52a2a, #fff2a8, #006837)'
    );
    assert.equal($scope.getLegendGradient(null), '');
    assert.equal($scope.getLegendGradient({ palette: ['#000'] }), '');
});

test('template: bloco de legenda nos dois lados, ligado a getLegend e ao gradiente', () => {
    const html = fs.readFileSync(TEMPLATE_PATH, 'utf8');

    assert.match(html, /class="left-layer-info"[\s\S]*?ng-if="leftMapConfig && getLegend\('landsat'\)"/);
    assert.match(html, /class="right-layer-info"[\s\S]*?ng-if="rightMapConfig && getLegend\('sentinel'\)"/);
    assert.equal((html.match(/class="layer-legend"/g) || []).length, 2);
    assert.equal((html.match(/class="legend-bar" ng-style="\{'background': getLegendGradient\(getLegend\('(landsat|sentinel)'\)\)\}"/g) || []).length, 2);
    assert.match(html, /\.legend-bar\s*\{/);
});
```

- [ ] **Step 2: Executar o teste para confirmar a falha**

Run: `cd /home/tharles/projects_lapig/tvi/src/server && node --test test/mosaicDialogLegend.test.js 2>&1 | tail -8`
Expected: FAIL com `TypeError: $scope.getLegend is not a function` e falha na guarda do template.

- [ ] **Step 3: Implementar as funções no controlador**

Em `/home/tharles/projects_lapig/tvi/src/client/controllers/mosaic-dialog.js`, após a função `$scope.getVisparamDisplayName` (termina em `return detail ? detail.display_name : visparamName;\n    };`), inserir:

```javascript
    $scope.getLegend = function(satellite) {
        var details = satellite === 'landsat' ? $scope.landsatVisparamDetails : $scope.sentinelVisparamDetails;
        var selected = satellite === 'landsat' ? $scope.selectedLandsatVisparam : $scope.selectedSentinelVisparam;
        if (!selected || !Array.isArray(details)) {
            return null;
        }
        var detail = details.find(function(vp) {
            return vp.name === selected;
        });
        var legend = detail && detail.legend;
        if (!legend || !Array.isArray(legend.palette) || legend.palette.length < 2) {
            return null;
        }
        return legend;
    };

    $scope.getLegendGradient = function(legend) {
        if (!legend || !Array.isArray(legend.palette) || legend.palette.length < 2) {
            return '';
        }
        return 'linear-gradient(to right, ' + legend.palette.join(', ') + ')';
    };
```

- [ ] **Step 4: Inserir o bloco de legenda e os estilos no template**

Em `/home/tharles/projects_lapig/tvi/src/client/views/mosaic-dialog.tpl.html`, substituir o bloco:

```html
    <!-- Labels das camadas -->
    <div class="layer-labels">
        <div class="left-layer-info">
            <i class="fa fa-arrow-left"></i> 
            <strong>Esquerda:</strong> {{leftLayerLabel}}
        </div>
        <div class="right-layer-info">
            <strong>Direita:</strong> {{rightLayerLabel}}
            <i class="fa fa-arrow-right"></i>
        </div>
    </div>
```

por:

```html
    <!-- Labels das camadas -->
    <div class="layer-labels">
        <div class="left-layer-info">
            <div class="layer-title">
                <i class="fa fa-arrow-left"></i>
                <strong>Esquerda:</strong> {{leftLayerLabel}}
            </div>
            <div class="layer-legend" ng-if="leftMapConfig && getLegend('landsat')">
                <span class="legend-label">{{getLegend('landsat').label}}</span>
                <span class="legend-value">{{getLegend('landsat').min}}</span>
                <span class="legend-bar" ng-style="{'background': getLegendGradient(getLegend('landsat'))}"></span>
                <span class="legend-value">{{getLegend('landsat').max}}</span>
            </div>
        </div>
        <div class="right-layer-info">
            <div class="layer-title">
                <strong>Direita:</strong> {{rightLayerLabel}}
                <i class="fa fa-arrow-right"></i>
            </div>
            <div class="layer-legend" ng-if="rightMapConfig && getLegend('sentinel')">
                <span class="legend-label">{{getLegend('sentinel').label}}</span>
                <span class="legend-value">{{getLegend('sentinel').min}}</span>
                <span class="legend-bar" ng-style="{'background': getLegendGradient(getLegend('sentinel'))}"></span>
                <span class="legend-value">{{getLegend('sentinel').max}}</span>
            </div>
        </div>
    </div>
```

No bloco `<style>` do mesmo arquivo, substituir:

```css
.left-layer-info, .right-layer-info {
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 14px;
    color: #495057;
}

.left-layer-info i, .right-layer-info i {
    color: #6c757d;
    font-size: 16px;
}
```

por:

```css
.left-layer-info, .right-layer-info {
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 14px;
    color: #495057;
}

.left-layer-info {
    align-items: flex-start;
}

.right-layer-info {
    align-items: flex-end;
}

.layer-title {
    display: flex;
    align-items: center;
    gap: 10px;
}

.layer-title i {
    color: #6c757d;
    font-size: 16px;
}

.layer-legend {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: #6c757d;
}

.legend-label {
    font-weight: 600;
    color: #495057;
}

.legend-bar {
    display: inline-block;
    width: 140px;
    height: 12px;
    border-radius: 3px;
    border: 1px solid #dee2e6;
}
```

- [ ] **Step 5: Executar os testes do TVI**

Run: `cd /home/tharles/projects_lapig/tvi/src/server && node --test test/mosaicDialogLegend.test.js test/mosaicDialogMonthly.test.js 2>&1 | tail -8`
Expected: `# pass 12` e `# fail 0`.

Run: `cd /home/tharles/projects_lapig/tvi/src/server && npm test 2>&1 | tail -8`
Expected: `# fail 0`.

- [ ] **Step 6: Commit (com autorização do usuário)**

```bash
cd /home/tharles/projects_lapig/tvi
git add src/client/controllers/mosaic-dialog.js src/client/views/mosaic-dialog.tpl.html src/server/test/mosaicDialogLegend.test.js
git commit -m "feat(tvi): legenda de índice no modal de comparação de imagens"
```

Observação: a correção da série mensal (`monthly.enabled`) já está no working tree do TVI com o teste `mosaicDialogMonthly.test.js`; se ainda não tiver sido commitada, commitá-la separadamente antes desta tarefa com `fix(tvi): corrige série mensal Sentinel no modal de comparação`.

---

### Task 11: Semente e verificação manual em ambiente com Earth Engine

**Files:**
- Nenhum arquivo novo. Executa o script da Tarefa 8 e valida os endpoints.

**Interfaces:**
- Consumes: tudo o que foi entregue nas Tarefas 1 a 10.
- Produces: evidência de funcionamento (códigos HTTP e capabilities) registrada na conversa.

- [ ] **Step 1: Subir o tiles localmente com credencial do Earth Engine**

O script `scripts/run_local_dev.sh` define `SKIP_GEE_INIT=true` e não serve para esta verificação. Subir os serviços auxiliares e o servidor com o Earth Engine ativo:

```bash
cd /home/tharles/projects_lapig/tiles
docker compose -f docker-compose.services.yml up -d
export TILES_ENV=development REDIS_URL=redis://localhost:6379 S3_ENDPOINT=http://localhost:9000 \
       S3_ACCESS_KEY=minioadmin S3_SECRET_KEY=minioadmin GEE_SERVICE_ACCOUNT_FILE=./.service-accounts/gee.json
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8083
```

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8083/health/light`
Expected: `200`. Se o ambiente local não tiver MongoDB, o fallback fixo já contém o NDVI e os passos seguintes continuam válidos, exceto o passo 2.

- [ ] **Step 2: Executar a semente**

Run: `cd /home/tharles/projects_lapig/tiles && .venv/bin/python scripts/migrate_vis_params.py`
Expected: linha `Visualization parameters: N inserted, M already present`, com os dois NDVI entre os inseridos na primeira execução (`2 inserted, 6 already present` numa base já semeada com os seis visparams atuais; `8 inserted, 0 already present` numa base vazia). Uma segunda execução imprime `0 inserted, 8 already present`.

- [ ] **Step 3: Confirmar as capabilities**

Run:

```bash
curl -s http://localhost:8083/api/capabilities/admin/refresh >/dev/null
curl -s http://localhost:8083/api/capabilities/ | .venv/bin/python -c "
import json, sys
caps = json.load(sys.stdin)
for c in caps['collections']:
    ndvi = [d for d in c['visparam_details'] if d.get('legend')]
    print(c['name'], c['visparam'], [(d['name'], d['legend']['min'], d['legend']['max'], len(d['legend']['palette'])) for d in ndvi])
"
```

Expected:

```
s2_harmonized [..., 'tvi-ndvi'] [('tvi-ndvi', -0.2, 0.9, 9)]
landsat [..., 'landsat-tvi-ndvi'] [('landsat-tvi-ndvi', -0.2, 0.9, 9)]
```

- [ ] **Step 4: Gerar tiles NDVI reais**

Run:

```bash
for q in \
  "s2_harmonized/2794/4592/13?period=DRY&year=2023&visparam=tvi-ndvi" \
  "s2_harmonized/2794/4592/13?period=MONTH&year=2023&month=8&visparam=tvi-ndvi" \
  "landsat/2794/4592/13?period=DRY&year=2020&visparam=landsat-tvi-ndvi" \
  "landsat/2794/4592/13?period=DRY&year=2005&visparam=landsat-tvi-ndvi"; do
  printf '%s -> ' "$q"
  curl -s -o /tmp/ndvi-tile.png -w '%{http_code} %{content_type} %{size_download}B\n' "http://localhost:8083/api/layers/$q"
done
```

Expected: quatro linhas `200 image/png` com tamanho maior que 1000 bytes. Abrir `/tmp/ndvi-tile.png` da última execução e confirmar cores da paleta (marrom a verde), sem o PNG de erro.

- [ ] **Step 5: Verificar o modal no TVI**

Com o TVI apontando para esse tiles, abrir a comparação de imagens de um ponto em ano com Sentinel (2023): botão "NDVI" nos dois grupos; ao selecioná-lo, legenda sob o rótulo do lado correspondente com `-0.2` e `0.9`; ativar "Visualização Mensal Sentinel" e mover o slider com NDVI selecionado, confirmando que a camada direita muda de mês.

- [ ] **Step 6: Registrar o resultado**

Relatar na conversa os códigos HTTP obtidos, a saída das capabilities e o comportamento observado no modal. Qualquer 4xx ou 5xx interrompe a entrega até a causa ser identificada.
