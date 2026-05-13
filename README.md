# 🗺️ Tiles API - High Performance

Sistema de alta performance para servir tiles geográficos com cache híbrido, otimizado para milhões de requisições por segundo.

## 🚀 Quick Start

### Com Docker Compose (Recomendado)
```bash
# 1. Clone o repositório
git clone <seu-repositorio>
cd tiles

# 2. Configure credenciais GEE
cp .service-accounts/gee.json.example .service-accounts/gee.json
# Edite com suas credenciais

# 3. Inicie todos os serviços
docker compose up -d

# 4. Configure MinIO
./scripts/setup-minio-simple.sh
```

### Com UV (Desenvolvimento)
```bash
# 1. Instale UV
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Configure ambiente
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# 3. Inicie serviços auxiliares
docker compose -f docker/docker-compose.services.yml up -d

# 4. Execute aplicação
./scripts/run_local_dev.sh
```

## 📁 Estrutura do Projeto

```
tiles/
├── app/                    # Código da aplicação
│   ├── api/               # Endpoints REST
│   ├── cache_hybrid.py    # Sistema de cache híbrido
│   └── prewarm.py         # Pre-warming de tiles
├── config/                # Configurações
│   ├── minio/            # Políticas S3/MinIO
│   ├── nginx/            # Configuração Nginx
│   └── prometheus.yml    # Métricas
├── docker/                # Docker compose extras
├── docs/                  # Documentação detalhada
├── scripts/               # Scripts utilitários
└── docker-compose.yml     # Stack principal
```

## 🔧 Configuração

### Variáveis de Ambiente

Copie o arquivo `.env.example` para `.env` e ajuste os valores:

```bash
cp .env.example .env
```

#### Variáveis Principais

| Variável | Descrição | Valor Padrão |
|----------|-----------|---------------|
| **TILES_ENV** | Ambiente de execução | `development` |
| **PORT** | Porta do servidor | `8083` |
| **GEE_SERVICE_ACCOUNT_FILE** | Credenciais Google Earth Engine | `./.service-accounts/gee.json` |
| **SKIP_GEE_INIT** | Pular inicialização do GEE | `false` |
| **REDIS_URL** | URL de conexão Redis/Valkey | `redis://localhost:6379` |
| **S3_ENDPOINT** | Endpoint S3/MinIO | `http://localhost:9000` |
| **S3_ACCESS_KEY** | Chave de acesso S3 | `minioadmin` |
| **S3_SECRET_KEY** | Chave secreta S3 | `minioadmin` |
| **S3_BUCKET** | Nome do bucket | `tiles-cache` |
| **WORKERS** | Número de workers | `32` |
| **WORKER_CONNECTIONS** | Conexões por worker | `2000` |
| **MAX_REQUESTS** | Requisições antes de restart | `10000` |
| **RATE_LIMIT_PER_MINUTE** | Limite de requisições/min | `1000` |
| **LIFESPAN_URL** | TTL da URL do EE (horas) | `24` |
| **LOG_LEVEL** | Nível de log | `INFO` |

### Configuração no IntelliJ IDEA / PyCharm

1. **Opção 1**: Use o arquivo de configuração pronto
   - O arquivo `.idea/runConfigurations/Tiles_API.xml` já está configurado
   - Abra o projeto no IntelliJ/PyCharm e a configuração aparecerá automaticamente

2. **Opção 2**: Configure manualmente
   - Run → Edit Configurations → Add New Configuration → Python
   - Script path: `main.py`
   - Environment variables: copie o conteúdo de `tiles.env`

### Endpoints Principais
- `GET /api/layers/landsat/{x}/{y}/{z}` - Tiles Landsat
- `GET /api/layers/s2_harmonized/{x}/{y}/{z}` - Tiles Sentinel-2
- `GET /api/cache/stats` - Estatísticas do cache
- `GET /health` - Health check

## 📊 Console de Administração

- **MinIO Console**: http://localhost:9001 (minioadmin/minioadmin)

## 🚀 Performance

### Arquitetura de Cache
```
[CDN] → [Nginx] → [App] → [Cache Híbrido]
                             ├── Redis (metadados)
                             └── S3/MinIO (tiles PNG)
```

### Características
- ✅ Cache multi-camada
- ✅ TTL otimizado (30 dias tiles)
- ✅ Pre-warming automático
- ✅ Rate limiting
- ✅ Compressão gzip
- ✅ Suporta milhões req/s

## 📚 Documentação

- [Guia de Performance](docs/README_PERFORMANCE.md)
- [Políticas MinIO](docs/MINIO_POLICIES.md)
- [Quick Start UV](docs/QUICKSTART_UV.md)

## 🛠️ Desenvolvimento

```bash
# Testes
pytest

# Linting
ruff check .

# Type checking
mypy app/

# Pre-warming manual
python -m app.prewarm popular
```

## 📝 Licença

[Sua licença aqui]

## 🤝 Contribuindo

1. Fork o projeto
2. Crie sua branch (`git checkout -b feature/AmazingFeature`)
3. Commit suas mudanças (`git commit -m 'Add some AmazingFeature'`)
4. Push para a branch (`git push origin feature/AmazingFeature`).
5. Abra um Pull Request

