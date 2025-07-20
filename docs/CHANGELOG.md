# 📝 Changelog

All notable changes to the Tiles API project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Authentication protection for Task Management routes (`/api/tasks/*`)
- Authentication protection for Administration routes (`/api/admin/*`)
- Lightweight health check endpoint `/health/light` for Traefik
- Documentation index file (INDEX.md)
- Hide administrative routes from public API documentation

### Changed
- Updated Traefik configuration to use lightweight health check
- Reorganized documentation structure
- Updated environment variables documentation
- Fixed Landsat 4 band mapping error (SR_B3→RED, SR_B4→NIR)

### Fixed
- Fixed health check to use correct Redis connection method
- Fixed Earth Engine band selection for Landsat 4/5/7

### Security
- All administrative endpoints now require SuperAdmin authentication

## [3.5.0] - 2024-01-20

### Added
- MongoDB-based dynamic visualization parameters system
- Capabilities and vis_params management APIs
- Admin endpoints for cache and category management
- Comprehensive API documentation

### Changed
- Integrated MongoDB-based capabilities management
- Fixed async/sync context issues in layers.py
- Standardized database categories from 'sentinel2' to 'sentinel'

### Fixed
- Earth Engine format conversion compatibility
- Thread safety in Earth Engine operations

## [3.0.0] - 2024-01-15

### Added
- Flower monitoring for Celery tasks
- Hybrid cache system (Redis + S3/MinIO)
- Load balancing with 5 instances
- OpenTelemetry monitoring support

### Changed
- Migrated from Redis-only to hybrid cache
- Improved performance to handle 2,500+ req/s

### Removed
- New Relic monitoring (replaced with OpenTelemetry)

## [2.0.0] - 2023-12-01

### Added
- FastAPI framework migration
- Async/await support throughout
- Rate limiting middleware
- CORS support for dynamic subdomains

### Changed
- Complete rewrite from Flask to FastAPI
- Improved Earth Engine integration
- Enhanced error handling

## [1.0.0] - 2023-06-01

### Added
- Initial release
- Basic tile generation for Landsat and Sentinel-2
- Redis caching
- Time series extraction API