#!/bin/bash
# Script simplificado para configurar MinIO

set -e

echo "🚀 Configurando MinIO para o projeto Tiles..."

# Configurações
MINIO_ALIAS="myminio"
MINIO_ENDPOINT="http://localhost:9000"
MINIO_ACCESS_KEY="minioadmin"
MINIO_SECRET_KEY="minioadmin"
BUCKET_NAME="tiles-cache"

# Executa comandos MinIO Client em sequência
echo "1️⃣ Configurando alias..."
docker run --rm --network host \
    minio/mc:latest \
    alias set ${MINIO_ALIAS} ${MINIO_ENDPOINT} ${MINIO_ACCESS_KEY} ${MINIO_SECRET_KEY}

echo "2️⃣ Criando bucket ${BUCKET_NAME}..."
docker run --rm --network host \
    minio/mc:latest \
    mb --ignore-existing ${MINIO_ALIAS}/${BUCKET_NAME}

echo "3️⃣ Configurando acesso público para leitura..."
docker run --rm --network host \
    minio/mc:latest \
    anonymous set download ${MINIO_ALIAS}/${BUCKET_NAME}

echo "4️⃣ Verificando configuração..."
docker run --rm --network host \
    minio/mc:latest \
    anonymous get ${MINIO_ALIAS}/${BUCKET_NAME}

echo ""
echo "✅ MinIO configurado com sucesso!"
echo ""
echo "📋 Resumo da configuração:"
echo "   - Bucket: ${BUCKET_NAME}"
echo "   - Endpoint: ${MINIO_ENDPOINT}"
echo "   - Console: http://localhost:9001"
echo "   - Login: ${MINIO_ACCESS_KEY} / ${MINIO_SECRET_KEY}"
echo "   - Política: Leitura pública em /tiles/*"
echo ""
echo "🔍 Para testar o acesso:"
echo "   curl http://localhost:9000/${BUCKET_NAME}/tiles/test.png"