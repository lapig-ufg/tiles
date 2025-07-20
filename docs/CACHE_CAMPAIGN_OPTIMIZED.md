# Cache Otimizado para Campanhas

## Visão Geral

O sistema de cache otimizado para campanhas foi desenvolvido para processar grandes volumes de pontos (até 90.000+) de forma eficiente, reduzindo o tempo de processamento em até 16x.

## Principais Otimizações

### 1. **Agrupamento em Grid**
- Tiles próximos são agrupados em grades de 4x4 ou 8x8
- Uma única requisição ao GEE pode gerar até 16 tiles
- Reduz drasticamente o número de chamadas à API do Google Earth Engine

### 2. **Priorização Inteligente**
- Anos recentes são processados primeiro
- Zoom levels mais utilizados (12-13) têm prioridade
- Pontos marcados com `enhance_in_cache=1` são priorizados

### 3. **Limites Otimizados do GEE**
- **Requisições simultâneas**: 25 (anteriormente 10)
- **Delay entre requisições**: 50ms (anteriormente 100ms)
- **Circuit breaker**: Detecta rate limiting e ajusta automaticamente
- **Backoff exponencial**: Em caso de erro 429

### 4. **Melhorias no Celery**
- Filas prioritárias para campanhas urgentes
- Batch size dinâmico baseado no tamanho da campanha
- Melhor coordenação com `chord` para callbacks eficientes

## Como Usar

### Endpoint Unificado

```bash
POST /api/cache/campaign/start
```

### Parâmetros

```json
{
  "campaign_id": "string",
  "batch_size": 50,           // opcional, padrão: 50
  "use_grid": true,           // opcional, padrão: true
  "priority_recent_years": true // opcional, padrão: true
}
```

### Exemplo de Requisição

```bash
curl -X POST https://api.exemplo.com/api/cache/campaign/start \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "campaign_id": "campaign_2024_01",
    "batch_size": 100,
    "use_grid": true,
    "priority_recent_years": true
  }'
```

### Resposta de Exemplo

```json
{
  "status": "started",
  "message": "Optimized cache task started for campaign campaign_2024_01",
  "data": {
    "task_id": "550e8400-e29b-41d4-a716-446655440000",
    "campaign_id": "campaign_2024_01",
    "point_count": 50000,
    "batch_size": 100,
    "optimization": {
      "grid_mode": true,
      "priority_recent_years": true,
      "estimated_tiles": 1500000,
      "estimated_gee_requests": 93750,
      "estimated_time_minutes": 78.13,
      "speedup_factor": "16x"
    }
  },
  "timestamp": "2024-01-15T10:30:00Z"
}
```

## Estimativas de Tempo

### Campanha Pequena (< 1.000 pontos)
- **Método antigo**: ~3 horas
- **Método otimizado**: ~15 minutos

### Campanha Média (1.000 - 10.000 pontos)
- **Método antigo**: ~8-10 horas
- **Método otimizado**: ~30-60 minutos

### Campanha Grande (10.000 - 90.000 pontos)
- **Método antigo**: ~24-48 horas
- **Método otimizado**: ~2-4 horas

## Monitoramento

### Verificar Status da Campanha

```bash
GET /api/cache/campaign/{campaign_id}/status
```

### Verificar Status da Task

```bash
GET /api/cache/tasks/{task_id}
```

## Configurações Avançadas

### Ajustar Batch Size

- **Campanhas pequenas**: Use batch_size menor (25-50)
- **Campanhas grandes**: Use batch_size maior (100-200)

### Desativar Grid (não recomendado)

```json
{
  "use_grid": false
}
```

### Processar Todos os Anos Igualmente

```json
{
  "priority_recent_years": false
}
```

## Troubleshooting

### Erro 429 (Rate Limiting)

O sistema possui circuit breaker automático. Se ocorrer:
1. A task fará retry automático com backoff exponencial
2. O processamento continuará após o período de cooldown

### Task Travada

1. Verifique o status da task
2. Verifique os logs do Celery worker
3. Se necessário, cancele e reinicie a task

### Performance Abaixo do Esperado

1. Verifique se o grid está ativado (`use_grid: true`)
2. Aumente o batch_size se possível
3. Verifique a carga dos workers Celery

## Comparação: Método Antigo vs Otimizado

| Aspecto | Método Antigo | Método Otimizado |
|---------|---------------|------------------|
| Requisições GEE/tile | 1 | 0.0625 (1/16) |
| Processamento | Serial | Paralelo com grid |
| Priorização | Nenhuma | Anos recentes primeiro |
| Rate limiting | Fixo | Dinâmico com circuit breaker |
| Batch size | Fixo (5) | Dinâmico (25-200) |
| Tempo médio | 24h para 50k pontos | 2h para 50k pontos |

## Limitações

- Máximo de 200 pontos por batch para evitar timeout
- Grid máximo de 8x8 para manter qualidade
- Requer autenticação super-admin
- Não processa campanhas já em andamento

## Próximos Passos

1. Implementar compressão WebP para reduzir armazenamento
2. Adicionar suporte para processar apenas tiles modificados
3. Implementar cache distribuído entre múltiplas regiões
4. Adicionar métricas detalhadas de performance