# 🛰️ Tiles API - High Performance Satellite Imagery Service

A high-performance tile server for satellite imagery (Landsat, Sentinel-2, MODIS) built with FastAPI, Google Earth Engine, and hybrid caching.

## 🚀 Features

- **Multi-satellite Support**: Landsat 4-9, Sentinel-2, MODIS
- **High Performance**: Handles 2,500+ requests/second
- **Hybrid Cache**: Redis (metadata) + MinIO/S3 (tiles)
- **Load Balanced**: 5 instances behind Traefik
- **Dynamic Visualization**: MongoDB-based parameters
- **Time Series API**: Extract NDVI, precipitation data
- **Protected Admin APIs**: Secure management endpoints

## 📖 Documentation

See [**INDEX.md**](./INDEX.md) for the complete documentation index.

### Quick Links
- [Quick Start Guide](./QUICKSTART_UV.md)
- [Layers API Guide](./LAYERS_API_GUIDE.md)
- [Environment Variables](./ENVIRONMENT_VARIABLES.md)
- [Frontend Integration Example](./FRONTEND_INTEGRATION_EXAMPLE.html)

## 🔧 Quick Setup

1. **Clone and Install**
```bash
git clone <repository>
cd tiles
uv sync
```

2. **Configure Environment**
```bash
cp .env.example .env
# Edit .env with your credentials
```

3. **Start Services**
```bash
docker-compose up -d
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8080
```

## 🌐 API Endpoints

### Public APIs (visible in /docs)
- `/api/layers` - Generate satellite tiles
- `/api/capabilities` - List available layers
- `/api/timeseries/*` - Extract time series data

### Admin APIs (hidden, require auth)
- `/api/cache/*` - Cache management
- `/api/tasks/*` - Task monitoring
- `/api/admin/*` - System administration
- `/api/vis-params/*` - Visualization parameters

## 🔐 Authentication

Admin endpoints require HTTP Basic Auth:
```bash
curl -u admin:password https://tiles.lapig.iesa.ufg.br/api/admin/vis-params-summary
```

## 📊 Performance

- **Throughput**: 2,500+ tiles/second
- **Cache Hit Rate**: ~80% after warming
- **Response Time**: <100ms (cached), <2s (uncached)
- **Storage**: Hybrid Redis + S3/MinIO

## 🏗️ Architecture

```
┌─────────────┐     ┌──────────┐     ┌─────────────┐
│   Traefik   │────▶│ FastAPI  │────▶│    Redis    │
│ Load Balancer│     │ (5 inst) │     │  (metadata) │
└─────────────┘     └──────────┘     └─────────────┘
                           │                  
                           ▼                  
                    ┌──────────┐     ┌─────────────┐
                    │   GEE    │     │   MinIO/S3  │
                    │  Engine  │     │   (tiles)   │
                    └──────────┘     └─────────────┘
```

## 📝 License

This project is licensed under the MIT License.