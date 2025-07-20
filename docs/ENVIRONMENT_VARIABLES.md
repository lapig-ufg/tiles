# 🔧 Environment Variables - Tiles API

This document describes all available environment variables for configuring the Tiles API.

## 📋 Table of Contents

- [Basic Configuration](#basic-configuration)
- [Google Earth Engine](#google-earth-engine)
- [MongoDB](#mongodb)
- [Cache Configuration](#cache-configuration)
- [Performance](#performance)
- [Rate Limiting](#rate-limiting)
- [Security](#security)
- [Monitoring](#monitoring)

## Basic Configuration

### TILES_ENV
- **Description**: Defines the execution environment
- **Values**: `development`, `production`
- **Default**: `development`
- **Example**: `TILES_ENV=production`

### HOST
- **Description**: Host to bind the server
- **Type**: String
- **Default**: `0.0.0.0`
- **Example**: `HOST=127.0.0.1`

### PORT
- **Description**: Port where the server will listen
- **Type**: Integer
- **Default**: `8080`
- **Example**: `PORT=8000`

## Google Earth Engine

### GEE_SERVICE_ACCOUNT
- **Description**: Path to the JSON file with Google Earth Engine service account credentials
- **Type**: String (file path)
- **Default**: `.service-accounts/gee-sa.json`
- **Example**: `GEE_SERVICE_ACCOUNT=/secrets/gee-credentials.json`
- **Important**: This file must contain valid GEE credentials

### GEE_PROJECT_ID
- **Description**: Google Cloud Project ID for Earth Engine
- **Type**: String
- **Example**: `GEE_PROJECT_ID=my-gee-project`

## MongoDB

### MONGO_DB_URL
- **Description**: MongoDB connection URL
- **Format**: `mongodb://[username:password@]host:port/database`
- **Default**: `mongodb://lapig:lapig@mongodb:27017/tvi`
- **Example**: `MONGO_DB_URL=mongodb://user:pass@cluster.mongodb.net/tiles`

### MONGO_DB_NAME
- **Description**: MongoDB database name
- **Type**: String
- **Default**: `tvi`
- **Example**: `MONGO_DB_NAME=tiles_production`

## Cache Configuration

### REDIS_URL
- **Description**: Redis/Valkey connection URL
- **Format**: `redis://[username:password@]host:port[/database]`
- **Default**: `redis://valkey:6379`
- **Examples**:
  - Local: `redis://localhost:6379`
  - With password: `redis://:mypassword@redis-server:6379`
  - With database: `redis://localhost:6379/1`

### S3_ENDPOINT
- **Description**: S3 or MinIO endpoint URL
- **Type**: String (URL)
- **Default**: `http://minio:9000`
- **Examples**:
  - Local MinIO: `http://localhost:9000`
  - AWS S3: `https://s3.amazonaws.com`
  - Production MinIO: `https://minio.example.com`

### S3_ACCESS_KEY
- **Description**: Access Key ID for S3/MinIO
- **Type**: String
- **Default**: `minioadmin`
- **Example**: `S3_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE`

### S3_SECRET_KEY
- **Description**: Secret Access Key for S3/MinIO
- **Type**: String
- **Default**: `minioadmin`
- **Example**: `S3_SECRET_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY`
- **Security**: Never commit this key to the repository

### S3_BUCKET
- **Description**: S3/MinIO bucket name for storing tile cache
- **Type**: String
- **Default**: `tiles-cache`
- **Example**: `S3_BUCKET=production-tiles-cache`

## Performance

### WORKERS
- **Description**: Number of worker processes (when using Gunicorn)
- **Type**: Integer
- **Default**: `1` (for uvicorn)
- **Recommendation**: `(2 x number of CPUs) + 1`
- **Example**: `WORKERS=4`

### CELERY_BROKER_URL
- **Description**: Celery broker URL (Redis)
- **Type**: String
- **Default**: `redis://valkey:6379/0`
- **Example**: `CELERY_BROKER_URL=redis://broker:6379/0`

### CELERY_RESULT_BACKEND
- **Description**: Celery result backend URL
- **Type**: String
- **Default**: `redis://valkey:6379/0`
- **Example**: `CELERY_RESULT_BACKEND=redis://broker:6379/0`

## Rate Limiting

### RATE_LIMIT_PER_MINUTE
- **Description**: Maximum requests per minute per IP
- **Type**: Integer
- **Default**: `100000`
- **Example**: `RATE_LIMIT_PER_MINUTE=50000`

## Security

### SECRET_KEY
- **Description**: Secret key for session/token signing
- **Type**: String
- **Default**: Generated randomly
- **Example**: `SECRET_KEY=your-secret-key-here`
- **Important**: Generate a strong key for production

### ALLOWED_HOSTS
- **Description**: Allowed hosts for the application
- **Type**: Comma-separated string
- **Default**: `*` (all hosts)
- **Example**: `ALLOWED_HOSTS=tiles.lapig.iesa.ufg.br,tm1.lapig.iesa.ufg.br`

## Monitoring

### OTEL_EXPORTER_OTLP_ENDPOINT
- **Description**: OpenTelemetry collector endpoint
- **Type**: String (URL)
- **Default**: `http://otel:4317`
- **Example**: `OTEL_EXPORTER_OTLP_ENDPOINT=http://telemetry:4317`

### OTEL_SERVICE_NAME
- **Description**: Service name for telemetry
- **Type**: String
- **Default**: `tiles-api`
- **Example**: `OTEL_SERVICE_NAME=tiles-api-production`

### LOG_LEVEL
- **Description**: Minimum log level to display
- **Values**: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`
- **Default**: `INFO`
- **Example**: `LOG_LEVEL=DEBUG`

## 🚀 Configuration Examples

### Local Development
```bash
TILES_ENV=development
HOST=127.0.0.1
PORT=8080
GEE_SERVICE_ACCOUNT=.service-accounts/gee-sa.json
MONGO_DB_URL=mongodb://localhost:27017/tvi
REDIS_URL=redis://localhost:6379
S3_ENDPOINT=http://localhost:9000
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
LOG_LEVEL=DEBUG
```

### Production
```bash
TILES_ENV=production
HOST=0.0.0.0
PORT=8080
GEE_SERVICE_ACCOUNT=/secrets/gee.json
GEE_PROJECT_ID=my-gee-project
MONGO_DB_URL=mongodb://user:pass@mongodb:27017/tiles
REDIS_URL=redis://valkey:6379
S3_ENDPOINT=http://minio:9000
S3_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE
S3_SECRET_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
S3_BUCKET=production-tiles
RATE_LIMIT_PER_MINUTE=100000
LOG_LEVEL=WARNING
SECRET_KEY=your-production-secret-key
ALLOWED_HOSTS=tiles.lapig.iesa.ufg.br
```

## 📝 Notes

1. **Security**: Never commit `.env` files with real credentials
2. **Docker**: Variables in `docker-compose.yml` override those in `.env`
3. **Priority**: Environment variables > `.env` file > default values
4. **Validation**: The application validates critical configurations at startup
5. **Secrets**: Use proper secret management in production (e.g., Kubernetes secrets, AWS Secrets Manager)