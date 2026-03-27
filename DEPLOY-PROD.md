# Deploy em Produção — Pool de Service Accounts GEE

## Pré-requisitos

- [ ] Todos os 12 arquivos `.json` de SAs no diretório `.service-accounts/` de cada instância
- [ ] Redis/Valkey acessível (já existe — usado para coordenação do pool)
- [ ] MongoDB com `vis_params` populado (já existe)

## Passo a passo

### 1. Atualizar código

```bash
git pull origin main
```

### 2. Rebuild da imagem Docker

```bash
docker build --no-cache -t lapig/app_tile:prod_latest -f docker/prod/Dockerfile .
```

### 3. Atualizar volume de SAs no compose de cada instância

Em cada `docker-compose.yml` de produção, alterar:

```yaml
# ANTES
volumes:
  - './.service-accounts/gee.json:/app/.service-accounts/gee.json'

# DEPOIS
volumes:
  - './.service-accounts:/app/.service-accounts:ro'
```

### 4. Copiar SAs para todas as instâncias

Garantir que cada host que roda uma instância do tile server tenha o diretório
`.service-accounts/` com todos os 12 arquivos JSON.

### 5. Deploy (rolling restart)

```bash
docker compose pull
docker compose up -d --force-recreate
```

### 6. Validação pós-deploy

```bash
# Health check
curl http://localhost:8083/health/light

# Verificar pool de SAs
curl http://localhost:8083/admin/gee/pool | python3 -m json.tool

# Verificar distribuição de workers
curl http://localhost:8083/admin/gee/workers | python3 -m json.tool

# Verificar que todas as SAs têm workers atribuídos
# e nenhuma está em cooldown
```

### 7. Monitoramento contínuo

Acompanhar nos primeiros minutos:

```bash
# Verificar se há erros 429 no pool
watch -n 10 'curl -s http://localhost:8083/admin/gee/pool | python3 -c "
import json,sys
d=json.load(sys.stdin)
a=d[\"accounts\"]
t429=sum(v[\"errors_429\"] for v in a.values())
cd=sum(1 for v in a.values() if v[\"in_cooldown\"])
w=sum(v[\"active_workers\"] for v in a.values())
print(f\"SAs={len(a)} workers={w} 429={t429} cooldown={cd}\")
"'
```

## O que mudou (resumo técnico)

| Componente | Antes | Depois |
|------------|-------|--------|
| GEE init | 1 SA global no lifespan | 1 SA por worker via `post_fork` |
| Gunicorn | `--preload` (SA compartilhada) | `--config gunicorn_conf.py` (SA por worker) |
| Erros 429 | Retry cego com backoff | Rotação automática de SA + backoff |
| Coordenação | Nenhuma | Redis sorted set entre todas as instâncias |
| `getInfo()` | Síncrono, bloqueante | REST API async via `ee_compute.py` |
| Monitoramento | Nenhum | `/admin/gee/pool` e `/admin/gee/workers` |
| Hot-reload SAs | Restart necessário | `POST /admin/gee/reload` |

## Rollback

Se houver problema, reverter para o comportamento anterior:

1. No `settings.toml`, definir `SKIP_GEE_INIT=true`
2. Ou reverter o commit e rebuild a imagem

O pool é retrocompatível — se o Redis não estiver acessível, o worker tenta
inicializar com a primeira SA disponível no diretório.
