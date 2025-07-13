#!/bin/bash
# Script para aplicar configuração CORS no bucket MinIO

MINIO_ENDPOINT=${MINIO_ENDPOINT:-http://localhost:9000}
BUCKET_NAME=${BUCKET_NAME:-tiles-cache}

echo "Aplicando configuração CORS ao bucket ${BUCKET_NAME}..."

# Aplica CORS
docker run --rm \
    --network host \
    -v $(pwd):/config \
    minio/mc:latest \
    cors set /config/minio-cors-config.json myminio/${BUCKET_NAME}

# Verifica CORS
echo -e "\nConfigurações CORS aplicadas:"
docker run --rm \
    --network host \
    minio/mc:latest \
    cors get myminio/${BUCKET_NAME}

echo -e "\n✅ CORS configurado com sucesso!"