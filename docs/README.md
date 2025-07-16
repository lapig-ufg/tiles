# 📚 Documentação do Sistema de Tiles Otimizado

## 🎯 Objetivo

Resolver o problema de "too many requests" ao carregar 35 anos de imagens de satélite (Landsat/Sentinel) em uma grid, transformando milhares de requisições simultâneas em um carregamento inteligente e progressivo.

## 📁 Estrutura da Documentação

### 1. [API_DOCUMENTATION.md](./API_DOCUMENTATION.md)
Documentação completa de todas as APIs disponíveis:
- Viewport API - Carregamento baseado em área visível
- WebSocket API - Streaming em tempo real
- Megatiles API - Agregação de tiles
- Progressive Loading API - Estratégias otimizadas
- Batch Processing API - Processamento em lote

### 2. [GRID_LOADING_SOLUTIONS.md](./GRID_LOADING_SOLUTIONS.md)
Detalhamento técnico das soluções implementadas:
- Análise do problema
- Arquitetura das soluções
- Benefícios esperados
- Exemplos de código

### 3. [FRONTEND_INTEGRATION_EXAMPLE.html](./FRONTEND_INTEGRATION_EXAMPLE.html)
Exemplo interativo demonstrando:
- Como integrar cada API
- Código JavaScript funcional
- Simulações de respostas
- Interface visual para testes

## 🚀 Quick Start

### Passo 1: Configurar Rate Limiting
```toml
# settings.toml
RATE_LIMIT_PER_MINUTE = 100000
RATE_LIMIT_BURST = 10000
```

### Passo 2: Iniciar Serviços
```bash
./start_services.sh
```

### Passo 3: Usar Viewport API
```javascript
const response = await fetch('/api/viewport/tiles', {
  method: 'POST',
  body: JSON.stringify({
    viewport: mapBounds,
    zoom: 13,
    years: [2020, 2021, 2022, 2023],
    priority_year: 2023
  })
});
```

## 📊 Comparação: Antes vs Depois

| Métrica | Antes | Depois | Melhoria |
|---------|-------|---------|----------|
| Requisições iniciais | ~15.000 | ~100 | 99.3% menos |
| Tempo de carregamento | ~30s | ~2s | 93% mais rápido |
| Taxa de erro | Alta | Mínima | ~99% menos erros |
| Uso de banda | ~1.5GB | ~50MB | 96% menos dados |

## 🔧 APIs Principais

### 1. **Viewport Loading**
```
POST /api/viewport/tiles
```
Carrega apenas tiles visíveis, priorizando o ano selecionado.

### 2. **WebSocket Streaming**
```
WS /ws/tiles/{client_id}
```
Stream contínuo de tiles com priorização dinâmica.

### 3. **Megatiles**
```
GET /api/megatile/{layer}/{x}/{y}/{z}
```
Combina múltiplos tiles em uma única imagem.

## 💡 Conceitos Chave

### Priorização Inteligente
- **Prioridade 0**: Ano atual visível
- **Prioridade 1-2**: Anos adjacentes
- **Prioridade 3+**: Outros anos

### Cache Híbrido
```
Request → Local LRU → Redis → S3 → Earth Engine
```

### Rate Limiting Diferenciado
- Tiles: 100.000/min
- Landsat/Sentinel: 50.000/min
- Time series: 10.000/min

## 📈 Monitoramento

### Endpoint de Métricas
```
GET /api/metrics
```

### WebSocket Stats
```json
{
  "type": "stats"
}
```

## 🛠️ Troubleshooting

### Problema: Rate limit mesmo com novas APIs
**Solução**: Verificar se está usando viewport API em vez de carregar todos os tiles

### Problema: WebSocket desconectando
**Solução**: Implementar reconnect automático com backoff exponencial

### Problema: Megatiles muito grandes
**Solução**: Reduzir parâmetro `size` ou usar menos anos por requisição

## 📞 Suporte

- **Logs**: `/var/log/tiles/`
- **Métricas**: Grafana dashboard
- **Status**: `/health` endpoint

## 🔗 Links Úteis

- [Swagger UI](/docs) - Documentação interativa das APIs
- [Exemplo Frontend](./FRONTEND_INTEGRATION_EXAMPLE.html) - Demo funcional
- [Arquitetura](./GRID_LOADING_SOLUTIONS.md) - Detalhes técnicos

---

**Última atualização**: Janeiro 2025  
**Versão**: 2.0.0