# Documentação das APIs de Otimização de Tiles

## Visão Geral

Este sistema fornece APIs otimizadas para carregamento eficiente de tiles de imagens de satélite, especialmente quando lidando com grandes volumes de dados (35 anos de imagens Landsat/Sentinel).

## APIs Disponíveis

### 1. Viewport API - Carregamento Inteligente

**Endpoint**: `POST /api/viewport/tiles`

**Descrição**: Carrega apenas os tiles visíveis na tela do usuário, priorizando o ano selecionado.

**Quando usar**: 
- Ao inicializar o mapa
- Quando o usuário mover o mapa (pan/zoom)
- Ao trocar o ano visualizado

**Exemplo de Requisição**:
```json
{
  "viewport": {
    "north": -10.0,
    "south": -15.0,
    "east": -45.0,
    "west": -50.0
  },
  "zoom": 10,
  "years": [2020, 2021, 2022, 2023],
  "layer": "landsat",
  "priority_year": 2023
}
```

**Benefícios**:
- ✅ Reduz carga inicial de ~15.000 para ~100 tiles
- ✅ Carrega ano atual primeiro
- ✅ Interface responsiva imediata

---

### 2. Megatiles API - Agregação de Tiles

**Endpoint**: `GET /api/megatile/{layer}/{x}/{y}/{z}`

**Descrição**: Combina múltiplos tiles em uma única imagem PNG.

**Quando usar**:
- Para visualizar múltiplos anos simultaneamente
- Em zooms menores (visão geral)
- Para reduzir requisições HTTP

**Exemplo**:
```
GET /api/megatile/landsat/2954/5123/13?years=2020,2021,2022,2023&size=4
```

**Resultado**: Uma imagem 1024x4096px contendo 16 tiles por ano (4x4 grid)

**Benefícios**:
- ✅ 1 requisição em vez de 64
- ✅ Cache eficiente
- ✅ Menor latência total

---

### 3. WebSocket API - Streaming em Tempo Real

**Endpoint**: `WS /ws/tiles/{client_id}`

**Descrição**: Conexão persistente para receber tiles conforme disponibilidade.

**Quando usar**:
- Carregamento progressivo de grandes áreas
- Animações temporais
- Aplicações em tempo real

**Protocolo de Comunicação**:

1. **Cliente se inscreve**:
```json
{
  "type": "subscribe",
  "tiles": [
    {"x": 2954, "y": 5123, "z": 13, "year": 2023, "layer": "landsat"}
  ]
}
```

2. **Servidor confirma**:
```json
{
  "type": "subscribed",
  "count": 420,
  "status": "processing"
}
```

3. **Cliente recebe tiles**:
```json
{
  "type": "tile",
  "x": 2954,
  "y": 5123,
  "z": 13,
  "year": 2023,
  "data": "base64_encoded_png..."
}
```

**Benefícios**:
- ✅ Sem polling
- ✅ Cancelamento de requisições
- ✅ Priorização dinâmica

---

### 4. Progressive Loading API

**Endpoint**: `POST /api/viewport/progressive`

**Descrição**: Retorna estratégias otimizadas de carregamento baseadas no contexto.

**Quando usar**:
- Para determinar melhor ordem de carregamento
- Otimizar para animação vs visualização estática

**Exemplo de Resposta**:
```json
[
  {
    "name": "animation_optimized",
    "description": "Carrega anos em sequência para animação suave",
    "load_order": [2023, 2024, 2025, 2022, 2021]
  },
  {
    "name": "resolution_progressive",
    "steps": [
      {"zoom": 11, "quality": "preview"},
      {"zoom": 12, "quality": "medium"},
      {"zoom": 13, "quality": "full"}
    ]
  }
]
```

---

### 5. Batch Processing API

**Endpoint**: `POST /api/stream/batch`

**Descrição**: Processa lotes de tiles com estratégias específicas.

**Estratégias disponíveis**:
- `viewport_optimized`: Prioriza tiles visíveis
- `temporal_sequence`: Otimiza para animação
- `spatial_cluster`: Agrupa tiles próximos

**Exemplo**:
```json
{
  "tiles": [...],
  "strategy": "viewport_optimized"
}
```

---

## Fluxo Recomendado de Uso

### 1. Inicialização do Mapa
```javascript
// 1. Detecta viewport inicial
const viewport = map.getBounds();

// 2. Solicita tiles otimizados
const tiles = await fetch('/api/viewport/tiles', {
  method: 'POST',
  body: JSON.stringify({
    viewport: viewport,
    zoom: map.getZoom(),
    years: selectedYears,
    priority_year: currentYear
  })
});

// 3. Conecta WebSocket para updates
const ws = new WebSocket(`/ws/tiles/${clientId}`);
```

### 2. Mudança de Ano
```javascript
// Re-prioriza tiles para novo ano
ws.send(JSON.stringify({
  type: 'prioritize',
  year: newYear
}));
```

### 3. Animação Temporal
```javascript
// Solicita estratégia otimizada
const strategy = await fetch('/api/viewport/progressive', {
  method: 'POST',
  body: JSON.stringify({
    viewport: viewport,
    zoom: zoom,
    years: years,
    current_view: {
      year: currentYear,
      animation_playing: true
    }
  })
});
```

## Códigos de Status HTTP

- `200 OK`: Requisição bem-sucedida
- `400 Bad Request`: Parâmetros inválidos
- `404 Not Found`: Tile não encontrado
- `429 Too Many Requests`: Rate limit excedido
- `500 Internal Server Error`: Erro no servidor
- `503 Service Unavailable`: Serviço temporariamente indisponível

## Headers Importantes

### Response Headers
- `X-Cache`: `HIT` ou `MISS` - Indica se veio do cache
- `X-Megatile`: `true` - Indica tile agregado
- `X-Priority`: `0-10` - Prioridade do tile
- `X-Rate-Limit-Remaining`: Requisições restantes

## Limites e Restrições

- **Viewport**: Máximo 1000 tiles por requisição
- **Megatile**: Tamanho máximo 8x8 tiles
- **WebSocket**: Máximo 10.000 inscrições por cliente
- **Rate Limits**: 
  - Tiles: 100.000/min
  - Landsat/Sentinel: 50.000/min
  - WebSocket: 1.000 mensagens/min

## Exemplos de Código

### JavaScript/TypeScript
```typescript
interface Viewport {
  north: number;
  south: number;
  east: number;
  west: number;
}

async function loadTiles(viewport: Viewport, year: number) {
  const response = await fetch('/api/viewport/tiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      viewport,
      zoom: 13,
      years: [2020, 2021, 2022, 2023],
      priority_year: year
    })
  });
  
  return response.json();
}
```

### Python
```python
import requests
import asyncio
import websockets

# Viewport API
def get_viewport_tiles(viewport, zoom, years, priority_year=None):
    response = requests.post(
        'http://api.example.com/api/viewport/tiles',
        json={
            'viewport': viewport,
            'zoom': zoom,
            'years': years,
            'priority_year': priority_year
        }
    )
    return response.json()

# WebSocket
async def stream_tiles(client_id):
    uri = f"ws://api.example.com/ws/tiles/{client_id}"
    async with websockets.connect(uri) as websocket:
        # Subscribe
        await websocket.send(json.dumps({
            'type': 'subscribe',
            'tiles': tiles_list
        }))
        
        # Receive tiles
        async for message in websocket:
            tile = json.loads(message)
            process_tile(tile)
```

## Troubleshooting

### Problema: "Too many requests"
**Solução**: Use viewport API em vez de carregar todos os tiles

### Problema: Tiles carregando lentamente
**Solução**: 
1. Verifique priorização (priority_year)
2. Use megatiles para zooms menores
3. Conecte WebSocket para streaming

### Problema: Animação travando
**Solução**: Use estratégia `temporal_sequence` no batch processing

## Métricas de Performance

Com as APIs otimizadas, você pode esperar:

- **Tempo inicial**: ~2s (vs ~30s anterior)
- **Tiles/segundo**: ~1000 (com cache)
- **Latência WebSocket**: <50ms
- **Taxa de cache hit**: >80%

## Suporte

Para dúvidas ou problemas:
- Verifique os logs em `/api/metrics`
- Status da fila: `/api/stats`
- Documentação Swagger: `/docs`