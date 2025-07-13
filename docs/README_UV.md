# Executando o Projeto com UV

Este guia mostra como executar o projeto localmente usando o `uv` para gerenciamento de dependências.

## Pré-requisitos

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) instalado
- Docker e Docker Compose (para serviços auxiliares)
- Arquivo de credenciais do Google Earth Engine

## Configuração Inicial

### 1. Criar ambiente virtual e instalar dependências

```bash
# Criar ambiente virtual
uv venv

# Ativar ambiente virtual
source .venv/bin/activate

# Instalar dependências
uv pip install -r requirements.txt
```

### 2. Iniciar serviços auxiliares

```bash
# Iniciar apenas Redis e MinIO
docker-compose -f docker-compose.services.yml up -d

# Verificar se estão rodando
docker ps
```

### 3. Configurar credenciais do Google Earth Engine

Certifique-se de ter o arquivo de credenciais em:
```
.service-accounts/gee.json
```

## Executando o Projeto

### Opção 1: Usando o script auxiliar

```bash
./run_local.sh
```

### Opção 2: Manualmente

```bash
# Ativar ambiente virtual
source .venv/bin/activate

# Configurar variáveis de ambiente
export TILES_ENV=development
export REDIS_URL=redis://localhost:6379
export S3_ENDPOINT=http://localhost:9000
export S3_ACCESS_KEY=minioadmin
export S3_SECRET_KEY=minioadmin
export GEE_SERVICE_ACCOUNT_FILE=./.service-accounts/gee.json

# Executar servidor
uvicorn main:app --reload --host 0.0.0.0 --port 8083
```

## Acessando a Aplicação

- **API**: http://localhost:8083
- **Documentação**: http://localhost:8083/docs
- **Redoc**: http://localhost:8083/redoc
- **MinIO Console**: http://localhost:9001 (admin/admin)

## Verificando o Sistema

### 1. Health Check
```bash
curl http://localhost:8083/health
```

### 2. Cache Stats
```bash
curl http://localhost:8083/api/cache/stats
```

### 3. Testar endpoint de tiles
```bash
# Landsat
curl "http://localhost:8083/api/layers/landsat/1000/2000/12?period=MONTH&year=2024&month=1&visparam=landsat-tvi-false"

# Sentinel-2
curl "http://localhost:8083/api/layers/s2_harmonized/1000/2000/12?period=WET&year=2024&visparam=tvi-red"
```

## Desenvolvimento

### Estrutura de Diretórios
```
tiles/
├── .venv/              # Ambiente virtual (criado pelo uv)
├── app/                # Código da aplicação
│   ├── api/           # Endpoints
│   ├── cache_hybrid.py # Sistema de cache
│   └── ...
├── cache/              # Cache local (criado automaticamente)
├── logs/               # Logs da aplicação
└── run_local.sh        # Script para executar localmente
```

### Hot Reload

O servidor reinicia automaticamente quando detecta mudanças no código (flag `--reload`).

### Logs

Os logs são salvos em:
- `logs/tiles/tiles.log` - Log geral
- `logs/tiles/tiles_WARNING.log` - Apenas warnings e erros

### Debug

Para debug detalhado, ajuste o nível de log:
```bash
export LOG_LEVEL=DEBUG
```

## Troubleshooting

### Problema: "Redis/Valkey não está rodando"
```bash
# Verificar se o container está rodando
docker ps | grep valkey

# Se não estiver, iniciar
docker-compose -f docker-compose.services.yml up -d valkey

# Verificar logs
docker logs valkey-local
```

### Problema: "MinIO não está rodando"
```bash
# Verificar se o container está rodando
docker ps | grep minio

# Se não estiver, iniciar
docker-compose -f docker-compose.services.yml up -d minio

# Verificar logs
docker logs minio-local
```

### Problema: "Failed to initialize GEE"
1. Verifique se o arquivo de credenciais existe
2. Verifique se as credenciais são válidas
3. Verifique a conectividade com a internet

### Problema: Dependências não instalam
```bash
# Limpar cache do uv
uv cache clean

# Reinstalar com verbose
uv pip install -r requirements.txt -v
```

## Performance Local

Para melhor performance em desenvolvimento:

1. **Use cache local**: Os tiles mais acessados são mantidos em memória
2. **Pre-warm tiles**: Execute o pre-warming para popular o cache
   ```bash
   python -m app.prewarm popular
   ```
3. **Monitore métricas**: Acesse http://localhost:8083/metrics

## Parando os Serviços

```bash
# Parar aplicação: Ctrl+C no terminal

# Parar serviços auxiliares
docker-compose -f docker-compose.services.yml down

# Parar e remover volumes (limpa dados)
docker-compose -f docker-compose.services.yml down -v
```