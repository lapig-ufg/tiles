# Visualização NDVI como visparam de índice declarativo

**Data:** 2026-09-09
**Serviços afetados:** `tiles` (FastAPI, Celery de pré-aquecimento, MongoDB `vis_params`) e `tvi` (modal de comparação de imagens)
**Tipo:** nova funcionalidade com extensão de modelo de dados
**Status:** rascunho para validação

## 1. Contexto

O modal "Comparação de Imagens" do TVI (`src/client/views/mosaic-dialog.tpl.html`) exibe lado a lado uma camada Landsat e uma camada Sentinel-2 do mesmo ano, com botões de visualização por satélite. Os botões são construídos a partir das capabilities do serviço de tiles, e não da configuração da campanha: tudo o que o tiles publica como visparam ativo aparece no modal para todos os períodos (WET, DRY e a série mensal).

Os intérpretes precisam de uma visualização NDVI (índice de vegetação por diferença normalizada) nos dois grupos, Sentinel-2 e Landsat, como mais um botão ao lado de "Tvi Green", "Tvi Red", "Tvi Rgb" e dos equivalentes Landsat.

## 2. Situação atual

### 2.1. Fluxo dos visparams

1. Os visparams são documentos da coleção `vis_params` do MongoDB, carregados por `app/visualization/vis_params_db.py` e convertidos para o formato consumido pelos construtores por `get_visparams_dict` e `get_landsat_vis_params`.
2. `CapabilitiesProvider` (`app/utils/capabilities.py`) monta `/api/capabilities` a partir dos documentos ativos, agrupados por categoria (`sentinel2` ou `landsat`), com `visparam` (nomes) e `visparam_details` (nome, rótulo, descrição, tags).
3. O servidor do TVI repassa `collections` sem transformação; o modal lê `visparam` e `visparam_details` de cada coleção.
4. Cada tile é gerado por um dos construtores: `_create_s2_layer_sync`, `_create_landsat_layer_sync` e `_create_landsat_layer_with_params` em `app/api/layers.py`; `_warm_create_s2_url` e `_warm_create_landsat_url` em `app/tasks/cache_operations.py` (réplicas usadas pelo pré-aquecimento, inclusive com credencial da campanha via `app/utils/ee_maps.py`).

### 2.2. Lacunas

- O modelo `VisParam` (`app/models/vis_params.py`) descreve apenas composições de bandas: `bands`, `min`, `max` e `gamma` obrigatórios. Não há paleta nem noção de banda derivada.
- Os construtores selecionam bandas, compõem a cena e chamam `getMapId` (ou `create_map_with_credential`). Nenhum calcula índices.
- A validação das requisições Landsat (`app/visualization/validation.py`) deriva a lista de visparams aceitos do dicionário fixo `app/visualization/visParam.py`, materializada no import. Um visparam Landsat cadastrado apenas no MongoDB é rejeitado com 422 (`invalid_visparam`) antes de chegar ao Earth Engine.
- O script `scripts/migrate_vis_params.py` usa `insert_many` e deriva `display_name` do nome em formato título ("Tvi Green"), sem permitir rótulo explícito.
- Uma visualização de banda única com paleta não é interpretável sem legenda; o modal do TVI não tem esse elemento.

## 3. Objetivos e não-objetivos

### Objetivos

- Disponibilizar `tvi-ndvi` (Sentinel-2) e `landsat-tvi-ndvi` (Landsat) como visparams ativos, exibidos como "NDVI", em WET, DRY e MONTH.
- Representar índices de forma declarativa no modelo, sem lógica por nome de visparam, de modo que NDWI, NBR e similares sejam apenas novos documentos.
- Aplicar o cálculo do índice em um único ponto reutilizado pelos quatro construtores.
- Publicar nas capabilities os dados necessários para o TVI desenhar a legenda.
- Exibir a legenda no modal de comparação do TVI para o lado cujo visparam selecionado tiver índice.
- Manter compatibilidade com os documentos existentes e com o fallback sem MongoDB.

### Não-objetivos

- Não alterar a composição de cena: o NDVI usa a mesma imagem composta dos demais visparams (Sentinel-2: mosaico ordenado por `CLOUDY_PIXEL_PERCENTAGE`; Landsat: `BEST_IMAGE` ou `MOSAIC`). Composto de máximo NDVI fica como evolução futura.
- Não aceitar expressões livres do Earth Engine vindas do banco.
- Não alterar o mapa principal da tela temporal, o formulário de campanha nem o `tiles-client`.
- Não adicionar o NDVI ao pré-aquecimento padrão, que segue com `tvi-red` e `landsat-tvi-false`.
- Não introduzir mascaramento de nuvens além do já existente em cada construtor.

## 4. Decisões

| Decisão | Escolha | Motivo |
|---|---|---|
| Representação do índice | Campo `index` declarativo no `VisParam` | Validável pelo Pydantic, sem superfície de injeção, extensível por dados |
| Significado de `bands` | Continua sendo "bandas que precisam existir na imagem" | Preserva intactas as verificações de disponibilidade de banda e a imagem vazia dos construtores Landsat |
| Gamma com paleta | `gamma` opcional; ignorado quando há `index` | O Earth Engine não combina correção gamma com paleta em banda única |
| Faixa e paleta | Mínimo -0,2, máximo 0,9, nove cores de marrom a verde | Faixa usual de NDVI sobre superfície terrestre; valores são dados e podem ser ajustados sem código |
| Fonte dupla | NDVI no dicionário fixo e no MongoDB | O dicionário fixo alimenta o fallback e a lista de aceitação Landsat |
| Semente | `update_one` com `$setOnInsert` e `upsert=True` | Insere apenas o que falta; não sobrescreve ajustes feitos em produção pela API |
| Legenda | Objeto `legend` em `visparam_details`; desenho apenas no modal do TVI | Menor alteração no TVI; o servidor do TVI já repassa `visparam_details` inteiro |

## 5. Projeto

### 5.1. Modelo (`app/models/vis_params.py`)

Novo submodelo:

```python
class IndexConfig(BaseModel):
    type: Literal["normalized_difference"]
    bands: List[str]          # exatamente duas: [numerador positivo, negativo]
    name: str = "NDVI"        # nome da banda derivada
```

Alterações em `VisParam`:

- `gamma`: passa a `Optional[...] = None`, mantendo o validador de conversão de string.
- `palette: Optional[List[str]] = None`.
- `index: Optional[IndexConfig] = None`.

Validadores (`model_validator(mode="after")`):

- `index.bands` deve ter tamanho 2 e estar contido em `bands`.
- `palette` só é aceita quando `index` está presente.
- Quando `index` está presente, `min` e `max` devem ter exatamente um valor cada (banda única).

`VisParamCreateRequest` e `VisParamUpdateRequest` (`app/api/vis_params.py`) herdam as alterações automaticamente por usarem `VisParam`.

### 5.2. Auxiliar de índice (`app/visualization/indices.py`, novo)

```python
def resolve_visualization(image, vis: dict) -> tuple[Any, dict]:
```

Comportamento:

- Sem `index`: devolve `(image, vis_limpo)`, em que `vis_limpo` é `vis` sem as chaves `index`, `palette` e sem valores `None`. Os construtores atuais permanecem equivalentes ao comportamento de hoje.
- Com `index`: envolve `image` em `ee.Image` (os construtores Landsat produzem `ee.Algorithms.If`, que não expõe métodos de imagem), calcula `normalizedDifference(index.bands)` e renomeia para `index.name`. Devolve o dicionário final para o Earth Engine: `bands=[index.name]`, `min`, `max` e `palette` (quando presente). `gamma` é omitido.
- O tipo de índice desconhecido levanta `ValueError`; o modelo já impede que isso chegue ao banco.

O auxiliar é puro em relação ao Earth Engine (apenas encadeia chamadas na imagem), o que permite testá-lo com `MagicMock`.

### 5.3. Conversão dos documentos (`app/visualization/vis_params_db.py`)

- `get_visparams_dict` e `get_landsat_vis_params` passam a usar `model_dump(exclude_none=True)`. A conversão de `min`, `max` e `gamma` para string continua, agora apenas para chaves presentes. `index` é serializado como dicionário e `palette` como lista de strings.
- `get_vis_param_details` (`app/utils/capabilities.py`) não muda.

### 5.4. Construtores

Cada construtor chama `resolve_visualization` imediatamente antes de registrar o mapa:

| Construtor | Antes | Depois |
|---|---|---|
| `_create_s2_layer_sync` | `getMapId({"image": best, **vis["visparam"]})` | `image, vis_ee = resolve_visualization(best, vis["visparam"])` e `getMapId({"image": image, **vis_ee})` |
| `_create_landsat_layer_sync` | `getMapId({"image": landsat, **vis})` | idem, com `vis` |
| `_create_landsat_layer_with_params` | `getMapId({"image": landsat, **vis})` | idem, com `vis` |
| `_warm_create_s2_url` | `getMapId` ou `create_map_with_credential(best, vis["visparam"], ...)` | idem, nos dois ramos |
| `_warm_create_landsat_url` | `getMapId` ou `create_map_with_credential(ee.Image(image), vis, ...)` | idem, nos dois ramos |

A conversão de listas em strings feita em `_create_landsat_layer_sync` e `_warm_create_landsat_url` (`min`, `max`, `gamma`) permanece antes da chamada ao auxiliar; o auxiliar não altera esses valores. `_retry_with_mosaic_if_band_missing` continua recebendo `vis["bands"]` (bandas de entrada), que é o que a mensagem de erro do Earth Engine cita.

As chaves de cache (`{layer}_{period}_{year}_{month}_{visparam}[_{composite}]/{geohash}`) já incluem o nome do visparam; não há colisão nem necessidade de expurgo.

### 5.5. Dicionário fixo (`app/visualization/visParam.py`)

Novas entradas, com paleta compartilhada em constante do módulo:

```python
NDVI_PALETTE = ["#a52a2a", "#c4813e", "#e6c26b", "#fff2a8", "#d9ef8b",
                "#a6d96a", "#66bd63", "#1a9850", "#006837"]

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
"landsat-tvi-ndvi": {
    "display_name": "NDVI",
    "visparam": {
        "LANDSAT/LT05/C02/T1_L2": {"bands": ["SR_B4", "SR_B3"], "min": [-0.2], "max": [0.9], "palette": NDVI_PALETTE, "index": {"type": "normalized_difference", "bands": ["SR_B4", "SR_B3"], "name": "NDVI"}},
        "LANDSAT/LE07/C02/T1_L2": {... "bands": ["SR_B4", "SR_B3"] ...},
        "LANDSAT/LC08/C02/T1_L2": {... "bands": ["SR_B5", "SR_B4"] ...},
        "LANDSAT/LC09/C02/T1_L2": {... "bands": ["SR_B5", "SR_B4"] ...},
    },
},
```

A chave `display_name` é ignorada pelos consumidores atuais (`vis["select"]`, `vis["visparam"]`) e lida apenas pelo script de semente. `KNOWN_LANDSAT_VISPARAMS` em `validation.py` passa a incluir `landsat-tvi-ndvi` sem alteração de código.

### 5.6. Capabilities (`app/utils/capabilities.py`)

Ao montar `param_info` em `get_capabilities`, incluir `legend` quando o documento tiver `index`:

- Sentinel-2: a partir de `vis_params`.
- Landsat: a partir do primeiro item de `satellite_configs` (as faixas são idênticas entre coleções para o NDVI).

```json
{
  "name": "tvi-ndvi",
  "display_name": "NDVI",
  "description": "...",
  "tags": ["sentinel2", "index", "ndvi"],
  "legend": {"label": "NDVI", "min": -0.2, "max": 0.9, "palette": ["#a52a2a", "...", "#006837"]}
}
```

Documentos sem `index` não recebem a chave `legend`. O fallback `_get_hardcoded_capabilities` lista os dois NDVI com a mesma `legend`.

### 5.7. Semente (`scripts/migrate_vis_params.py`)

- Substituir `insert_many` por um laço de `update_one({"_id": doc["_id"]}, {"$setOnInsert": doc}, upsert=True)`, registrando quantos foram inseridos e quantos já existiam.
- Usar `config.get("display_name")` quando presente; caso contrário, manter a derivação atual.
- Propagar `palette` e `index` por meio de `VisParam(**...)`, sem tratamento especial.
- Os documentos `landsat_collections` e `sentinel_collections` continuam com `replace_one(upsert=True)`, como hoje.

Documento resultante para o Sentinel-2:

```json
{
  "_id": "tvi-ndvi",
  "name": "tvi-ndvi",
  "display_name": "NDVI",
  "description": "Sentinel-2 visualization parameters for NDVI",
  "category": "sentinel2",
  "band_config": {"original_bands": ["B8", "B4"], "mapped_bands": ["NIR", "RED"]},
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

O documento Landsat segue o mesmo padrão em `satellite_configs`, um item por coleção.

### 5.8. Legenda no TVI

Servidor: sem alteração (`src/server/controllers/capabilities.js` devolve `collections` inteiro).

`src/client/controllers/mosaic-dialog.js`:

- `getLegend(satellite)`: localiza em `landsatVisparamDetails` ou `sentinelVisparamDetails` o item cujo `name` é o visparam selecionado do lado correspondente e devolve `legend` ou `null`.
- `getLegendGradient(legend)`: devolve a string `linear-gradient(to right, c1, c2, ...)` a partir de `legend.palette`, para uso em `ng-style`.

`src/client/views/mosaic-dialog.tpl.html`: dentro de `.left-layer-info` e `.right-layer-info`, um bloco `.legend` com `ng-if` sobre a legenda do lado, contendo o rótulo, uma barra de 12 px de altura com o gradiente e os valores mínimo e máximo nas extremidades. Estilos no bloco `<style>` já existente no template, alinhados à tipografia atual. Os rótulos de camada passam a empilhar verticalmente com a legenda (`flex-direction: column`) apenas dentro de cada lado, sem alterar a distribuição esquerda e direita.

## 6. Tratamento de erros

- Documento inválido enviado à API de visparams: erro 422 do Pydantic (bandas do índice fora de `bands`, paleta sem índice, faixa com mais de um valor).
- Documento inválido já presente no banco: ignorado no carregamento com `logger.warning`, comportamento atual de `VisParamsManager.initialize`.
- Coleção vazia no período: os construtores Landsat produzem a imagem vazia com as bandas de entrada; a diferença normalizada de uma imagem totalmente mascarada permanece mascarada e o tile sai transparente, como hoje.
- Falhas do Earth Engine: sem mudança (retentativa, circuit breaker, PNG de erro).
- TVI sem `legend` nos detalhes (tiles antigo): o bloco de legenda simplesmente não é exibido.

## 7. Testes

### tiles (pytest, stubs de `tests/conftest.py`)

- `tests/unit/test_vis_params_model.py`: `VisParam` aceita documentos atuais sem `palette` e `index`; rejeita `index.bands` fora de `bands`, `palette` sem `index`, `index` com faixa multivalor; `gamma` ausente é válido.
- `tests/unit/test_indices.py`: `resolve_visualization` sem índice devolve a mesma imagem e remove chaves nulas; com índice chama `normalizedDifference` com as bandas declaradas, renomeia para `index.name` e devolve `bands=[name]`, `min`, `max`, `palette`, sem `gamma`.
- `tests/unit/test_vis_params_dict.py`: `get_visparams_dict` e `get_landsat_vis_params` não emitem `gamma` como string "None" e propagam `index` e `palette`.
- `tests/unit/test_capabilities_legend.py`: `legend` presente apenas para documentos com `index`, nas duas categorias e no fallback fixo.
- `tests/unit/test_validate_landsat_request.py`: `landsat-tvi-ndvi` aceito.
- `tests/unit/test_migrate_vis_params.py`: com coleção simulada, a segunda execução não altera documentos existentes e `display_name` explícito prevalece.
- Suítes existentes (`test_landsat_band_fallback.py`, `test_tile_handlers_propagate_status.py`, `test_warm_tvi_credential.py`) continuam verdes.

### TVI (node:test, `src/server/test`)

- `mosaicDialogLegend.test.js`: controlador executado em `vm` como em `mosaicDialogMonthly.test.js`; `getLegend` devolve a legenda do visparam selecionado e `null` quando não há; `getLegendGradient` monta o gradiente; guarda estática do template exige o bloco de legenda em ambos os lados.

## 8. Implantação e verificação

1. tiles: implantar o código; executar `scripts/migrate_vis_params.py` no ambiente (insere apenas os dois documentos novos); aguardar até cinco minutos pelo cache de capabilities ou acionar `clear_cache`.
2. Verificação manual em ambiente com credencial do Earth Engine: um tile `s2_harmonized` com `visparam=tvi-ndvi` em DRY e em MONTH; um tile `landsat` com `visparam=landsat-tvi-ndvi` em ano OLI (2020) e em ano TM (2005); confirmar resposta 200 com PNG colorido pela paleta.
3. TVI: implantar a legenda. Sem esta etapa o botão NDVI já aparece no modal, apenas sem legenda.
4. Verificação no modal: botão "NDVI" nos dois grupos; legenda sob o rótulo do lado selecionado; série mensal com NDVI.

## 9. Riscos e evoluções futuras

- **Nuvens:** sem mascaramento adicional, nuvens aparecem com NDVI baixo (tons de marrom). Evolução: composto de máximo NDVI (`qualityMosaic`) como novo `type` de índice ou modo de composição.
- **Pré-aquecimento com coleção vazia:** `_build_landsat_image` em `cache_operations.py` devolve uma imagem com banda `empty` quando não há cenas; o cálculo do índice falha nesse caso do mesmo modo que a seleção de bandas RGB já falha hoje. Fora do escopo; registrar como dívida conhecida.
- **Cache de capabilities no tiles:** cinco minutos de atraso até o botão aparecer após a semente.
- **Outros índices:** NDWI (`GREEN`, `NIR`) e NBR (`NIR`, `SWIR2`) exigem apenas novos documentos e entradas no dicionário fixo.
- **Legenda no mapa principal:** o `visparam-selector` da tela temporal poderá reutilizar `legend` no futuro; fora do escopo desta entrega.
