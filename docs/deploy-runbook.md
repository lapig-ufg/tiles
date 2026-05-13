# Runbook de deploy — PR de correção de cache poisoning e SR_B4

## Contexto

Este conjunto de PRs altera o comportamento de erro do endpoint de tile:

| Antes | Depois |
|---|---|
| Erro EE → HTTP **200** + PNG placeholder "ERRO" | Erro EE → HTTP **4xx/5xx** + `Cache-Control: no-store` |
| Coleção vazia (area sem imagens) → HTTP 500 `SR_B4 not found` | Coleção vazia → HTTP **200** + tile transparente |

## Impacto esperado no momento do deploy

**Taxa de erro HTTP (2xx vs 4xx/5xx) vai mudar abruptamente.** O cenário mais provável:

- **Baseline atual** (antes do deploy): ~100 % 2xx no access log. Mascara ~1.600 erros/h reais.
- **Imediatamente após deploy**: taxa de 2xx cai para ~95–98 %. Erros reais aparecem como 500/503/429 no log.
- **Após alguns minutos** (conforme o fix `_empty_image_with_bands` toma efeito): taxa de 500 `SR_B4` cai drasticamente (de ~3.300/h para próximo a 0) porque tiles sem cobertura passam a ser 200 transparente.

**Expectativa final**: erro HTTP estabiliza em < 1 % (principalmente 429 sob pico de rate-limit EE).

## Alertas que podem disparar

Se houver alerta do tipo `rate(tiles_total{status_class!="2xx"}[5m]) > 0.05 * rate(tiles_total[5m])`, ele **disparará durante a janela de 5–15 min após o deploy** antes de estabilizar.

**Mitigação recomendada** — uma das opções:

1. **Silenciar o alerta por 30 minutos antes do deploy.**
2. **Deploy fora do horário de pico** (menos tráfego = menos erros em absoluto).
3. **Deploy em canário** (1 das 20 réplicas primeiro; avaliar métricas por 10 min antes de rolar para as outras).

Recomendação: opção 3.

## Verificação pós-deploy

### 1. Status HTTP passou a refletir realidade

```bash
# Deve aparecer mix de 200/429/500 (antes era quase 100% 200)
ssh prod-lapig 'docker service logs --since 5m prod_tiles_tile 2>&1 \
  | grep "api/layers/landsat" | grep -oE "HTTP/1\.1\" [0-9]+" \
  | sort | uniq -c | sort -rn'
```

Esperado: 95%+ 2xx, o resto distribuído em 429 (rate-limit EE, aceitável) e 500/503 (erros genuínos, investigar).

### 2. `SR_B4 not found` caiu para perto de zero

```bash
ssh prod-lapig 'docker service logs --since 30m prod_tiles_tile 2>&1 \
  | grep -c "no band named"'
```

Baseline pré-deploy: ~1.600/30min. Esperado pós-deploy: ≤ 50/30 min (erros residuais de cenas específicas com bandas faltantes — tratados pelo fallback MOSAIC do PR #3).

### 3. Cache poisoning cessou

```bash
# Contagem de tile metadados com size < 1024 B criadas após o deploy
ssh prod-lapig 'docker exec $(docker ps -qf name=valkey | head -1) \
  redis-cli --scan --pattern "tile:*" | head -1000 \
  | while read k; do \
      size=$(docker exec $(docker ps -qf name=valkey | head -1) redis-cli HGET "$k" size); \
      created=$(docker exec $(docker ps -qf name=valkey | head -1) redis-cli HGET "$k" created); \
      [ -n "$size" ] && [ "$size" -lt 1024 ] && echo "$k size=$size created=$created"; \
    done | head -20'
```

Esperado: nenhuma entrada nova com `created` após o timestamp do deploy.

### 4. Purge do estoque de placeholders envenenados

Depois do deploy estabilizar, rodar:

```bash
# Dry-run primeiro
python scripts/purge_poisoned_tiles.py --dry-run --limit 10000

# Se contagem bate com o esperado, aplicar
python scripts/purge_poisoned_tiles.py --apply
```

## Circuit breaker — ativação

O breaker está presente no código mas **desligado por default** (feature flag
`CIRCUIT_BREAKER_ENABLED=false`). Ativar apenas após:

1. PR #6 (métricas) em produção com baseline de 24h.
2. Dashboard mostrando taxa de erro HTTP estável (< 1%).
3. Alerta configurado para `rate(tile_requests_total{error_reason="ee_unavailable"}[5m])` disparar junto com circuit open (detecta se o breaker está fechando demais).

Setar no env do serviço (`docker service update --env-add`):

```bash
CIRCUIT_BREAKER_ENABLED=true
CIRCUIT_BREAKER_THRESHOLD=20              # falhas em 10s para abrir
CIRCUIT_BREAKER_WINDOW_SECONDS=10
CIRCUIT_BREAKER_COOLDOWN_SECONDS=30       # tempo aberto antes de tentar fechar
```

Ajustar `THRESHOLD` ao baseline observado: se p95 de erro/s estiver em 5, um
`THRESHOLD=20` dispara só em incidentes reais.

### Verificar estado

```bash
# Chave Redis/Valkey
ssh prod-lapig 'docker exec $(docker ps -qf name=valkey | head -1) redis-cli GET cb:ee:open_until'
# se retornar timestamp > now, circuito está aberto
```

### Forçar abertura manual (emergência — parar chamadas ao EE)

```bash
ssh prod-lapig 'docker exec $(docker ps -qf name=valkey | head -1) \
  redis-cli SET cb:ee:open_until $(($(date +%s) + 300)) EX 300'
# abre por 5 minutos
```

### Forçar fechamento

```bash
ssh prod-lapig 'docker exec $(docker ps -qf name=valkey | head -1) \
  redis-cli DEL cb:ee:open_until'
```

## Purge continuado via Celery beat (recomendado)

Execução manual ampla do `purge_poisoned_tiles.py` causou contenção no
Valkey (latência 2× em batches sequenciais, taxa de 5xx subiu de 2,7 % para
10 % durante a operação). A limpeza total deve ser feita em janelas de
baixa carga.

**Recomendação:** Celery beat diário às 03:00 BRT com batch pequeno e
scan não-agressivo:

```python
# app/tasks/tile.py
@celery_app.task(name="tiles.purge_poisoned_nightly")
def purge_poisoned_nightly():
    """Remove 50k tiles envenenados por execução (≤ 30 min de Valkey pressure)."""
    import subprocess
    subprocess.run([
        "python", "/app/scripts/purge_poisoned_tiles.py",
        "--apply",
        "--limit", "50000",
        "--scan-count", "500",   # menos agressivo que o manual (5000)
    ], check=False)

# celery beat schedule (settings.toml ou celery config)
# purge-poisoned-nightly:
#   task: tiles.purge_poisoned_nightly
#   schedule: crontab(hour=3, minute=0)
```

**Expectativa:** ~100k envenenadas removidas a cada 2 dias (beat + cache
expirando naturalmente em 30d). Estoque de ~700k deve zerar em ≤ 14 dias.

**Monitorar:** adicionar painel Grafana com `DBSIZE` e `used_memory_human`
do Valkey; alerta se `DBSIZE` não decrescer ~50k/dia.

**Desativar após limpeza**: quando métrica `tiles_poisoned_count` cair para
< 1000, remover o beat (sem mais utilidade — fixes do deploy impedem
novos placeholders).

## Rollback

Em caso de regressão grave (quebra de UX, explosão de erro sustentada):

```bash
# Rollback a imagem anterior
ssh prod-lapig 'docker service update --image lapig/tiles:prod_previous prod_tiles_tile'
```

Os tiles transparentes criados pelo `_empty_image_with_bands` continuarão em cache (válidos), mas os erros voltam a virar HTTP 200 + placeholder "ERRO" até a próxima tentativa expirar o cache (30 d).

## Contato

Em caso de dúvida durante deploy, escalar para o time de backend antes de aplicar rollback.

## Expansão do pool de Service Accounts do GEE

A taxa de 503 (`ee_unavailable`) sob carga é limitada pela cota das SAs
ativas no pool, não pelo código. O fix de rotação reativa (entrega de
2026-05-12) mitiga picos transitórios; o teto sustentável depende do
número de SAs com cota disponível.

### Sintomas que indicam saturação de capacidade

- Métrica `gee_sa_rotation_total` cresce continuamente, sem estabilizar.
- Métrica `gee_sa_in_cooldown` mostra todas as SAs alternando em cooldown.
- `gee_tile_url_regen_total` alto e sustentado — confirma que 429 não é
  pico transitório, é teto de capacidade.

### Ação operacional

1. Provisionar SAs adicionais no GCP Console (projeto `earthengine-legacy`
   ou equivalente). Cada SA tem cota independente no endpoint de tiles.
2. Baixar o JSON da nova SA e depositar em `.service-accounts/`:
   ```bash
   scp nova-sa.json prod-lapig:/path/to/.service-accounts/
   ```
3. Hot-reload do pool — sem necessidade de redeploy:
   ```bash
   curl -X POST http://localhost:8080/admin/gee-pool/refresh
   ```
4. Verificar que a nova SA foi descoberta:
   ```bash
   curl http://localhost:8080/admin/gee-pool/metrics
   ```
5. Acompanhar `gee_sa_http_429_total` por SA: distribuição deve ficar
   mais equilibrada à medida que o pool absorve a carga.

### Quando reduzir a frota

Se `gee_sa_in_cooldown` raramente passa de 0 e `gee_sa_rotation_total`
fica estagnado por dias seguidos, há overprovisionamento. SAs ociosas
não custam, mas complicam a rotação dos JSONs — manter o pool em
tamanho mínimo necessário para sustentar o pico observado.
