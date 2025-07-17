#!/bin/bash

# Script para inicializar Celery Beat (scheduler)
echo "Iniciando Celery Beat..."

# Configurações
CELERY_APP="app.celery_app"
LOG_LEVEL="info"
SCHEDULE_FILE="/tmp/celerybeat-schedule"

# Remove arquivo de schedule antigo se existir
if [ -f "$SCHEDULE_FILE" ]; then
    echo "Removendo arquivo de schedule antigo..."
    rm -f "$SCHEDULE_FILE"
fi

# Inicia Celery Beat
celery -A $CELERY_APP beat \
    --loglevel=$LOG_LEVEL \
    --schedule=$SCHEDULE_FILE

echo "Celery Beat finalizado."