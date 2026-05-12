# Mitigação de 429 do Earth Engine em downloads de tiles

**Data:** 2026-05-12
**Serviço afetado:** `prod_tiles_tile` (FastAPI + 20 réplicas)
**Tipo:** correção de bug + reforço arquitetural
**Status:** rascunho para validação

## 1. Contexto

O serviço de tiles serve mosaicos do Google Earth Engine (Landsat, Sentinel-2, etc.) renderizando PNGs via URLs assinadas obtidas com `ee.data.getMapId`. Sob carga, uma fração significativa das respostas retorna 503 (`ee_unavailable`) e o frontend exibe placeholders aleatórios, não restritos a um período específico.

Observabilidade coletada em um container, janela de cinco minutos:

| Métrica | Valor |
|---|---|
| Requisições 200 OK | 1.468 |
| Requisições 503 | 150 (~9,2%) |
| Requisições 500 | 8 |
| 429 da EE (primeira tentativa) | 252 |
| 429 da EE (segunda tentativa) | 140 |
| TimeoutError (10 s) | 172 |

Em uma janela de trinta minutos, o pior container acumulou 28.921 erros e os demais oscilaram entre 250 e 7.343 — distribuição assimétrica entre as 20 réplicas. Os 503 distribuem-se entre os anos 1989 e 2025, sem concentração específica em 1993–2000.

## 2. Diagnóstico técnico

### 2.1. Causa raiz inicial (parcial)

O pool de Service Accounts em `app/core/gee_pool.py` distribui SAs do GEE entre workers via Redis. Ao receber 429 em `app/utils/http.py:38` (download de tile), o código apenas registra a métrica `report_http_429`, dorme com `base_delay * 2^attempt + jitter` e retenta a mesma URL. A função `WorkerGEEManager.rotate_on_429` existe, mas só é acionada via decorador `@gee_retry` em chamadas síncronas da REST API do Earth Engine — **não** em downloads HTTP de tiles.

### 2.2. Causa raiz adicional descoberta na análise

A `getMapId` do EE devolve uma URL no formato:

```
https://earthengine.googleapis.com/v1/projects/earthengine-legacy/maps/{mapID}-{token}/tiles/{z}/{x}/{y}
```

O token embutido está vinculado à SA que assinou a chamada. Em `app/api/layers.py:537`, essa URL é cacheada em Redis (`set_meta(path_cache, {"url": layer_url, "date": ...})`) e reutilizada por todos os workers que servirem tiles do mesmo bbox + camada + período até o TTL expirar (`settings.LIFESPAN_URL`).

Quando a SA satura:

1. A URL em cache continua apontando para o token saturado.
2. Qualquer worker que ler aquela URL fica acoplado à SA penalizada.
3. Rotacionar a SA do worker **sem invalidar a URL cacheada** não muda o destino do retry.
4. A rotação apenas isolada (proposta original do diagnóstico) é teatral: o retry em vôo continua falhando.

### 2.3. Causa raiz operacional

O pool atualmente expõe apenas duas SAs descobertas (`ee-lapig-tvi5`, `ee-lapig-tvi7`) servindo 20 réplicas. Mesmo com rotação perfeita, duas SAs saturando produzem oscilação A → B → A → B sob carga real. O fix de código mitiga picos transitórios; o teto sustentável é capacidade de cota — exige expansão de SAs no pool.

### 2.4. Contribuição do timeout

`ClientTimeout(total=10.0)` esgota o budget quando o EE responde em 8–12 s sob carga. Os 172 TimeoutErrors observados em cinco minutos refletem esse aperto. O retry com backoff dorme entre 1 e 4 s entre tentativas, comendo ainda mais do orçamento.

## 3. Objetivos e não-objetivos

### Objetivos

- Eliminar a parcela dos 503 cuja causa é rotação ausente em 429 de download de tile.
- Reduzir TimeoutErrors em condição de EE lento mas saudável.
- Tornar o cache de URL responsivo a 429 (invalidar e regenerar com SA fresca).
- Adicionar observabilidade granular por SA para diagnóstico futuro.

### Não-objetivos

- Não alterar o circuit breaker recém-introduzido (PR #5).
- Não introduzir CDN ou cache de borda nesta iteração.
- Não modificar o caminho da REST API (`app/utils/ee_compute.py`) — já contém rotação reativa em 429 desde a iteração anterior.
- Não tocar no frontend `tiles-client`.
- Não otimizar custo/quota da conta GCP — escopo operacional, fora deste design.

## 4. Estratégia técnica

A solução tem cinco componentes, ordenados por dependência.

### C1 — Exceção tipada em `http_get_bytes`

Substituir `HTTPException(503, …)` lançada na rota de 429 por uma exceção dedicada `EarthEngineRateLimitedError(message, sa_name)`. O caller passa a poder reagir especificamente a 429 versus erros HTTP genéricos.

`http_get_bytes` permanece agnóstico de GEE — não chama `mgr.rotate_on_429`, não toca em Redis de cache de URL. A única dependência GEE que mantém é o registro de métrica via `mgr.report_http_429()`, que é fire-and-forget.

Justificativa da separação: `http_get_bytes` é usada por três módulos distintos (`layers.py`, `imagery.py`, `embedding_maps/router.py`). Cada um tem sua estratégia de geração de URL. Acoplar `http_get_bytes` ao domínio GEE quebraria a generalidade.

### C2 — Helper `fetch_tile_with_rotation` em `app/utils/ee_tile_fetch.py`

Novo módulo. Função:

```python
async def fetch_tile_with_rotation(
    cache_key: str,
    url_factory: Callable[[], Awaitable[str]],
    x: int, y: int, z: int,
) -> bytes:
    """
    1. Lê URL do meta cache (chave cache_key).
    2. Tenta http_get_bytes(url.format(x, y, z)).
    3. Em EarthEngineRateLimitedError:
       a. mgr.rotate_on_429() — abandona SA atual, adquire nova.
       b. Invalida meta cache (delete_meta(cache_key)).
       c. Chama url_factory() para regenerar URL com nova SA.
       d. Persiste nova URL em meta cache.
       e. Retenta http_get_bytes uma única vez.
    4. Segundo 429 → propaga EarthEngineRateLimitedError, caller converte em 503.
    """
```

Limite rígido de uma regeneração por request. `getMapId` custa de 200 a 500 ms por round-trip; mais que isso transforma 429 em latência cumulativa pior que o próprio erro.

### C3 — Refator dos três call sites

`app/api/layers.py:576`, `app/api/imagery.py:464`, `app/modules/embedding_maps/router.py:330` passam a usar `fetch_tile_with_rotation`. Cada um fornece o seu `url_factory` (encapsulando `_create_landsat_layer_with_params`, `_create_s2_layer_sync`, etc.).

Sem regressão de comportamento: o caminho feliz continua idêntico (lê meta cache, baixa via `http_get_bytes`). Só o caminho de erro 429 ganha o ciclo de rotação + invalidação + regeneração.

### C4 — Aumento de timeout

`ClientTimeout(total=10.0)` passa para `ClientTimeout(total=20.0)`. Configurável via `settings.HTTP_GET_BYTES_TIMEOUT` (padrão 20.0). Justificativa: dar margem para a EE responder sob carga sem consumir todo o budget no primeiro retry. Risco controlado: o circuit breaker continua disparando quando os erros se acumulam, evitando worker starvation.

### C5 — Observabilidade granular

Já existe endpoint `/metrics` Prometheus (PR #6). Adicionar:

- Em `app/core/gee_pool.py`:
  - `gee_sa_http_429_total{sa_name}` — counter incrementado em `report_http_429`.
  - `gee_sa_rotation_total{from_sa, to_sa, trigger}` — counter em `rotate_on_429` (`trigger=http_429|rest_api_429`).
  - `gee_sa_in_cooldown{sa_name}` — gauge atualizado em `report_http_429` e `report_429`.
- Em `app/utils/ee_tile_fetch.py`:
  - `gee_tile_url_regen_total{layer}` — counter incrementado a cada regeneração de URL disparada por 429.

Log estruturado em `fetch_tile_with_rotation`: campo `event=sa_rotated_http_429` com `sa_from`, `sa_to`, `request_id` (já propagado via `RequestIdMiddleware` da PR #7).

### C6 — Documentação operacional (fora do código)

No `docs/deploy-runbook.md`, adicionar seção curta sobre expansão do pool de SAs:

- Sintoma: oscilação visível em `gee_sa_rotation_total` entre poucas SAs.
- Ação: provisionar SAs adicionais no GCP, depositar JSON em `.service-accounts/`, chamar `POST /admin/gee-pool/refresh` (já implementado em `refresh_registry`).

## 5. Estrutura de arquivos

```
app/
├── utils/
│   ├── http.py                  # editar: timeout, exceção tipada
│   └── ee_tile_fetch.py         # NOVO: helper fetch_tile_with_rotation
├── core/
│   └── gee_pool.py              # editar: counters Prometheus por SA
├── api/
│   ├── layers.py                # editar: usar fetch_tile_with_rotation
│   └── imagery.py               # editar: usar fetch_tile_with_rotation
└── modules/
    └── embedding_maps/
        └── router.py            # editar: usar fetch_tile_with_rotation
tests/
├── utils/
│   ├── test_http.py             # editar: testar EarthEngineRateLimitedError
│   └── test_ee_tile_fetch.py    # NOVO
└── core/
    └── test_gee_pool.py         # editar: counters
docs/
└── deploy-runbook.md            # editar: seção expansão de SAs
```

## 6. Estratégia de testes

### Unitário

- `tests/utils/test_http.py`: mockar `aiohttp` retornando 429 → confirmar que `EarthEngineRateLimitedError` é lançado com `sa_name` correto.
- `tests/utils/test_ee_tile_fetch.py`:
  - 429 na primeira tentativa, sucesso após rotação → confirmar 1× call de `rotate_on_429`, 1× `delete_meta`, 1× `url_factory`, 1× retry de `http_get_bytes` retornando 200.
  - 429 em ambas as tentativas → confirmar propagação de erro, sem terceira tentativa.
  - 200 no primeiro shot → caminho feliz, sem invocar rotação ou regeneração.
  - `url_factory` levanta exceção → confirmar que a falha é propagada limpa, sem deixar cache em estado inconsistente.
- `tests/core/test_gee_pool.py`: incrementos de counter Prometheus após `report_http_429` e `rotate_on_429`.

### Integração

- Suite em `tests/integration/test_tile_rotation.py` com Redis e mock EE local (aiohttp test server) que alterna 200 / 429 por header `Authorization`. Confirmar que após 429, a próxima request usa um token diferente e devolve 200.

### Carga (manual, pós-deploy canário)

- Script `scripts/load_test_tiles.py` que dispara 1.000 reqs/min contra Landsat 1993–2025 random. Métricas alvo:
  - Taxa de 503 < 1%.
  - p95 de latência < 8 s.
  - `gee_sa_rotation_total` cresce em picos, depois estabiliza.

## 7. Riscos e mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Regeneração de URL aumenta latência média | Média | Médio | Limitar a 1 regeneração por request. Métrica `gee_tile_url_regen_total` monitora a taxa. |
| Thundering herd em regeneração simultânea | Baixa | Médio | `layers.py:520` já usa `tile_lock(f"url:{path_cache}")` ao redor da geração inicial; `embedding_maps/router.py:261` tem padrão equivalente. Aplicar o mesmo lock dentro de `fetch_tile_with_rotation` ao redor da regeneração; `imagery.py` ganha o lock no `url_factory` ao adotar o helper. |
| Worker rotaciona, mas cooldown da SA antiga ainda permite reaquisição por outro worker | Média | Baixo | `report_http_429` já aplica cooldown de 15 s na SA. O outro worker é bloqueado de adquiri-la durante esse período. |
| Capacidade insuficiente persistente (somente 2 SAs) | Alta | Alto | Documentação operacional explícita (C6); métricas tornam o sintoma visível antes do incidente. |
| Aumento de timeout deixa workers presos em requests lentas | Baixa | Médio | Circuit breaker da PR #5 corta upstream quando taxa de erro sobe; o número de workers (`gunicorn_conf.py`) tem reserva para absorver requests com timeout mais longo. |

## 8. Plano de rollout

1. **Implementação** em branch dedicada com testes verdes localmente.
2. **Build** da imagem `tiles:ee-429-rotation`.
3. **Deploy canário** em uma réplica (`docker service update --image ... --replicas 1` ou similar do runbook).
4. **Monitoramento** por 30 min: taxa de 503, `gee_sa_rotation_total`, p95 de latência.
5. **Rollout completo** se métricas estáveis; **rollback** via `docker service rollback` se taxa de 503 não baixar ou se p95 piorar.
6. **Pós-deploy**: avaliar necessidade de expandir o pool de SAs com base em `gee_sa_rotation_total` e quotas GCP.

## 9. Critérios de aceite

- Taxa de 503 em produção cai abaixo de 2% sob carga comparável (de ~9% observados).
- Logs evidenciam rotação de SA disparada por 429 de download HTTP (campo `event=sa_rotated_http_429`).
- Métrica `gee_tile_url_regen_total` é não-nula sob carga, comprovando que o cache está sendo invalidado e regenerado.
- Nenhuma regressão em testes existentes (`pytest tests/`).
- Sem aumento mensurável de p95 de latência no caminho feliz.

## 10. Trabalho derivado (fora do escopo desta entrega)

- Expandir pool de SAs no GCP (tarefa operacional).
- Avaliar adoção de CDN para PNGs com `Cache-Control` longo.
- Reduzir o `LIFESPAN_URL` se o EE começar a invalidar tokens proativamente.
