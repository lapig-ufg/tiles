# Sistema de Cache Warming para Tiles

## Visão Geral

O sistema de cache warming simula requisições de usuários em webmaps (Leaflet/OpenLayers) para pré-carregar tiles populares no cache, melhorando significativamente a performance para usuários finais.

## Arquitetura

### Componentes Principais

1. **Cache Warmer** (`app/cache_warmer.py`)
   - Gera coordenadas de tiles inteligentemente
   - Simula padrões de carregamento de webmaps
   - Prioriza regiões populares brasileiras

2. **API de Gerenciamento** (`app/api/cache_management.py`)
   - Endpoints REST para controle do cache
   - Simulação de navegação de usuários
   - Análise de padrões de uso

3. **Tasks Celery** (`app/celery_app.py`)
   - Processamento assíncrono em lotes
   - Agendamento periódico via Celery Beat
   - Rate limiting automático

## Padrões de Carregamento

### 1. Spiral (Leaflet)
Carrega tiles do centro para fora em espiral, simulando o comportamento padrão do Leaflet.

### 2. Grid (OpenLayers)
Carrega tiles em grade com buffer ao redor, simulando o comportamento do OpenLayers.

### 3. Viewport
Carrega apenas tiles visíveis no viewport atual.

### 4. Predictive
Carrega tiles baseado em previsão de movimento (futuro).

## Regiões Prioritárias

O sistema prioriza automaticamente as principais regiões brasileiras:
- São Paulo
- Rio de Janeiro
- Brasília
- Salvador
- Porto Alegre

## API Endpoints

### POST /api/cache/warmup
Inicia processo de aquecimento de cache.

```bash
curl -X POST http://localhost:8000/api/cache/warmup \
  -H "Content-Type: application/json" \
  -d '{
    "layer": "landsat",
    "params": {"bands": ["B4", "B3", "B2"]},
    "max_tiles": 1000,
    "batch_size": 50
  }'
```

### POST /api/cache/simulate-navigation
Simula navegação de usuário no mapa.

```bash
curl -X POST http://localhost:8000/api/cache/simulate-navigation \
  -H "Content-Type: application/json" \
  -d '{
    "start_lat": -23.5505,
    "start_lon": -46.6333,
    "zoom_levels": [10, 11, 12],
    "movement_pattern": "random",
    "duration_seconds": 60
  }'
```

### GET /api/cache/status
Retorna status e métricas do cache.

### GET /api/cache/recommendations
Fornece recomendações de otimização.

## CLI de Gerenciamento

### Instalação
```bash
pip install click rich
```

### Comandos Disponíveis

```bash
# Aquecer cache
./scripts/cache_warmer_cli.py warmup -l landsat -m 1000

# Simular navegação
./scripts/cache_warmer_cli.py simulate --lat -23.5505 --lon -46.6333 -z 10 -z 11

# Ver status
./scripts/cache_warmer_cli.py status

# Ver recomendações
./scripts/cache_warmer_cli.py recommendations

# Analisar padrões
./scripts/cache_warmer_cli.py analyze -d 7

# Verificar task
./scripts/cache_warmer_cli.py check <task_id>
```

## Configuração do Celery

### Worker
```bash
celery -A app.celery_app worker --loglevel=info
```

### Beat (Scheduler)
```bash
celery -A app.celery_app beat --loglevel=info
```

### Tarefas Agendadas

1. **Aquecimento Diário (2 AM)**
   - Pré-carrega tiles de regiões populares
   - 1000 tiles por execução

2. **Análise Semanal (Segunda 3 AM)**
   - Analisa padrões de uso
   - Gera recomendações de otimização

## Configurações

### Prioridades de Zoom
```python
{
    10: 10,  # Máxima prioridade
    11: 9,
    12: 8,
    13: 7,
    14: 6,
    9: 5,
    15: 4,
    8: 3,
    16: 2,
    7: 1    # Mínima prioridade
}
```

### Rate Limiting
- Landsat tiles: 100/minuto
- Sentinel tiles: 100/minuto
- Cache warming: 200/minuto

## Monitoramento

### Logs
```bash
# Ver logs do worker
tail -f celery_worker.log

# Ver logs do beat
tail -f celery_beat.log
```

### Métricas
- Total de tiles em cache
- Taxa de acerto (hit rate)
- Tiles mais populares
- Tasks ativas

## Otimizações

### 1. Batch Processing
Tiles são processados em lotes para melhor performance.

### 2. Priorização Inteligente
Tiles de zooms e regiões mais utilizados são priorizados.

### 3. Cache Híbrido
Utiliza cache em memória e disco para máxima eficiência.

## Troubleshooting

### Tasks não reconhecidas
Certifique-se de que o Celery está usando `app.celery_app`:
```bash
celery -A app.celery_app worker
```

### Warning de deprecação
A configuração `broker_connection_retry_on_startup=True` já foi adicionada.

### Cache não está sendo preenchido
1. Verifique se o worker está rodando
2. Verifique logs de erro
3. Confirme conectividade com Redis/Valkey

## Exemplo de Uso Completo

```bash
# 1. Iniciar worker
celery -A app.celery_app worker --loglevel=info &

# 2. Iniciar beat (opcional para agendamento)
celery -A app.celery_app beat --loglevel=info &

# 3. Aquecer cache para São Paulo
./scripts/cache_warmer_cli.py warmup -l landsat -m 500

# 4. Verificar status
./scripts/cache_warmer_cli.py status

# 5. Simular usuário navegando
./scripts/cache_warmer_cli.py simulate --lat -23.5505 --lon -46.6333 -z 10 -z 11 -z 12
```