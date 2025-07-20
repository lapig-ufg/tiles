# Cache Campaign API Documentation

API para gerenciamento de cache por campanhas, com acompanhamento de progresso individual de pontos.

## Visão Geral

O sistema de cache por campanha permite processar múltiplos pontos de forma assíncrona, atualizando o status de cada ponto conforme são cacheados e mantendo um progresso geral da campanha.

## Fluxo de Processamento

1. **Início da Campanha**: Quando uma campanha é iniciada, o sistema:
   - Verifica quais pontos ainda não foram cacheados
   - Atualiza o status da campanha no MongoDB
   - Cria tarefas individuais para cada ponto
   - Cada tarefa de ponto é encadeada com uma callback para atualizar o progresso

2. **Processamento de Pontos**: Para cada ponto:
   - Gera tiles para zoom levels 12, 13, 14
   - Processa todos os anos e parâmetros de visualização
   - Atualiza o status do ponto como `cached: true` ao concluir
   - Executa callback para atualizar progresso da campanha

3. **Atualização de Progresso**: Após cada ponto cacheado:
   - Conta total de pontos cacheados na campanha
   - Calcula percentual de conclusão
   - Atualiza campos de progresso no MongoDB
   - Marca campanha como completa quando todos os pontos estão cacheados

## Endpoints

### Iniciar Cache de Campanha

**POST** `/api/cache/campaign/start`

```json
{
  "campaign_id": "teste_upload_pontos",
  "batch_size": 5
}
```

**Resposta:**
```json
{
  "status": "scheduled",
  "message": "Scheduled 10 cache tasks",
  "data": {
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "campaign_id": "teste_upload_pontos",
    "total_points": 15,
    "already_cached": 5,
    "points_to_cache": 10,
    "group_id": "group-123"
  }
}
```

### Verificar Status da Campanha

**GET** `/api/cache/campaign/{campaign_id}/status`

**Resposta:**
```json
{
  "status": "success",
  "message": "Cache status for campaign teste_upload_pontos",
  "data": {
    "campaign_id": "teste_upload_pontos",
    "total_points": 15,
    "cached_points": 8,
    "cache_percentage": 53.33,
    "caching_in_progress": true,
    "caching_started_at": "2025-07-19T10:30:00",
    "last_point_cached_at": "2025-07-19T10:35:30",
    "points_to_cache": 10,
    "all_points_cached": false
  }
}
```

## Campos de Status no MongoDB

### Collection: campaign

```javascript
{
  "_id": "teste_upload_pontos",
  // ... outros campos da campanha
  
  // Campos de progresso do cache:
  "caching_in_progress": true,           // Se está cacheando atualmente
  "caching_started_at": ISODate(),       // Quando iniciou o cache
  "total_points": 15,                    // Total de pontos na campanha
  "points_to_cache": 10,                 // Quantos pontos serão cacheados nesta execução
  "cached_points": 8,                    // Quantos já foram cacheados
  "cache_percentage": 53.33,             // Percentual concluído
  "last_point_cached_at": ISODate(),     // Último ponto cacheado
  "caching_completed_at": ISODate(),     // Quando concluiu (se aplicável)
  "all_points_cached": false,            // Se todos os pontos estão cacheados
  "caching_error": "error message",      // Erro se houver
  "caching_error_at": ISODate()         // Quando ocorreu o erro
}
```

### Collection: points

```javascript
{
  "_id": "1_teste_upload_pontos",
  "campaign": "teste_upload_pontos",
  "lon": -47.123,
  "lat": -15.789,
  
  // Campos de cache:
  "cached": true,                        // Se o ponto está cacheado
  "cachedAt": ISODate(),                 // Quando foi cacheado
  "cachedBy": "celery-task",             // Quem/o que cacheou
  "enhance_in_cache": 1                  // Flag de cache
}
```

## Monitoramento via Celery

### Listar Tasks Ativas

**GET** `/api/tasks/list`

```json
{
  "active": [
    {
      "task_id": "abc123",
      "name": "app.tasks.cache_tasks.cache_point_async",
      "state": "ACTIVE",
      "args": ["1_teste_upload_pontos"],
      "worker": "celery@worker01"
    }
  ],
  "stats": {
    "total_active": 5,
    "total_scheduled": 3,
    "total_reserved": 2
  }
}
```

### Verificar Task Específica

**GET** `/api/tasks/status/{task_id}`

```json
{
  "task_id": "abc123",
  "state": "SUCCESS",
  "ready": true,
  "successful": true,
  "result": {
    "status": "completed",
    "point_id": "1_teste_upload_pontos",
    "total_tiles": 45,
    "successful_tiles": 45,
    "failed_tiles": 0
  }
}
```

## Exemplos de Uso

### 1. Cachear campanha completa:
```bash
# Iniciar cache
curl -X POST http://localhost:8083/api/cache/campaign/start \
  -H "Content-Type: application/json" \
  -d '{"campaign_id": "teste_upload_pontos"}'

# Verificar progresso
curl http://localhost:8083/api/cache/campaign/teste_upload_pontos/status
```

### 2. Monitorar progresso em tempo real:
```bash
# Script para monitorar progresso
while true; do
  curl -s http://localhost:8083/api/cache/campaign/teste_upload_pontos/status | jq .data.cache_percentage
  sleep 5
done
```

### 3. Verificar pontos não cacheados:
```javascript
// MongoDB query
db.points.find({
  campaign: "teste_upload_pontos",
  $or: [
    {cached: {$ne: true}},
    {cached: {$exists: false}}
  ]
}).count()
```

## Notas Importantes

1. **Idempotência**: O sistema só processa pontos não cacheados, permitindo reexecutar com segurança
2. **Paralelismo**: Múltiplos pontos são processados em paralelo usando Celery groups
3. **Progresso Real-time**: O progresso é atualizado após cada ponto concluído
4. **Recuperação de Falhas**: Se houver erro, o status é salvo e pode ser retomado
5. **Performance**: Use `batch_size` para controlar a carga no sistema