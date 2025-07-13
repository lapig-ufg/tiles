# 🔧 Variáveis de Ambiente - Tiles API

Este documento descreve todas as variáveis de ambiente disponíveis para configurar a Tiles API.

## 📋 Índice

- [Configuração Básica](#configuração-básica)
- [Google Earth Engine](#google-earth-engine)
- [Cache Redis/Valkey](#cache-redisvalkey)
- [Cache S3/MinIO](#cache-s3minio)
- [Performance](#performance)
- [Rate Limiting](#rate-limiting)
- [Cache TTL](#cache-ttl)
- [CORS](#cors)
- [Logging](#logging)
- [Desenvolvimento](#desenvolvimento)

## Configuração Básica

### TILES_ENV
- **Descrição**: Define o ambiente de execução
- **Valores**: `development`, `production`
- **Padrão**: `development`
- **Exemplo**: `TILES_ENV=production`

### PORT
- **Descrição**: Porta em que o servidor irá escutar
- **Tipo**: Inteiro
- **Padrão**: `8083`
- **Exemplo**: `PORT=8000`

## Google Earth Engine

### GEE_SERVICE_ACCOUNT_FILE
- **Descrição**: Caminho para o arquivo JSON com as credenciais de serviço do Google Earth Engine
- **Tipo**: String (caminho de arquivo)
- **Padrão**: `./.service-accounts/gee.json`
- **Exemplo**: `GEE_SERVICE_ACCOUNT_FILE=/secrets/gee-credentials.json`
- **Importante**: Este arquivo deve conter credenciais válidas do GEE

### SKIP_GEE_INIT
- **Descrição**: Pula a inicialização do Google Earth Engine (útil para desenvolvimento sem credenciais)
- **Valores**: `true`, `false`
- **Padrão**: `false`
- **Exemplo**: `SKIP_GEE_INIT=true`

## Cache Redis/Valkey

### REDIS_URL
- **Descrição**: URL completa de conexão com o Redis/Valkey
- **Formato**: `redis://[username:password@]host:port[/database]`
- **Padrão**: `redis://localhost:6379`
- **Exemplos**:
  - Local: `redis://localhost:6379`
  - Com senha: `redis://:mypassword@redis-server:6379`
  - Com database: `redis://localhost:6379/1`

## Cache S3/MinIO

### S3_ENDPOINT
- **Descrição**: URL do endpoint S3 ou MinIO
- **Tipo**: String (URL)
- **Padrão**: `http://localhost:9000`
- **Exemplos**:
  - MinIO local: `http://localhost:9000`
  - AWS S3: `https://s3.amazonaws.com`
  - MinIO produção: `https://minio.example.com`

### S3_ACCESS_KEY
- **Descrição**: Chave de acesso (Access Key ID) para S3/MinIO
- **Tipo**: String
- **Padrão**: `minioadmin`
- **Exemplo**: `S3_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE`

### S3_SECRET_KEY
- **Descrição**: Chave secreta (Secret Access Key) para S3/MinIO
- **Tipo**: String
- **Padrão**: `minioadmin`
- **Exemplo**: `S3_SECRET_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY`
- **Segurança**: Nunca commite esta chave no repositório

### S3_BUCKET
- **Descrição**: Nome do bucket S3/MinIO para armazenar cache de tiles
- **Tipo**: String
- **Padrão**: `tiles-cache`
- **Exemplo**: `S3_BUCKET=production-tiles-cache`

## Performance

### WORKERS
- **Descrição**: Número de processos worker do Gunicorn
- **Tipo**: Inteiro
- **Padrão**: `32`
- **Recomendação**: `(2 x número de CPUs) + 1`
- **Exemplo**: `WORKERS=64`

### WORKER_CONNECTIONS
- **Descrição**: Número máximo de conexões simultâneas por worker
- **Tipo**: Inteiro
- **Padrão**: `2000`
- **Exemplo**: `WORKER_CONNECTIONS=4000`

### MAX_REQUESTS
- **Descrição**: Número de requisições antes de reiniciar um worker
- **Tipo**: Inteiro
- **Padrão**: `10000`
- **Exemplo**: `MAX_REQUESTS=20000`
- **Nota**: Ajuda a prevenir vazamentos de memória

### MAX_REQUESTS_JITTER
- **Descrição**: Variação aleatória no MAX_REQUESTS para evitar restart simultâneo
- **Tipo**: Inteiro
- **Padrão**: `1000`
- **Exemplo**: `MAX_REQUESTS_JITTER=2000`

## Rate Limiting

### RATE_LIMIT_PER_MINUTE
- **Descrição**: Número máximo de requisições por minuto por IP
- **Tipo**: Inteiro
- **Padrão**: `1000`
- **Exemplo**: `RATE_LIMIT_PER_MINUTE=500`

### RATE_LIMIT_BURST
- **Descrição**: Número de requisições permitidas em burst
- **Tipo**: Inteiro
- **Padrão**: `100`
- **Exemplo**: `RATE_LIMIT_BURST=200`

## Cache TTL

### LIFESPAN_URL
- **Descrição**: Tempo de vida da URL do Earth Engine em horas
- **Tipo**: Inteiro (horas)
- **Padrão**: `24`
- **Exemplo**: `LIFESPAN_URL=48`

## CORS

### ALLOW_ORIGINS
- **Descrição**: Lista de origens permitidas para CORS
- **Formato**: URLs separadas por vírgula
- **Padrão**: `` (vazio = permite todas)
- **Exemplos**:
  - Produção: `ALLOW_ORIGINS=https://app.example.com,https://www.example.com`
  - Desenvolvimento: `ALLOW_ORIGINS=http://localhost:3000,http://localhost:4200`

## Logging

### LOG_LEVEL
- **Descrição**: Nível mínimo de log a ser exibido
- **Valores**: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- **Padrão**: `INFO`
- **Exemplo**: `LOG_LEVEL=DEBUG`

## Desenvolvimento

### UV_NO_SYNC
- **Descrição**: Desabilita sincronização automática de dependências do UV
- **Valores**: `true`, `false`
- **Padrão**: `false`
- **Exemplo**: `UV_NO_SYNC=true`

### UV_SYSTEM_PYTHON
- **Descrição**: Usa Python do sistema ao invés do gerenciado pelo UV
- **Valores**: `true`, `false`
- **Padrão**: `false`
- **Exemplo**: `UV_SYSTEM_PYTHON=true`

## 🚀 Exemplos de Configuração

### Desenvolvimento Local
```bash
TILES_ENV=development
PORT=8083
SKIP_GEE_INIT=true
REDIS_URL=redis://localhost:6379
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
WORKERS=4
LOG_LEVEL=DEBUG
```

### Produção
```bash
TILES_ENV=production
PORT=8083
GEE_SERVICE_ACCOUNT_FILE=/secrets/gee.json
REDIS_URL=redis://redis-cluster:6379
S3_ENDPOINT=https://s3.amazonaws.com
S3_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE
S3_SECRET_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
S3_BUCKET=production-tiles
WORKERS=64
WORKER_CONNECTIONS=4000
RATE_LIMIT_PER_MINUTE=2000
LOG_LEVEL=WARNING
ALLOW_ORIGINS=https://app.example.com
```

## 📝 Notas

1. **Segurança**: Nunca commite arquivos `.env` com credenciais reais
2. **Docker**: As variáveis no `docker-compose.yml` sobrescrevem as do `.env`
3. **Prioridade**: Variáveis de ambiente > arquivo `.env` > `settings.toml`
4. **Validação**: A aplicação valida as configurações críticas na inicialização