# Guia de API de Layers - Sistema de Tiles

Este documento descreve como utilizar o sistema de layers/tiles após a migração para o modelo dinâmico baseado em MongoDB.

## Índice

1. [Visão Geral](#visão-geral)
2. [Endpoints de Tiles](#endpoints-de-tiles)
3. [Sistema de Capabilities](#sistema-de-capabilities)
4. [Configuração de Vis_Params no MongoDB](#configuração-de-vis_params-no-mongodb)
5. [Cache e Performance](#cache-e-performance)
6. [Exemplos de Uso](#exemplos-de-uso)
7. [Troubleshooting](#troubleshooting)

## Visão Geral

O sistema de tiles foi refatorado para usar configurações dinâmicas armazenadas no MongoDB, permitindo:

- ✅ Adição/remoção de visualizações sem alterar código
- ✅ Configurações específicas por satélite/sensor
- ✅ Validação dinâmica de parâmetros
- ✅ Cache distribuído com FanoutCache
- ✅ Capabilities geradas automaticamente

### Arquitetura

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────┐
│   Cliente Web   │────▶│  API Tiles   │────▶│ Google Earth │
│   (Leaflet)     │     │  (FastAPI)   │     │   Engine     │
└─────────────────┘     └──────┬───────┘     └──────────────┘
                               │
                        ┌──────▼───────┐
                        │   MongoDB    │
                        │ (vis_params) │
                        └──────────────┘
```

## Endpoints de Tiles

### 1. Sentinel-2 Harmonized

```
GET /api/layers/s2_harmonized/{x}/{y}/{z}
```

**Parâmetros:**
- `x`, `y`, `z`: Coordenadas do tile (padrão XYZ/Slippy map)
- `period`: Período temporal
  - `"WET"`: Janeiro a Abril (período úmido)
  - `"DRY"`: Junho a Outubro (período seco)
  - `"MONTH"`: Mês específico (requer parâmetro `month`)
- `year`: Ano (2017 até atual)
- `month`: Mês (1-12, usado apenas quando `period="MONTH"`)
- `visparam`: Nome da visualização (ex: `"tvi-red"`, `"ndvi"`, `"rgb"`)

**Exemplo:**
```bash
curl "https://tiles.lapig.iesa.ufg.br/api/layers/s2_harmonized/2794/4592/13?period=WET&year=2024&visparam=tvi-red"
```

### 2. Landsat Collection

```
GET /api/layers/landsat/{x}/{y}/{z}
```

**Parâmetros:**
- `x`, `y`, `z`: Coordenadas do tile
- `period`: Período temporal (mesmo que Sentinel-2)
- `year`: Ano (1985 até atual)
- `month`: Mês (1-12)
- `visparam`: Nome da visualização com prefixo `landsat-` (ex: `"landsat-tvi-false"`)

**Exemplo:**
```bash
curl "https://tiles.lapig.iesa.ufg.br/api/layers/landsat/2794/4592/13?period=MONTH&year=2024&month=7&visparam=landsat-tvi-false"
```

### Limites de Zoom

- **Mínimo**: 6
- **Máximo**: 18

## Sistema de Capabilities

O endpoint de capabilities fornece informações dinâmicas sobre as coleções e visualizações disponíveis:

### 1. Obter Capabilities

```
GET /api/capabilities/
```

**Resposta:**
```json
{
  "collections": [
    {
      "name": "s2_harmonized",
      "display_name": "Sentinel-2 Harmonized",
      "satellite": "sentinel",
      "visparam": ["tvi-red", "ndvi", "rgb", "agriculture"],
      "visparam_details": [
        {
          "name": "tvi-red",
          "display_name": "TVI Red",
          "description": "Índice de Vegetação Triangular",
          "tags": ["vegetation", "index"]
        }
      ],
      "period": ["WET", "DRY", "MONTH"],
      "year": [2017, 2018, ..., 2025],
      "bands": {
        "optical": ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"],
        "quality": ["QA60", "MSK_CLDPRB", "SCL"]
      }
    },
    {
      "name": "landsat",
      "display_name": "Landsat Collection",
      "satellite": "landsat",
      "visparam": ["landsat-tvi-false", "landsat-ndvi", "landsat-rgb"],
      "period": ["WET", "DRY", "MONTH"],
      "year": [1985, ..., 2025]
    }
  ],
  "metadata": {
    "last_updated": "2025-07-20T18:00:00Z",
    "version": "2.0"
  }
}
```

### 2. Validação Automática

O sistema valida automaticamente:
- Anos disponíveis por coleção
- Períodos suportados
- Visualizações (visparam) ativas
- Compatibilidade de bandas por satélite

## Configuração de Vis_Params no MongoDB

### Estrutura do Documento

```javascript
{
  "_id": ObjectId("..."),
  "name": "tvi-red",
  "display_name": "TVI Red",
  "description": "Triangular Vegetation Index with Red band",
  "category": "sentinel",  // ou "landsat"
  "active": true,
  "tags": ["vegetation", "index", "tvi"],
  
  // Configuração de bandas
  "band_config": {
    "expression": "(120 * (nir - green) - 200 * (red - green)) / 2",
    "bands_used": ["B8", "B3", "B4"],
    "index_range": [-1, 1]
  },
  
  // Parâmetros de visualização para Google Earth Engine
  "vis_params": {
    "min": 0,
    "max": 100,
    "palette": ["#d7191c", "#fdae61", "#ffffbf", "#a6d96a", "#1a9641"]
  },
  
  // Configurações específicas por satélite (para Landsat)
  "satellite_configs": {
    "TM": {
      "bands": ["SR_B4", "SR_B3", "SR_B2"],
      "scale_factor": 0.0000275,
      "offset": -0.2
    },
    "OLI": {
      "bands": ["SR_B5", "SR_B3", "SR_B2"],
      "scale_factor": 0.0000275,
      "offset": -0.2
    }
  },
  
  "created_at": ISODate("2024-01-01T00:00:00Z"),
  "updated_at": ISODate("2024-07-20T00:00:00Z")
}
```

### Adicionando Nova Visualização

```javascript
// MongoDB Shell ou Compass
db.vis_params.insertOne({
  name: "custom-index",
  display_name: "Custom Vegetation Index",
  description: "Índice customizado para análise",
  category: "sentinel",
  active: true,
  tags: ["custom", "vegetation"],
  band_config: {
    expression: "(nir - red) / (nir + red)",
    bands_used: ["B8", "B4"]
  },
  vis_params: {
    min: -1,
    max: 1,
    palette: ["blue", "white", "green"]
  }
});
```

### Desativando Visualização

```javascript
db.vis_params.updateOne(
  { name: "old-visualization" },
  { $set: { active: false } }
);
```

## Cache e Performance

### Sistema de Cache

O sistema utiliza um cache em três níveis:

1. **Cache de PNG (Tiles)**
   - Armazena tiles renderizados
   - TTL: Configurável (padrão 24h)
   - Path: `{layer}_{period}_{year}_{month}_{visparam}/{geohash}/{z}/{x}_{y}.png`

2. **Cache de URLs do Earth Engine**
   - Armazena URLs de tiles do Google Earth Engine
   - TTL: Configurável via `LIFESPAN_URL`
   - Evita recriação de mapas no EE

3. **Cache de Capabilities**
   - Cache em memória das capabilities
   - TTL: 5 minutos
   - Reduz consultas ao MongoDB

### Invalidação de Cache

Para invalidar o cache após mudanças:

```python
# Via API
POST /api/cache/invalidate/capabilities

# Ou programaticamente
from app.utils.capabilities import get_capabilities_provider
provider = get_capabilities_provider()
provider.clear_cache()
```

## Exemplos de Uso

### 1. Integração com Leaflet

```javascript
// Camada Sentinel-2 TVI
const sentinelLayer = L.tileLayer(
  'https://tiles.lapig.iesa.ufg.br/api/layers/s2_harmonized/{x}/{y}/{z}' +
  '?period=WET&year=2024&visparam=tvi-red',
  {
    attribution: 'LAPIG/UFG',
    minZoom: 6,
    maxZoom: 18
  }
);

// Camada Landsat NDVI
const landsatLayer = L.tileLayer(
  'https://tiles.lapig.iesa.ufg.br/api/layers/landsat/{x}/{y}/{z}' +
  '?period=MONTH&year=2024&month=7&visparam=landsat-ndvi',
  {
    attribution: 'LAPIG/UFG',
    minZoom: 6,
    maxZoom: 18
  }
);
```

### 2. Obter Visualizações Disponíveis

```python
import requests

# Obter capabilities
resp = requests.get("https://tiles.lapig.iesa.ufg.br/api/capabilities/")
capabilities = resp.json()

# Listar visualizações do Sentinel-2
for collection in capabilities["collections"]:
    if collection["name"] == "s2_harmonized":
        print("Visualizações Sentinel-2:")
        for vp in collection["visparam_details"]:
            print(f"  - {vp['name']}: {vp['description']}")
```

### 3. Validar Parâmetros

```python
def validate_request(collection, year, period, visparam):
    resp = requests.get("https://tiles.lapig.iesa.ufg.br/api/capabilities/")
    capabilities = resp.json()
    
    for coll in capabilities["collections"]:
        if coll["name"] == collection:
            if year not in coll["year"]:
                return False, f"Ano {year} não disponível"
            if period not in coll["period"]:
                return False, f"Período {period} não suportado"
            if visparam not in coll["visparam"]:
                return False, f"Visualização {visparam} não encontrada"
            return True, "OK"
    
    return False, f"Coleção {collection} não encontrada"
```

## Troubleshooting

### Erros Comuns

1. **404 - Visparam inválido**
   - Verifique se o visparam está ativo no MongoDB
   - Confirme o nome exato via `/api/capabilities/`

2. **404 - Ano inválido**
   - Sentinel-2: disponível a partir de 2017
   - Landsat: disponível a partir de 1985

3. **400 - Zoom fora do intervalo**
   - Use zoom entre 6 e 18

4. **Tiles em branco**
   - Pode não haver dados para a região/período
   - Verifique cobertura de nuvens

### Logs e Monitoramento

```bash
# Verificar logs do serviço
docker logs tiles-api

# Verificar cache
redis-cli -n 0
> KEYS *s2_harmonized*

# Verificar MongoDB
mongosh
> use tiles_db
> db.vis_params.find({ active: true, category: "sentinel" })
```

### Performance

Para otimizar performance:

1. **Use zoom apropriado**: Zooms muito altos em áreas grandes geram muitas requisições
2. **Implemente cache no cliente**: Cache tiles no navegador
3. **Use CDN**: Configure CDN para servir tiles cacheados
4. **Monitore rate limits**: Respeite os limites configurados

## Migração do Sistema Antigo

Se você está migrando do sistema antigo:

1. **URLs permanecem as mesmas** - Apenas a lógica interna mudou
2. **Novos vis_params** - Devem ser adicionados via MongoDB
3. **Capabilities dinâmicas** - Não é mais necessário editar código para adicionar anos

---

Para mais informações, consulte:
- [Documentação de Vis_Params](./VIS_PARAMS_GUIDE.md)
- [API Reference](./API_REFERENCE.md)
- [Cache Configuration](./CACHE_CONFIG.md)