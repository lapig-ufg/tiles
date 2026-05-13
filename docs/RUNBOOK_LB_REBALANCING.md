# Runbook — Rebalanceamento do Load Balancer de Tiles

## Sintoma

Um pod do serviço de tiles (`prod_tiles_tile.<N>`) recebe carga muito superior à
mediana — observado até **17× a mediana** para `tile.4` — enquanto outros pods
operam ociosos. O desequilíbrio é **intermitente** e correlacionado com sessões
HTTP longas de clientes pesados (ex.: campanhas TVI que renderizam muitos tiles
em sequência).

## Causa raiz

O Traefik está configurado com **sticky session por cookie** no service
`tiles-loadbalancer`:

```
traefik.http.services.tiles-loadbalancer.loadbalancer.sticky.cookie = true
traefik.http.services.tiles-loadbalancer.loadbalancer.sticky.cookie.name = tiles_session
```

Com sticky cookie ativo, **todas as requisições HTTP de um mesmo cliente vão
para o mesmo pod backend indefinidamente**. A motivação histórica era cache
locality (cada pod tem um `local_cache` LRU in-memory de 1000 tiles), mas a
estratégia escolhida é errada — o desejado seria afinidade **tile → pod**
(consistent hashing por URL), não **cliente → pod**.

Quando um cliente carrega tiles em massa (ex.: 5000 tiles para uma campanha),
ele concentra todo esse tráfego em um único pod. O `wrr` (weighted round
robin) do Traefik só distribui o cookie inicial; depois disso, o cookie
governa.

## Topologia atual

| Aspecto | Valor |
|---------|-------|
| Stack Swarm | `prod_tiles` |
| Réplicas | 20 (max 10 por nó, spread entre vm1 e vm2) |
| Nodes | vm1, vm2 |
| Load balancer | Traefik v3.6.7 |
| Strategy | `wrr` |
| Discovery | Traefik enumera as 20 réplicas individualmente (IPs `10.0.10.x:8080`) |
| Cache local | LRU in-memory de 1000 tiles por pod (default) |
| Cache compartilhado | Valkey (metadados) + S3 (PNGs) |

## Inspeção do estado atual

### Listar pods de tile

```bash
ssh prod-lapig "docker ps --format 'table {{.Name}}\t{{.Status}}' \
  | grep prod_tiles_tile"
```

### Distribuição de CPU/RAM por pod

```bash
ssh prod-lapig "docker stats --no-stream \
  \$(docker ps -q -f name=prod_tiles_tile)"
```

Desvio padrão alto entre pods (>3% CPU) indica desbalanceamento ativo.

### Configuração do Traefik (verifica se sticky está ativo)

```bash
ssh prod-lapig "TRAEFIK_CID=\$(docker ps -q -f name=traefik | head -1); \
  docker exec \$TRAEFIK_CID wget -qO- http://localhost:8080/api/http/services \
  | python3 -c 'import sys,json; \
    [print(json.dumps(s, indent=2)) for s in json.load(sys.stdin) \
     if \"tiles-loadbalancer\" in s.get(\"name\",\"\")]' \
  | grep -A 8 sticky"
```

Esperado **após o fix**: ausência de campo `sticky` no output.

### Health dos backends

```bash
ssh prod-lapig "TRAEFIK_CID=\$(docker ps -q -f name=traefik | head -1); \
  docker exec \$TRAEFIK_CID wget -qO- http://localhost:8080/api/http/services \
  | python3 -c 'import sys,json; s=[x for x in json.load(sys.stdin) \
    if \"tiles-loadbalancer\" in x.get(\"name\",\"\")][0]; \
    print(json.dumps(s.get(\"serverStatus\",{}), indent=2))'"
```

Esperado: 20 endereços, todos `UP`.

## Procedimento de mudança

### Pré-requisitos

- Acesso a `vm1` ou `vm2` como usuário `suporte`.
- Versão recém-aprovada de `config/swarm/prod.compose.yml` no repositório
  (branch `feat/tiles-p1-remove-sticky-cookie`).

### Passo 1 — Sincronizar o arquivo do repo com o host

A partir de uma checkout local do repo, com a branch correta:

```bash
scp config/swarm/prod.compose.yml \
  prod-lapig:/glusterfs/aplications/services/tiles/prod.compose.yml
```

Validar diff antes do deploy:

```bash
ssh prod-lapig "diff /glusterfs/aplications/services/tiles/prod.compose.yml \
  <(cat /glusterfs/aplications/services/tiles/prod.compose.yml.bak)"
```

(Fazer o backup antes do scp.)

### Passo 2 — Deploy do stack

Em vm1 (manager node):

```bash
ssh prod-lapig "cd /glusterfs/aplications/services/tiles && \
  docker stack deploy -c prod.compose.yml prod_tiles --with-registry-auth"
```

A diretiva `update_config: order: start-first, parallelism: 2` garante rolling
update sem downtime: 2 pods por vez, novo sobe antes do antigo cair.

### Passo 3 — Verificação imediata

1. **Confirmar ausência do Set-Cookie:**

   ```bash
   curl -sI https://tiles.lapig.iesa.ufg.br/health/light \
     | grep -i 'set-cookie' || echo 'OK: sem sticky cookie'
   ```

2. **Confirmar nivelamento de CPU** (esperado <0.5% de desvio padrão em ~15 min
   após o deploy completo):

   ```bash
   watch -n 5 'ssh prod-lapig "docker stats --no-stream \
     \$(docker ps -q -f name=prod_tiles_tile)"'
   ```

3. **Confirmar `LOCAL_CACHE_SIZE=10000` aplicado:**

   ```bash
   ssh prod-lapig "docker inspect \
     \$(docker ps -q -f name=prod_tiles_tile | head -1) \
     | grep -A 1 LOCAL_CACHE_SIZE"
   ```

### Passo 4 — Monitoramento por 24 h

- Métricas Prometheus: `tile_requests_total` por pod (via label `pod`/`instance`
  agregada no Grafana).
- Logs do Valkey: medir aumento de carga (esperado leve aumento, com
  `LOCAL_CACHE_SIZE=10000` o impacto é pequeno).
- Métrica `gee_sa_in_cooldown`: esperado redução pelo nivelamento de carga.

## Rollback

Se o desempenho cair ou cache local degradar significativamente:

```bash
ssh prod-lapig "cp /glusterfs/aplications/services/tiles/prod.compose.yml.bak \
  /glusterfs/aplications/services/tiles/prod.compose.yml && \
  cd /glusterfs/aplications/services/tiles && \
  docker stack deploy -c prod.compose.yml prod_tiles --with-registry-auth"
```

O rolling update restaura os pods com a config anterior em ~2-3 minutos.

## Justificativa técnica para remoção

| Argumento | Detalhe |
|-----------|---------|
| Serviço stateless | Toda persistência fica em Valkey + S3 compartilhados — pods não mantêm estado de sessão |
| Cache local pequeno | 1000 tiles ≈ 30 MB por pod; é caching de hot path, não dado crítico |
| Penalidade de miss baixa | Cache miss local cai para Valkey (sub-ms), depois S3 (~10 ms) — sem ir ao GEE |
| Compensação aplicada | `LOCAL_CACHE_SIZE=10000` no `x-common-tile-env` aumenta 10× o cache local; 300 MB de 4 GB de limite por pod |
| Métrica observável | Após mudança, `tile_duration_seconds` deve permanecer no mesmo p99; se subir, ajustar `LOCAL_CACHE_SIZE` |

## Investigação adicional para cliente pesado (se reincidir após fix)

Caso o desequilíbrio persista mesmo sem o cookie, suspeitar de:

1. **Keep-alive HTTP/1.1 longo** — `--timeout-keep-alive 300` no uvicorn pode
   manter uma conexão de cliente no mesmo pod por 5 min. Não é problema se
   bem distribuído; é problema se um único IP gera 1000 req/s nessa conexão.
2. **Single client behaviour** — identificar via:

   ```bash
   ssh prod-lapig "docker service logs prod_tiles_tile --since 1h 2>&1 \
     | grep 'GET /api/layers' \
     | awk '{print \$NF}' \
     | sort | uniq -c | sort -rn | head -20"
   ```

   Se um único IP domina, considerar rate limit por IP no Traefik.

3. **Burst de campanha programado** — bater com o cron de Celery
   (`celery_beat`) ou jobs de pre-aquecimento de cache. Esses devem rodar
   contra Valkey/S3 direto, não via load balancer.
