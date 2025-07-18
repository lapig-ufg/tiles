# Capabilities API Documentation

## Overview

The Capabilities API provides dynamic information about available collections, visualization parameters, and system capabilities. It integrates with MongoDB to provide real-time updates as vis_params are added or modified.

## Key Features

1. **Dynamic Capabilities** - Reads from MongoDB vis_params collection
2. **Fallback Support** - Uses hardcoded values if MongoDB is unavailable
3. **Detailed Metadata** - Provides comprehensive information about collections and bands
4. **Validation Support** - Validates request parameters before processing

## API Endpoints

### 1. Get All Capabilities
```
GET /api/capabilities/
```

Returns complete capabilities including:
- Available collections (Sentinel-2, Landsat)
- Visualization parameters with details
- System metadata
- Collection-specific information (bands, years, periods)

**Response Example:**
```json
{
  "collections": [
    {
      "name": "s2_harmonized",
      "display_name": "Sentinel-2 Harmonized",
      "satellite": "sentinel",
      "visparam": ["tvi-green", "tvi-red", "tvi-rgb"],
      "visparam_details": [
        {
          "name": "tvi-green",
          "display_name": "TVI Green",
          "description": "SWIR1/REDEDGE4/RED",
          "tags": ["vegetation", "analysis"]
        }
      ],
      "period": ["WET", "DRY", "MONTH"],
      "year": [2017, 2018, ..., 2025],
      "collections": ["COPERNICUS/S2_HARMONIZED"],
      "bands": {
        "optical": ["B1", "B2", "B3", ...],
        "quality": ["QA60", "MSK_CLDPRB", "SCL"],
        "common_names": {
          "B4": "RED",
          "B8": "NIR",
          "B11": "SWIR1"
        }
      },
      "cloud_filter": {
        "property": "CLOUDY_PIXEL_PERCENTAGE",
        "max_value": 20
      }
    }
  ],
  "vis_params": {
    "tvi-green": {
      "category": "sentinel",
      "band_config": {...},
      "vis_params": {...},
      "active": true
    }
  },
  "metadata": {
    "last_updated": "2025-07-18T00:00:00",
    "version": "2.0",
    "total_vis_params": 6,
    "active_sentinel": 3,
    "active_landsat": 3
  }
}
```

### 2. Legacy Capabilities (Backward Compatibility)
```
GET /api/capabilities/legacy
GET /api/capabilities  (original endpoint)
```

Returns simplified format matching the original API structure.

### 3. Collection Information
```
GET /api/capabilities/collections
GET /api/capabilities/collections/{collection_name}
```

Returns detailed metadata about collections including:
- Band information
- Sensor details
- Date ranges
- Cloud filtering parameters

### 4. Visualization Parameters
```
GET /api/capabilities/vis-params?category=sentinel&active_only=true
GET /api/capabilities/vis-params/{vis_param_name}
```

Query and retrieve visualization parameter details.

### 5. Validate Request
```
POST /api/capabilities/validate
{
  "collection": "s2_harmonized",
  "vis_param": "tvi-green",
  "year": 2024,
  "period": "WET"
}
```

Validates if the requested parameters are available before making tile requests.

### 6. Available Years
```
GET /api/capabilities/years/{collection_name}
```

Returns available years for a specific collection with range information.

### 7. Refresh Capabilities
```
GET /api/capabilities/admin/refresh
```

Forces a reload of capabilities from MongoDB.

## Collection Metadata

### Sentinel-2 Harmonized
- **Years**: 2017 - present
- **Periods**: WET, DRY, MONTH
- **Collections**: 
  - COPERNICUS/S2_HARMONIZED
  - COPERNICUS/S2_SR_HARMONIZED
- **Cloud Filter**: CLOUDY_PIXEL_PERCENTAGE < 20%

### Landsat Collection
- **Years**: 1985 - present
- **Periods**: WET, DRY, MONTH
- **Months**: 01-12 (for monthly composites)
- **Collections**:
  - Landsat 5 (1985-2011): LANDSAT/LT05/C02/T1_L2
  - Landsat 7 (2012-2013): LANDSAT/LE07/C02/T1_L2
  - Landsat 8 (2014-2024): LANDSAT/LC08/C02/T1_L2
  - Landsat 9 (2025+): LANDSAT/LC09/C02/T1_L2
- **Cloud Filter**: CLOUD_COVER < 20%

## Integration with MongoDB

The capabilities system automatically integrates with the vis_params collection in MongoDB:

1. **Dynamic Loading** - Reads active vis_params on each request
2. **Category Grouping** - Groups vis_params by satellite type
3. **Metadata Enrichment** - Includes display names, descriptions, and tags
4. **Fallback Support** - Uses hardcoded values if MongoDB is unavailable

## Usage Examples

### 1. Check Available Visualizations
```bash
curl http://localhost:8000/api/capabilities/vis-params?category=sentinel
```

### 2. Validate Before Request
```bash
curl -X POST http://localhost:8000/api/capabilities/validate \
  -H "Content-Type: application/json" \
  -d '{
    "collection": "landsat",
    "vis_param": "landsat-tvi-true",
    "year": 2023,
    "month": "06"
  }'
```

### 3. Get Collection Details
```bash
curl http://localhost:8000/api/capabilities/collections/s2_harmonized
```

## Migration from Static to Dynamic

To migrate from hardcoded VISPARAMS to MongoDB:

1. Run the migration script:
   ```bash
   python scripts/migrate_vis_params.py
   ```

2. Set environment variable:
   ```bash
   export USE_MONGODB_VIS_PARAMS=true
   ```

3. The capabilities API will automatically use MongoDB vis_params

## Best Practices

1. **Cache Results** - Capabilities don't change frequently, cache on client side
2. **Validate First** - Use the validate endpoint before making tile requests
3. **Check Metadata** - Use metadata to understand data availability
4. **Monitor Updates** - Check last_updated timestamp for changes