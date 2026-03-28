#!/bin/bash

cd /app

# Atualiza código se necessário
if [ "$AUTO_UPDATE" = "true" ]; then
    git pull
    pip install --no-cache-dir -r requirements.txt
fi

# Detecta número de CPUs disponíveis
CPUS=$(nproc)
WORKERS=${WORKERS:-$((CPUS * 2))}

# Configurações de performance
export PYTHONUNBUFFERED=1

echo "Iniciando servidor com $WORKERS workers..."

# Inicia Gunicorn com configurações otimizadas
exec gunicorn main:app \
    --config /app/gunicorn_conf.py \
    --workers $WORKERS \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8083 \
    --worker-connections ${WORKER_CONNECTIONS:-2000} \
    --max-requests ${MAX_REQUESTS:-10000} \
    --max-requests-jitter ${MAX_REQUESTS_JITTER:-1000} \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    --log-level ${LOG_LEVEL:-info} \
    --graceful-timeout 30 \
    --limit-request-line 0 \
    --limit-request-field_size 0
