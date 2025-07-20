# Tasks Management API

API para gerenciamento e monitoramento de tasks do Celery no sistema de tiles.

## Endpoints Disponíveis

### 1. Listar Tasks
**GET** `/api/tasks/list`

Lista todas as tasks com seus status e posição na fila.

**Resposta:**
```json
{
  "active": [
    {
      "task_id": "3b32780e-eaab-4742-87d9-5c96c4a7e0f1",
      "name": "app.tasks.cache_tasks.cache_point_async",
      "state": "ACTIVE",
      "args": ["2_teste_upload_pontos"],
      "kwargs": {},
      "worker": "celery@workstation02"
    }
  ],
  "scheduled": [],
  "reserved": [],
  "stats": {
    "total_active": 1,
    "total_scheduled": 0,
    "total_reserved": 0,
    "total_pending": 0,
    "workers": ["celery@workstation02"]
  }
}
```

### 2. Status de Task Específica
**GET** `/api/tasks/status/{task_id}`

Obtém o status detalhado de uma task específica.

**Parâmetros:**
- `task_id`: ID da task

**Resposta:**
```json
{
  "task_id": "3b32780e-eaab-4742-87d9-5c96c4a7e0f1",
  "state": "SUCCESS",
  "ready": true,
  "successful": true,
  "failed": false,
  "result": {
    "status": "completed",
    "point_id": "2_teste_upload_pontos",
    "total_tiles": 45,
    "successful_tiles": 45,
    "failed_tiles": 0
  }
}
```

### 3. Estatísticas dos Workers
**GET** `/api/tasks/workers`

Obtém estatísticas sobre os workers do Celery.

**Resposta:**
```json
{
  "workers": {
    "celery@workstation02": {
      "stats": {
        "total": 1234,
        "pool": {
          "max-concurrency": 8,
          "processes": [123, 124, 125, 126, 127, 128, 129, 130],
          "max-tasks-per-child": 1000
        }
      },
      "active_tasks": 1,
      "scheduled_tasks": 0,
      "reserved_tasks": 2
    }
  },
  "total_workers": 1,
  "active_tasks": 1,
  "scheduled_tasks": 0,
  "reserved_tasks": 2
}
```

### 4. Tasks Registradas
**GET** `/api/tasks/registered`

Lista todas as tasks registradas no Celery, organizadas por categoria.

**Resposta:**
```json
{
  "total": 10,
  "tasks": [
    "app.tasks.cache_tasks.cache_point_async",
    "app.tasks.cache_tasks.cache_campaign_async",
    "app.tasks.cache_tasks.get_cache_status",
    "app.tasks.tasks.process_landsat_tile",
    "app.tasks.tasks.process_sentinel_tile"
  ],
  "categories": {
    "cache": [
      "app.tasks.cache_tasks.cache_point_async",
      "app.tasks.cache_tasks.cache_campaign_async",
      "app.tasks.cache_tasks.get_cache_status"
    ],
    "tile": [
      "app.tasks.tasks.process_landsat_tile",
      "app.tasks.tasks.process_sentinel_tile"
    ],
    "timeseries": [],
    "warmup": [],
    "other": []
  }
}
```

### 5. Tamanho das Filas
**GET** `/api/tasks/queue-length`

Obtém o comprimento das filas do Celery.

**Resposta:**
```json
{
  "queues": {
    "celery": 5,
    "celery.priority.high": 0,
    "celery.priority.low": 2
  },
  "total_tasks": 7
}
```

### 6. Limpar Filas
**POST** `/api/tasks/purge`

Remove tasks da fila. **CUIDADO: Esta operação é irreversível!**

**Parâmetros Query:**
- `queue_name` (opcional): Nome da fila a ser limpa
- `state` (opcional): Estado das tasks a serem removidas

**Resposta:**
```json
{
  "status": "purged",
  "message": "Removed 5 tasks from all queues"
}
```

## Estados das Tasks

- **PENDING**: Task não encontrada ou ainda não submetida
- **ACTIVE**: Task sendo executada atualmente
- **SCHEDULED**: Task agendada para execução futura
- **RESERVED**: Task reservada por um worker mas ainda não iniciada
- **SUCCESS**: Task concluída com sucesso
- **FAILURE**: Task falhou
- **RETRY**: Task será reexecutada
- **REVOKED**: Task foi cancelada

## Exemplos de Uso

### Monitorar progresso de cache de pontos:
```bash
# Listar todas as tasks ativas
curl http://localhost:8083/api/tasks/list

# Verificar status específico
curl http://localhost:8083/api/tasks/status/3b32780e-eaab-4742-87d9-5c96c4a7e0f1

# Ver estatísticas dos workers
curl http://localhost:8083/api/tasks/workers
```

### Gerenciar filas:
```bash
# Ver tamanho das filas
curl http://localhost:8083/api/tasks/queue-length

# Limpar todas as filas (CUIDADO!)
curl -X POST http://localhost:8083/api/tasks/purge
```