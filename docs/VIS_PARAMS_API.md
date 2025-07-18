# Visualization Parameters API Documentation

API para gerenciamento dos parâmetros de visualização (vis_params) armazenados no MongoDB.

## Autenticação

Todos os endpoints requerem autenticação de Super Admin.

## Endpoints

### 1. Listar Parâmetros de Visualização

```
GET /api/vis-params/
```

Lista todos os parâmetros de visualização com filtros opcionais.

**Query Parameters:**
- `category` (opcional): Filtra por categoria (sentinel2, landsat)
- `active` (opcional): Filtra por status ativo (true/false)
- `tag` (opcional): Filtra por tag

**Exemplo:**
```bash
curl -X GET "http://localhost:8080/api/vis-params/?category=sentinel2&active=true" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 2. Obter Parâmetro Específico

```
GET /api/vis-params/{name}
```

Retorna um parâmetro de visualização específico.

**Exemplo:**
```bash
curl -X GET "http://localhost:8080/api/vis-params/tvi-green" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 3. Criar Novo Parâmetro

```
POST /api/vis-params/
```

Cria um novo parâmetro de visualização.

**Body (Sentinel-2):**
```json
{
  "name": "my-custom-vis",
  "display_name": "My Custom Visualization",
  "description": "Custom visualization for specific use case",
  "category": "sentinel2",
  "band_config": {
    "original_bands": ["B4", "B3", "B2"],
    "mapped_bands": ["RED", "GREEN", "BLUE"]
  },
  "vis_params": {
    "bands": ["B4", "B3", "B2"],
    "min": [0, 0, 0],
    "max": [3000, 3000, 3000],
    "gamma": 1.4
  },
  "tags": ["custom", "rgb"],
  "active": true
}
```

**Body (Landsat):**
```json
{
  "name": "landsat-custom",
  "display_name": "Custom Landsat Visualization",
  "description": "Custom Landsat parameters",
  "category": "landsat",
  "satellite_configs": [
    {
      "collection_id": "LANDSAT/LC08/C02/T1_L2",
      "vis_params": {
        "bands": ["SR_B4", "SR_B3", "SR_B2"],
        "min": [0.0, 0.0, 0.0],
        "max": [0.3, 0.3, 0.3],
        "gamma": 1.2
      }
    },
    {
      "collection_id": "LANDSAT/LC09/C02/T1_L2",
      "vis_params": {
        "bands": ["SR_B4", "SR_B3", "SR_B2"],
        "min": [0.0, 0.0, 0.0],
        "max": [0.3, 0.3, 0.3],
        "gamma": 1.2
      }
    }
  ],
  "tags": ["landsat", "true-color"],
  "active": true
}
```

### 4. Atualizar Parâmetro

```
PUT /api/vis-params/{name}
```

Atualiza um parâmetro existente. Apenas os campos fornecidos são atualizados.

**Body:**
```json
{
  "display_name": "Updated Display Name",
  "vis_params": {
    "bands": ["B4", "B3", "B2"],
    "min": [0, 0, 0],
    "max": [2500, 2500, 2500],
    "gamma": 1.5
  },
  "tags": ["updated", "custom"]
}
```

### 5. Deletar Parâmetro

```
DELETE /api/vis-params/{name}
```

Remove permanentemente um parâmetro de visualização.

**Exemplo:**
```bash
curl -X DELETE "http://localhost:8080/api/vis-params/my-custom-vis" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 6. Alternar Status Ativo

```
PATCH /api/vis-params/{name}/toggle
```

Alterna o status ativo/inativo de um parâmetro.

**Exemplo:**
```bash
curl -X PATCH "http://localhost:8080/api/vis-params/tvi-green/toggle" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 7. Testar Parâmetros

```
POST /api/vis-params/test
```

Testa parâmetros de visualização gerando uma URL de tile de exemplo.

**Body:**
```json
{
  "vis_params": {
    "bands": ["B4", "B3", "B2"],
    "min": [0, 0, 0],
    "max": [3000, 3000, 3000],
    "gamma": 1.4
  },
  "x": 512,
  "y": 512,
  "z": 10,
  "layer_type": "sentinel2"
}
```

### 8. Clonar Parâmetro

```
POST /api/vis-params/clone/{name}?new_name=my-clone
```

Cria uma cópia de um parâmetro existente com novo nome.

**Query Parameters:**
- `new_name` (obrigatório): Nome para o parâmetro clonado

### 9. Exportar Todos os Parâmetros

```
GET /api/vis-params/export/all
```

Exporta todos os parâmetros de visualização em formato JSON.

**Response:**
```json
{
  "export_date": "2024-01-15T10:30:00",
  "count": 6,
  "vis_params": [
    {...},
    {...}
  ]
}
```

### 10. Importar Parâmetros

```
POST /api/vis-params/import?overwrite=false
```

Importa parâmetros de visualização de um JSON.

**Query Parameters:**
- `overwrite` (opcional): Se true, sobrescreve parâmetros existentes

**Body:**
```json
{
  "vis_params": [
    {
      "_id": "imported-vis",
      "name": "imported-vis",
      "display_name": "Imported Visualization",
      ...
    }
  ]
}
```

### 11. Gerenciar Coleções Landsat

#### Obter Mapeamentos
```
GET /api/vis-params/landsat-collections
```

#### Atualizar Mapeamentos
```
PUT /api/vis-params/landsat-collections
```

**Body:**
```json
[
  {
    "start_year": 1985,
    "end_year": 2011,
    "collection": "LANDSAT/LT05/C02/T1_L2",
    "satellite": "Landsat 5"
  },
  {
    "start_year": 2012,
    "end_year": 2013,
    "collection": "LANDSAT/LE07/C02/T1_L2",
    "satellite": "Landsat 7"
  },
  {
    "start_year": 2014,
    "end_year": 2024,
    "collection": "LANDSAT/LC08/C02/T1_L2",
    "satellite": "Landsat 8"
  },
  {
    "start_year": 2025,
    "end_year": 2030,
    "collection": "LANDSAT/LC09/C02/T1_L2",
    "satellite": "Landsat 9"
  }
]
```

### 12. Gerenciar Coleções Sentinel-2

#### Obter Configurações
```
GET /api/vis-params/sentinel-collections
```

Retorna as configurações das coleções Sentinel-2, incluindo informações sobre bandas e parâmetros de filtragem de nuvens.

#### Atualizar Configurações
```
PUT /api/vis-params/sentinel-collections
```

**Body:**
```json
{
  "collections": [
    {
      "name": "COPERNICUS/S2_HARMONIZED",
      "display_name": "Sentinel-2 Harmonized",
      "description": "Harmonized Sentinel-2 MSI: MultiSpectral Instrument, Level-2A",
      "start_date": "2015-06-27",
      "bands": {
        "B1": {"name": "B1", "description": "Aerosols", "wavelength": "443nm", "resolution": "60m"},
        "B2": {"name": "B2", "description": "Blue", "wavelength": "490nm", "resolution": "10m"},
        "B3": {"name": "B3", "description": "Green", "wavelength": "560nm", "resolution": "10m"},
        "B4": {"name": "B4", "description": "Red", "wavelength": "665nm", "resolution": "10m"},
        "B8": {"name": "B8", "description": "NIR", "wavelength": "842nm", "resolution": "10m"},
        "B11": {"name": "B11", "description": "SWIR 1", "wavelength": "1610nm", "resolution": "20m"},
        "B12": {"name": "B12", "description": "SWIR 2", "wavelength": "2190nm", "resolution": "20m"}
      }
    }
  ],
  "default_collection": "COPERNICUS/S2_HARMONIZED",
  "cloud_filter_params": {
    "max_cloud_coverage": 20,
    "use_cloud_score": true,
    "cloud_score_threshold": 0.5
  }
}
```

#### Inicializar com Configuração Padrão
```
POST /api/vis-params/sentinel-collections/initialize
```

Cria uma configuração padrão completa com todas as bandas e propriedades do Sentinel-2.

**Response:**
```json
{
  "status": "success",
  "message": "Sentinel-2 collections initialized with default configuration",
  "collections_count": 2
}
```

#### Obter Informações de Bandas
```
GET /api/vis-params/sentinel-collections/bands/{collection_name}
```

Retorna informações detalhadas sobre as bandas de uma coleção específica.

**Exemplo:**
```bash
curl -X GET "http://localhost:8080/api/vis-params/sentinel-collections/bands/COPERNICUS/S2_HARMONIZED" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**Response:**
```json
{
  "collection": "COPERNICUS/S2_HARMONIZED",
  "bands": {
    "B1": {"name": "B1", "description": "Aerosols", "wavelength": "443nm", "resolution": "60m"},
    "B2": {"name": "B2", "description": "Blue", "wavelength": "490nm", "resolution": "10m"},
    "B3": {"name": "B3", "description": "Green", "wavelength": "560nm", "resolution": "10m"},
    "B4": {"name": "B4", "description": "Red", "wavelength": "665nm", "resolution": "10m"},
    ...
  },
  "quality_bands": ["QA10", "QA20", "QA60"]
}
```

## Estrutura dos Dados

### VisParam
```typescript
{
  bands: string[]       // Lista de bandas
  min: number[]        // Valores mínimos para cada banda
  max: number[]        // Valores máximos para cada banda
  gamma: number        // Correção gamma
}
```

### BandConfig
```typescript
{
  original_bands: string[]    // Bandas originais (ex: ["B4", "B8A"])
  mapped_bands?: string[]     // Bandas mapeadas (ex: ["RED", "NIR"])
}
```

### SatelliteVisParam
```typescript
{
  collection_id: string       // ID da coleção (ex: "LANDSAT/LC08/C02/T1_L2")
  vis_params: VisParam       // Parâmetros de visualização
}
```

## Notas Importantes

1. **Cache**: Após criar, atualizar ou deletar parâmetros, o cache é atualizado automaticamente em background.

2. **Validação**: 
   - O nome deve ser único e é usado como ID
   - Deve ter `vis_params` OU `satellite_configs`, não ambos
   - Para Sentinel-2: use `vis_params`
   - Para Landsat: use `satellite_configs`

3. **Migração**: Use o script `scripts/migrate_vis_params.py` para migrar os parâmetros existentes do código para o MongoDB.

4. **Configuração**: Defina `USE_MONGODB_VIS_PARAMS = true` no `settings.toml` para usar os parâmetros do MongoDB.