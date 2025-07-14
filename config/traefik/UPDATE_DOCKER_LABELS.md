# 📝 Atualização das Labels Docker para Usar Middlewares

## 🎯 Solução: Adicionar Middlewares via Labels

Para evitar conflitos, mantenha os routers individuais e adicione os middlewares através das labels do Docker.

## 📋 Labels Atualizadas para cada Container

### Para `app_tile_1` (tiles.lapig.iesa.ufg.br e tm1.lapig.iesa.ufg.br):
```json
"Labels": {
    "traefik.enable": "true",
    "traefik.http.routers.app_tile_1.rule": "Host(`tiles.lapig.iesa.ufg.br`) || Host(`tm1.lapig.iesa.ufg.br`)",
    "traefik.http.routers.app_tile_1.tls": "true",
    "traefik.http.routers.app_tile_1.tls.certresolver": "le",
    "traefik.http.routers.app_tile_1.entrypoints": "websecure",
    "traefik.http.routers.app_tile_1.service": "app_tile_1",
    "traefik.http.routers.app_tile_1.middlewares": "rate-limit-tiles@file,compress@file,cache-headers-tiles@file,secure-headers@file",
    "traefik.http.services.app_tile_1.loadbalancer.server.port": "8080",
    "traefik.http.services.app_tile_1.loadbalancer.sticky": "true",
    "traefik.http.services.app_tile_1.loadbalancer.sticky.cookie.name": "tile_session",
    "traefik.http.services.app_tile_1.loadbalancer.sticky.cookie.httpOnly": "true",
    "traefik.http.services.app_tile_1.loadbalancer.sticky.cookie.secure": "true",
    "traefik.http.services.app_tile_1.loadbalancer.healthcheck.path": "/health",
    "traefik.http.services.app_tile_1.loadbalancer.healthcheck.interval": "10s"
}
```

### Para `app_tile_2` até `app_tile_5`:
```json
"Labels": {
    "traefik.enable": "true",
    "traefik.http.routers.app_tile_X.rule": "Host(`tmX.lapig.iesa.ufg.br`)",
    "traefik.http.routers.app_tile_X.tls": "true",
    "traefik.http.routers.app_tile_X.tls.certresolver": "le",
    "traefik.http.routers.app_tile_X.entrypoints": "websecure",
    "traefik.http.routers.app_tile_X.service": "app_tile_X",
    "traefik.http.routers.app_tile_X.middlewares": "rate-limit-tiles@file,compress@file,cache-headers-tiles@file,secure-headers@file",
    "traefik.http.services.app_tile_X.loadbalancer.server.port": "8080",
    "traefik.http.services.app_tile_X.loadbalancer.sticky": "true",
    "traefik.http.services.app_tile_X.loadbalancer.sticky.cookie.name": "tile_session",
    "traefik.http.services.app_tile_X.loadbalancer.sticky.cookie.httpOnly": "true",
    "traefik.http.services.app_tile_X.loadbalancer.sticky.cookie.secure": "true",
    "traefik.http.services.app_tile_X.loadbalancer.healthcheck.path": "/health",
    "traefik.http.services.app_tile_X.loadbalancer.healthcheck.interval": "10s"
}
```
*Substitua X pelo número da instância (2, 3, 4, 5)*

## 🔧 Script para Atualizar Todos os Arquivos

```bash
#!/bin/bash
# update-tile-labels.sh

# Diretório com os arquivos JSON
ENV_DIR="/home/tharles/projects_lapig/tiles/env"

# Adicionar middlewares a cada arquivo
for i in {1..5}; do
    FILE="$ENV_DIR/tile-${i}.json"
    
    # Fazer backup
    cp "$FILE" "$FILE.backup"
    
    # Adicionar a linha de middlewares após o service
    # Este é um exemplo - ajuste conforme a estrutura exata do seu JSON
    jq '.Labels += {
        "traefik.http.routers.app_tile_'$i'.middlewares": "rate-limit-tiles@file,compress@file,cache-headers-tiles@file,secure-headers@file",
        "traefik.http.services.app_tile_'$i'.loadbalancer.sticky": "true",
        "traefik.http.services.app_tile_'$i'.loadbalancer.sticky.cookie.name": "tile_session",
        "traefik.http.services.app_tile_'$i'.loadbalancer.sticky.cookie.httpOnly": "true",
        "traefik.http.services.app_tile_'$i'.loadbalancer.sticky.cookie.secure": "true",
        "traefik.http.services.app_tile_'$i'.loadbalancer.healthcheck.path": "/health",
        "traefik.http.services.app_tile_'$i'.loadbalancer.healthcheck.interval": "10s"
    }' "$FILE" > "$FILE.tmp" && mv "$FILE.tmp" "$FILE"
done
```

## 🚀 Aplicação

1. **Use o arquivo `traefik-labels-compatible.yml`** que contém apenas middlewares (sem routers/services)
2. **Atualize as labels** em cada arquivo JSON de configuração do container
3. **Reinicie os containers** para aplicar as novas labels

## ✅ Vantagens desta Abordagem

1. **Sem conflitos**: Routers continuam definidos via labels
2. **Middlewares centralizados**: Definidos no arquivo YAML
3. **Rate limiting aplicado**: Cada instância usa os mesmos middlewares
4. **Flexibilidade**: Cada host mantém seu próprio router

## 📊 Resultado

- Cada instância terá seu próprio router (sem conflito)
- Todos compartilharão os mesmos middlewares (rate limiting, cache, etc.)
- Rate limiting total: 2500 req/s distribuído entre todas as instâncias
- Cache de 30 dias aplicado a todos os tiles