# 📚 Tiles API Documentation Index

## 📝 Recent Changes
- [**CHANGELOG**](./CHANGELOG.md) - Version history and recent updates

## 🏗️ Architecture & Setup

### Getting Started
- [**Quick Start Guide**](./QUICKSTART_UV.md) - Quick setup with UV package manager
- [**Environment Variables**](./ENVIRONMENT_VARIABLES.md) - Configuration reference
- [**Performance Guide**](./README_PERFORMANCE.md) - Performance optimization tips

### Infrastructure
- [**MinIO Policies**](./MINIO_POLICIES.md) - S3 storage configuration
- [**Cache System**](./CACHE_WARMING.md) - Cache warming and optimization

## 🔌 Public APIs

### Core APIs (Visible in /docs)
- [**Layers API Guide**](./LAYERS_API_GUIDE.md) - Tile generation endpoints
- [**Capabilities API**](./CAPABILITIES_API.md) - Dynamic capabilities system
- **Timeseries API** - Time series data extraction (see /docs)

### Example Integration
- [**Frontend Integration Example**](./FRONTEND_INTEGRATION_EXAMPLE.html) - Interactive HTML example

## 🔐 Admin APIs (Hidden from /docs)

### Cache Management
- [**Cache API Documentation**](./CACHE_API_DOCUMENTATION.md) - Cache management endpoints
- [**Cache Campaign API**](./CACHE_CAMPAIGN_API.md) - Campaign-based caching
- [**Cache Campaign Optimized**](./CACHE_CAMPAIGN_OPTIMIZED.md) - Optimized campaign strategies

### System Administration
- [**Tasks API**](./TASKS_API.md) - Celery task management
- [**Visualization Parameters API**](./VIS_PARAMS_API.md) - Dynamic vis params management
- [**Vis Params Examples**](./VIS_PARAMS_EXAMPLES.md) - Configuration examples

## 📊 Performance & Optimization

### Grid Loading Solutions
- [**Grid Loading Solutions**](./GRID_LOADING_SOLUTIONS.md) - Handling massive tile requests
- [**API Documentation**](./API_DOCUMENTATION.md) - Complete API reference

## 🚀 Quick Reference

### Essential Endpoints

#### Public Endpoints
```
GET  /                    # API info (hidden from docs)
GET  /health             # Full health check (hidden)
GET  /health/light       # Lightweight health check (hidden)
GET  /api/capabilities    # List available layers
POST /api/layers         # Generate tiles
GET  /api/timeseries/*   # Extract time series data
```

#### Protected Endpoints (Require Authentication)
```
# Cache Management
GET    /api/cache/stats
DELETE /api/cache/clear
POST   /api/cache/warmup

# Task Management  
GET    /api/tasks/list
GET    /api/tasks/status/{task_id}
POST   /api/tasks/purge

# Administration
POST   /api/admin/clear-capabilities-cache
GET    /api/admin/vis-params-summary
POST   /api/admin/fix-categories

# Visualization Parameters
GET    /api/vis-params/
POST   /api/vis-params/
PUT    /api/vis-params/{name}
DELETE /api/vis-params/{name}
```

### Authentication
Protected endpoints require HTTP Basic Auth with super-admin credentials:
```bash
curl -u admin:password https://tiles.lapig.iesa.ufg.br/api/admin/vis-params-summary
```

### Rate Limits
- Default: 100,000 requests/minute
- Burst: 10,000 requests
- Tiles endpoint: 2,500 req/s (5 instances)

## 📝 Notes

- All administrative routes are hidden from the public API documentation (/docs, /redoc)
- The system supports 5 concurrent tile server instances behind Traefik load balancer
- Cache is hybrid: Redis for metadata, MinIO/S3 for tile storage
- Supports Landsat 4-9, Sentinel-2, and MODIS imagery

## 🔗 External Resources

- [Google Earth Engine](https://earthengine.google.com/)
- [Traefik Documentation](https://doc.traefik.io/traefik/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)