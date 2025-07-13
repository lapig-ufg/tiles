#!/bin/bash
# Script para executar o projeto em modo desenvolvimento sem GEE

# Ativa o ambiente virtual
source .venv/bin/activate

# Configura variáveis de ambiente
export TILES_ENV=development
export REDIS_URL=redis://localhost:6379
export S3_ENDPOINT=http://localhost:9000
export S3_ACCESS_KEY=minioadmin
export S3_SECRET_KEY=minioadmin
export GEE_SERVICE_ACCOUNT_FILE=./.service-accounts/gee.json
export SKIP_GEE_INIT=true  # Pula inicialização do GEE

echo "⚠️  MODO DESENVOLVIMENTO - Google Earth Engine desabilitado"
echo ""
echo "Verificando serviços necessários..."

# Verifica Redis/Valkey
if ! nc -z localhost 6379 2>/dev/null; then
    echo "⚠️  Redis/Valkey não está rodando na porta 6379"
    echo "   Execute: docker compose -f docker-compose.services.yml up -d"
    exit 1
fi

# Verifica MinIO
if ! nc -z localhost 9000 2>/dev/null; then
    echo "⚠️  MinIO não está rodando na porta 9000"
    echo "   Execute: docker compose -f docker-compose.services.yml up -d"
    exit 1
fi

echo "✅ Serviços auxiliares OK"
echo ""
echo "Iniciando servidor de desenvolvimento..."
echo "Acesse: http://localhost:8083"
echo "Docs: http://localhost:8083/docs"
echo ""

# Executa o servidor com uvicorn para desenvolvimento
uvicorn main:app --reload --host 0.0.0.0 --port 8083