# 🔧 Guia para Remover Labels do Traefik e Usar Load Balance Global

## 📋 O que fazer em cada container (tile-1.json até tile-5.json)

### 1. Substituir TODAS as Labels por:
```json
"Labels": {
    "traefik.enable": "false"
}
```

### 2. Configuração Completa Simplificada

Para **TODOS** os containers (app_tile_1 até app_tile_5), use apenas:

```json
{
    "Hostname": "app_tile_X",
    "Names": "app_tile_X",
    "Image": "lapig/app_tile:prod_latest",
    "Tty": true,
    "WorkingDir": "/app",
    "Cmd": [
        "uvicorn", 
        "--host", 
        "0.0.0.0",
        "--port",
        "8080", 
        "--workers", 
        "20", 
        "main:app",
        "--timeout-keep-alive",
        "300"
    ],
    "Env":[
        "ALLOW_ORIGINS=http://localhost:4200",
        "GEE_SERVICE_ACCOUNT_FILE=/app/.service-accounts/gee.json",
        "LIFESPAN_URL=24",
        "LOG_LEVEL=WARNING",
        "MAX_REQUESTS=50000",
        "MAX_REQUESTS_JITTER=5000",
        "PORT=8080",
        "RATE_LIMIT_BURST=500",
        "RATE_LIMIT_PER_MINUTE=5000",
        "REDIS_URL=redis://valkey:6379",
        "S3_ACCESS_KEY=ZIv8tLyxtryMA7Lir5vX",
        "S3_BUCKET=tiles-cache",
        "S3_ENDPOINT=https://s3.lapig.iesa.ufg.br",
        "S3_SECRET_KEY=RIN4DkTNNBxXoUj2z8SwlsOLiVxRFDQu1EsgrlK3",
        "SKIP_GEE_INIT=false",
        "TILES_ENV=production",
        "WORKER_CONNECTIONS=4000",
        "WORKERS=20"
    ],
    "HostConfig": {
        "RestartPolicy": {
            "Name": "always"
        },
        "Memory": 4294967296,
        "MemoryReservation": 2147483648,
        "Mounts": [
            {
                "Type": "bind",
                "Source": "/etc/localtime",
                "Target": "/etc/localtime",
                "ReadOnly": true
            },
            {
                "Type": "bind",
                "Source": "/home/suporte/config/service_account/blissful-axiom-314717.json",
                "Target": "/app/.service-accounts/gee.json",
                "ReadOnly": true
            }
        ],
        "PortBindings": {
            "8080/tcp": [
                {
                    "HostPort": ""
                }
            ]
        },
        "NetworkMode": "web_lapig"
    },
    "NetworkingConfig": {
        "EndpointsConfig": {
            "web_lapig": {
                "IPAMConfig": {}
            }
        }
    },
    "Labels": {
        "traefik.enable": "false"
    }
}
```

**Substitua X pelo número da instância (1, 2, 3, 4, 5)**

## 🚀 Script para Atualizar Todos os Arquivos

```bash
#!/bin/bash
# remove-traefik-labels.sh

# Diretório dos arquivos
ENV_DIR="/home/tharles/projects_lapig/tiles/env"

# Remover labels do Traefik de cada arquivo
for i in {1..5}; do
    FILE="$ENV_DIR/tile-${i}.json"
    
    # Fazer backup
    cp "$FILE" "$FILE.backup-$(date +%Y%m%d-%H%M%S)"
    
    # Usar jq para substituir todas as labels por apenas traefik.enable=false
    jq '.Labels = {"traefik.enable": "false"}' "$FILE" > "$FILE.tmp" && mv "$FILE.tmp" "$FILE"
    
    echo "Atualizado: $FILE"
done

echo "Concluído! Todas as labels do Traefik foram removidas."
```

## 📝 O que isso faz:

1. **Remove** todas as configurações individuais de roteamento
2. **Desabilita** o Traefik discovery para estes containers
3. **Permite** que o arquivo `traefik.yml` controle todo o roteamento
4. **Centraliza** o load balancing no arquivo de configuração

## ✅ Resultado:

- **Única fonte de verdade**: Apenas o `traefik.yml` controla o roteamento
- **Load balancing unificado**: Todos os 6 hosts (tiles + tm1-5) vão para o mesmo pool
- **Rate limiting global**: 2500 req/s distribuído entre todas as instâncias
- **Sem conflitos**: Nenhuma configuração duplicada

## 🔄 Passos para Aplicar:

1. Execute o script para remover as labels
2. Aplique o arquivo `traefik-optimized.yml` no Traefik
3. Reinicie todos os containers:
   ```bash
   docker restart app_tile_1 app_tile_2 app_tile_3 app_tile_4 app_tile_5
   docker restart traefik
   ```

## ⚠️ Importante:

Após fazer isso, **TODOS** os acessos aos domínios:
- tiles.lapig.iesa.ufg.br
- tm1.lapig.iesa.ufg.br
- tm2.lapig.iesa.ufg.br
- tm3.lapig.iesa.ufg.br
- tm4.lapig.iesa.ufg.br
- tm5.lapig.iesa.ufg.br

Serão balanceados entre as 5 instâncias pelo Traefik!