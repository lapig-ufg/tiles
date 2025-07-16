#!/bin/bash

# Script para inicializar Gunicorn + Celery
echo "Iniciando serviços..."

# Função para cleanup
cleanup() {
    echo "Parando serviços..."
    kill $GUNICORN_PID $CELERY_PID 2>/dev/null
    exit 0
}

# Trap para capturar sinais
trap cleanup SIGINT SIGTERM

# Inicia Celery Worker em background
echo "Iniciando Celery Worker..."
celery -A app.celery_app worker \
    --loglevel=info \
    --concurrency=8 \
    --max-tasks-per-child=1000 \
    --pool=prefork \
    --without-gossip \
    --without-mingle \
    --without-heartbeat &
CELERY_PID=$!

# Aguarda um pouco para o Celery inicializar
sleep 2

# Inicia Gunicorn
echo "Iniciando Gunicorn..."
gunicorn main:app \
    -w 36 \
    -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8080 \
    --worker-connections 2000 \
    --max-requests 100000 \
    --max-requests-jitter 10000 \
    --timeout 300 \
    --backlog 4096 \
    --preload &
GUNICORN_PID=$!

echo "Serviços iniciados:"
echo "- Gunicorn PID: $GUNICORN_PID"
echo "- Celery PID: $CELERY_PID"

# Aguarda os processos
wait $GUNICORN_PID $CELERY_PID