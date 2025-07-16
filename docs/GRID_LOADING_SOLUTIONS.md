# Soluções para Carregamento em Grid de 35 Anos de Imagens

## Problema Identificado
Usuários carregam em uma grid todos os 35 anos de imagens de uma única vez, resultando em milhares de requisições simultâneas que causam erros "too many requests" mesmo com rate limiting aumentado.

## Soluções Implementadas

### 1. **Viewport-Based Lazy Loading** (`/app/api/viewport.py`)
- **Endpoint**: `/api/viewport/tiles`
- **Funcionalidade**: Carrega apenas tiles visíveis no viewport atual
- **Priorização**: 
  - Ano em foco: prioridade 0 (máxima)
  - Anos adjacentes (±1): prioridade 1
  - Outros anos: prioridade crescente baseada na distância
- **Benefício**: Reduz carga inicial de milhares para dezenas de requisições

### 2. **WebSocket Streaming** (`/app/api/websocket_tiles.py`)
- **Endpoint**: `/ws/tiles/{client_id}`
- **Funcionalidade**: Stream contínuo de tiles via WebSocket
- **Protocolo**:
  - Cliente se inscreve para tiles específicos
  - Servidor envia tiles conforme disponibilidade
  - Re-priorização dinâmica baseada em mudanças de viewport/ano
- **Benefício**: Elimina polling, reduz latência, permite cancelamento

### 3. **Tile Aggregation - Megatiles** (`/app/api/tile_aggregation.py`)
- **Endpoint**: `/api/megatile/{layer}/{x}/{y}/{z}`
- **Funcionalidade**: Combina múltiplos tiles em uma única imagem
- **Exemplo**: Grid 4x4 = 1 requisição em vez de 16
- **Cache**: Megatiles são cacheados para reuso
- **Benefício**: Redução drástica no número de requisições HTTP

### 4. **Priority Request Queue** (`/app/request_queue.py`)
- **Funcionalidade**: Fila com priorização inteligente
- **Limites**: 
  - Max concurrent: 100 requisições
  - Rate limit: 1000 req/s
- **Re-priorização**: Dinâmica baseada em ano selecionado
- **Benefício**: Garante que tiles importantes sejam carregados primeiro

## Estratégias de Carregamento

### Para Visualização Estática
1. Carrega ano atual em alta prioridade
2. Pre-carrega anos adjacentes (±5 anos)
3. Carrega resto em background

### Para Animação Temporal
1. Carrega sequência de anos na ordem da animação
2. Pre-carrega próximos 5 frames
3. Mantém buffer para playback suave

### Progressive Enhancement
1. Carrega preview em baixa resolução (zoom-2)
2. Refina para resolução média (zoom-1)
3. Finaliza com resolução completa

## Integração com Frontend

### Exemplo de Uso - Viewport Loading
```javascript
// Detecta viewport e solicita apenas tiles visíveis
const viewport = map.getBounds();
const response = await fetch('/api/viewport/tiles', {
  method: 'POST',
  body: JSON.stringify({
    viewport: {
      north: viewport.getNorth(),
      south: viewport.getSouth(),
      east: viewport.getEast(),
      west: viewport.getWest()
    },
    zoom: map.getZoom(),
    years: [2020, 2021, 2022, 2023],
    priority_year: currentYear
  })
});
```

### Exemplo de Uso - WebSocket
```javascript
// Conecta e recebe tiles via streaming
const ws = new WebSocket(`/ws/tiles/${clientId}`);

ws.send(JSON.stringify({
  type: 'subscribe',
  tiles: tilesNeeded,
  priorities: calculatePriorities(tilesNeeded)
}));

ws.onmessage = (event) => {
  const tile = JSON.parse(event.data);
  renderTile(tile);
};
```

### Exemplo de Uso - Megatiles
```javascript
// Solicita megatile para múltiplos anos
const megatileUrl = `/api/megatile/landsat/${x}/${y}/${z}?years=2020,2021,2022,2023&size=4`;
const img = new Image();
img.src = megatileUrl;
// Recorta localmente conforme necessário
```

## Benefícios Esperados

1. **Redução de Requisições**: De ~420 tiles/ano para ~10-20 tiles visíveis
2. **Tempo de Carregamento**: De minutos para segundos para visualização inicial
3. **Experiência do Usuário**: Interface responsiva com carregamento progressivo
4. **Eficiência de Cache**: Megatiles e priorização aumentam hit rate
5. **Escalabilidade**: Sistema pode lidar com múltiplos usuários simultâneos

## Próximos Passos

1. Implementar frontend adaptado para usar as novas APIs
2. Configurar cache de megatiles no Redis/S3
3. Ajustar tamanhos de megatile baseado em análise de uso
4. Implementar analytics para otimizar estratégias de pre-loading
5. Adicionar compressão WebP para reduzir tamanho de transferência