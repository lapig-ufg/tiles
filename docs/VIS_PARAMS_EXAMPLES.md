# Exemplos de Configuração de Vis_Params

Este documento contém exemplos práticos de configurações de vis_params para o MongoDB.

## Índices de Vegetação

### NDVI - Normalized Difference Vegetation Index

```json
{
  "name": "ndvi",
  "display_name": "NDVI",
  "description": "Índice de Vegetação por Diferença Normalizada",
  "category": "sentinel",
  "active": true,
  "tags": ["vegetation", "index", "ndvi", "classic"],
  "band_config": {
    "expression": "(nir - red) / (nir + red)",
    "bands_used": ["B8", "B4"],
    "index_range": [-1, 1]
  },
  "vis_params": {
    "min": -0.2,
    "max": 0.8,
    "palette": ["#d73027", "#fc8d59", "#fee08b", "#d9ef8b", "#91cf60", "#1a9850"]
  }
}
```

### EVI - Enhanced Vegetation Index

```json
{
  "name": "evi",
  "display_name": "EVI",
  "description": "Índice de Vegetação Melhorado",
  "category": "sentinel",
  "active": true,
  "tags": ["vegetation", "index", "evi", "enhanced"],
  "band_config": {
    "expression": "2.5 * ((nir - red) / (nir + 6 * red - 7.5 * blue + 1))",
    "bands_used": ["B8", "B4", "B2"],
    "index_range": [-1, 1]
  },
  "vis_params": {
    "min": 0,
    "max": 1,
    "palette": ["#ffffff", "#ce7e45", "#df923d", "#f1b555", "#fcd163", "#99b718", "#74a901", "#66a000", "#529400", "#3e8601", "#207401", "#056201", "#004c00", "#023b01", "#012e01", "#011d01", "#011301"]
  }
}
```

### SAVI - Soil Adjusted Vegetation Index

```json
{
  "name": "savi",
  "display_name": "SAVI",
  "description": "Índice de Vegetação Ajustado para Solo",
  "category": "sentinel",
  "active": true,
  "tags": ["vegetation", "index", "savi", "soil"],
  "band_config": {
    "expression": "((nir - red) / (nir + red + 0.5)) * 1.5",
    "bands_used": ["B8", "B4"],
    "index_range": [-1.5, 1.5],
    "soil_factor": 0.5
  },
  "vis_params": {
    "min": -0.5,
    "max": 1,
    "palette": ["#8b4513", "#daa520", "#ffff00", "#9acd32", "#00ff00", "#228b22", "#006400"]
  }
}
```

## Composições RGB

### True Color (Cor Natural)

```json
{
  "name": "rgb",
  "display_name": "Cor Natural",
  "description": "Composição RGB em cores naturais",
  "category": "sentinel",
  "active": true,
  "tags": ["rgb", "true-color", "natural"],
  "band_config": {
    "bands": ["B4", "B3", "B2"],
    "description": "Red-Green-Blue"
  },
  "vis_params": {
    "min": 0,
    "max": 3000,
    "gamma": 1.4
  }
}
```

### False Color (Infravermelho)

```json
{
  "name": "false-color",
  "display_name": "Falsa Cor",
  "description": "Composição falsa cor para destacar vegetação",
  "category": "sentinel",
  "active": true,
  "tags": ["rgb", "false-color", "nir"],
  "band_config": {
    "bands": ["B8", "B4", "B3"],
    "description": "NIR-Red-Green"
  },
  "vis_params": {
    "min": 0,
    "max": 3000,
    "gamma": 1.2
  }
}
```

### Agriculture (Agricultura)

```json
{
  "name": "agriculture",
  "display_name": "Agricultura",
  "description": "Composição otimizada para análise agrícola",
  "category": "sentinel",
  "active": true,
  "tags": ["rgb", "agriculture", "swir"],
  "band_config": {
    "bands": ["B11", "B8", "B2"],
    "description": "SWIR-NIR-Blue"
  },
  "vis_params": {
    "min": 0,
    "max": 3000,
    "gamma": 1.3
  }
}
```

## Índices Específicos

### NDWI - Normalized Difference Water Index

```json
{
  "name": "ndwi",
  "display_name": "NDWI",
  "description": "Índice de Água por Diferença Normalizada",
  "category": "sentinel",
  "active": true,
  "tags": ["water", "index", "ndwi"],
  "band_config": {
    "expression": "(green - nir) / (green + nir)",
    "bands_used": ["B3", "B8"],
    "index_range": [-1, 1]
  },
  "vis_params": {
    "min": -0.5,
    "max": 0.5,
    "palette": ["#ffffcc", "#c7e9b4", "#7fcdbb", "#41b6c4", "#2c7fb8", "#253494"]
  }
}
```

### NDBI - Normalized Difference Built-up Index

```json
{
  "name": "ndbi",
  "display_name": "NDBI",
  "description": "Índice de Área Construída",
  "category": "sentinel",
  "active": true,
  "tags": ["urban", "index", "ndbi", "built-up"],
  "band_config": {
    "expression": "(swir - nir) / (swir + nir)",
    "bands_used": ["B11", "B8"],
    "index_range": [-1, 1]
  },
  "vis_params": {
    "min": -0.5,
    "max": 0.5,
    "palette": ["#0000ff", "#00ffff", "#ffff00", "#ff7f00", "#ff0000", "#7f0000"]
  }
}
```

## Configurações Landsat

### Landsat NDVI (Multi-sensor)

```json
{
  "name": "landsat-ndvi",
  "display_name": "NDVI Landsat",
  "description": "NDVI compatível com todos os sensores Landsat",
  "category": "landsat",
  "active": true,
  "tags": ["vegetation", "index", "ndvi", "landsat"],
  "satellite_configs": {
    "TM": {
      "bands": ["SR_B4", "SR_B3"],
      "expression": "(b4 - b3) / (b4 + b3)",
      "scale_factor": 0.0000275,
      "offset": -0.2
    },
    "ETM+": {
      "bands": ["SR_B4", "SR_B3"],
      "expression": "(b4 - b3) / (b4 + b3)",
      "scale_factor": 0.0000275,
      "offset": -0.2
    },
    "OLI": {
      "bands": ["SR_B5", "SR_B4"],
      "expression": "(b5 - b4) / (b5 + b4)",
      "scale_factor": 0.0000275,
      "offset": -0.2
    },
    "OLI-2": {
      "bands": ["SR_B5", "SR_B4"],
      "expression": "(b5 - b4) / (b5 + b4)",
      "scale_factor": 0.0000275,
      "offset": -0.2
    }
  },
  "vis_params": {
    "min": -0.2,
    "max": 0.8,
    "palette": ["#d73027", "#fc8d59", "#fee08b", "#d9ef8b", "#91cf60", "#1a9850"]
  }
}
```

### Landsat True Color

```json
{
  "name": "landsat-rgb",
  "display_name": "Cor Natural Landsat",
  "description": "Composição RGB para todos os sensores Landsat",
  "category": "landsat",
  "active": true,
  "tags": ["rgb", "true-color", "landsat"],
  "satellite_configs": {
    "TM": {
      "bands": ["SR_B3", "SR_B2", "SR_B1"],
      "scale_factor": 0.0000275,
      "offset": -0.2
    },
    "ETM+": {
      "bands": ["SR_B3", "SR_B2", "SR_B1"],
      "scale_factor": 0.0000275,
      "offset": -0.2
    },
    "OLI": {
      "bands": ["SR_B4", "SR_B3", "SR_B2"],
      "scale_factor": 0.0000275,
      "offset": -0.2
    },
    "OLI-2": {
      "bands": ["SR_B4", "SR_B3", "SR_B2"],
      "scale_factor": 0.0000275,
      "offset": -0.2
    }
  },
  "vis_params": {
    "min": 0,
    "max": 0.3,
    "gamma": 1.4
  }
}
```

## Scripts de Importação

### Importar Todos os Exemplos

```javascript
// MongoDB Shell
use tiles_db;

// Ler arquivo JSON com todos os vis_params
const visParams = [
  // Cole aqui os JSONs acima
];

// Inserir todos
db.vis_params.insertMany(visParams);

// Verificar
db.vis_params.find({ active: true }).count();
```

### Script Python para Importação

```python
from pymongo import MongoClient
from datetime import datetime
import json

# Conectar ao MongoDB
client = MongoClient('mongodb://localhost:27017/')
db = client['tiles_db']
collection = db['vis_params']

# Vis params para importar
vis_params = [
    {
        "name": "ndvi",
        "display_name": "NDVI",
        # ... resto da configuração
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    },
    # Adicione outros vis_params aqui
]

# Inserir
for vp in vis_params:
    collection.update_one(
        {"name": vp["name"]},
        {"$set": vp},
        upsert=True
    )

print(f"Importados {len(vis_params)} vis_params")
```

## Validação

### Verificar Vis_Params Ativos

```javascript
// MongoDB Shell
db.vis_params.find(
  { active: true },
  { name: 1, display_name: 1, category: 1 }
).sort({ category: 1, name: 1 });
```

### Testar Visualização

```python
import requests

# Testar endpoint de capabilities
resp = requests.get("http://localhost:8080/api/capabilities/")
caps = resp.json()

# Verificar se vis_param foi carregado
for coll in caps["collections"]:
    print(f"\n{coll['name']}:")
    for vp in coll["visparam"]:
        print(f"  - {vp}")

# Testar tile
tile_url = "http://localhost:8080/api/layers/s2_harmonized/2794/4592/13"
params = {
    "period": "WET",
    "year": 2024,
    "visparam": "ndvi"
}
resp = requests.get(tile_url, params=params)
print(f"\nTile status: {resp.status_code}")
```

## Boas Práticas

1. **Nomenclatura Consistente**
   - Use nomes descritivos em minúsculas
   - Prefixe com `landsat-` para visualizações Landsat
   - Use `-` como separador

2. **Tags Úteis**
   - Categorize por tipo: `vegetation`, `water`, `urban`
   - Indique o tipo: `index`, `rgb`, `composite`
   - Marque características: `multitemporal`, `seasonal`

3. **Documentação**
   - Sempre inclua `description` clara
   - Documente a `expression` matemática
   - Liste `bands_used` explicitamente

4. **Performance**
   - Evite cálculos complexos na `expression`
   - Use paletas com cores apropriadas
   - Ajuste `min`/`max` para a região de interesse

5. **Versionamento**
   - Use `created_at` e `updated_at`
   - Mantenha histórico de mudanças
   - Desative em vez de deletar