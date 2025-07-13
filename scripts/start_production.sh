#!/bin/bash
# Script otimizado para produção com alta performance

# Detecta número de CPUs
CPUS=$(nproc)
WORKERS=$((CPUS * 2))  # 2 workers por CPU

# Variáveis de ambiente para otimização
export PYTHONUNBUFFERED=1
export TILES_ENV=production

# Configurações do Gunicorn para alta performance
echo "Iniciando servidor com $WORKERS workers..."

gunicorn main:app \
    --workers $WORKERS \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8083 \
    --worker-connections 2000 \
    --max-requests 10000 \
    --max-requests-jitter 1000 \
    --timeout 30 \
    --keepalive 5 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    --preload \
    --graceful-timeout 30 \
    --limit-request-line 0 \
    --limit-request-field_size 0 \
    --worker-tmp-dir /dev/shm