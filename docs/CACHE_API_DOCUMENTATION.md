# Cache Management API Documentation

This document provides comprehensive details for integrating with the Tiles API cache management system from backend applications.

## Overview

The cache management API is organized into the following sections:

1. **General Cache Management** - Protected endpoints for cache statistics and clearing
2. **Cache Warming** - Protected endpoints for pre-warming cache with tiles
3. **TVI Point/Campaign Management** - Protected endpoints for managing TVI system cache
4. **Task Management** - Protected endpoints for monitoring Celery tasks

All cache endpoints are available under the `/api/cache` prefix and **require super-admin authentication**.

## Authentication

**IMPORTANT: All cache endpoints require super-admin authentication using HTTP Basic Auth.**

### Authentication Methods

1. **HTTP Basic Authentication**
```bash
curl -u "username:password" http://localhost:8000/api/cache/...
```

2. **Using Authorization Header**
```bash
# Encode credentials in base64
echo -n "username:password" | base64
# Result: dXNlcm5hbWU6cGFzc3dvcmQ=

# Use in header
curl -H "Authorization: Basic dXNlcm5hbWU6cGFzc3dvcmQ=" http://localhost:8000/api/cache/...
```

3. **Python Example**
```python
import requests
from requests.auth import HTTPBasicAuth

# Method 1: Using auth parameter
response = requests.get(
    "http://localhost:8000/api/cache/stats",
    auth=HTTPBasicAuth('username', 'password')
)

# Method 2: Using headers
import base64
credentials = base64.b64encode(b"username:password").decode('ascii')
headers = {'Authorization': f'Basic {credentials}'}
response = requests.get(
    "http://localhost:8000/api/cache/stats",
    headers=headers
)
```

### User Requirements

The authenticated user must have:
- `role = "super-admin"` OR
- `type = "admin"`

Authentication supports both plain text and SHA256 hashed passwords stored in MongoDB.

## Endpoints

### General Cache Management

#### GET /api/cache/stats
Get comprehensive cache statistics and metrics.

**Authentication Required:** Yes (super-admin)

**Response:**
```json
{
  "total_cached_tiles": 12543,
  "cache_hit_rate": 0.85,
  "redis_keys": 12543,
  "disk_usage_mb": 1024.5,
  "popular_tiles": [
    {"tile": "10/512/512", "hits": 1523}
  ],
  "last_warmup": "2024-01-15T10:30:00",
  "active_tasks": 3
}
```

#### DELETE /api/cache/clear
Clear cache entries based on filters. Requires confirmation.

**Query Parameters:**
- `layer` (optional): Specific layer to clear
- `year` (optional): Specific year to clear
- `x`, `y`, `z` (optional): Specific tile coordinates
- `pattern` (optional): Custom pattern for clearing
- `confirm` (required): Must be `true` to execute

**Examples:**
```bash
# Clear all landsat cache
curl -X DELETE "http://localhost:8000/api/cache/clear?layer=landsat&confirm=true"

# Clear specific year
curl -X DELETE "http://localhost:8000/api/cache/clear?year=2023&confirm=true"

# Clear specific tile
curl -X DELETE "http://localhost:8000/api/cache/clear?x=123&y=456&z=10&confirm=true"
```

### Cache Warming

#### POST /api/cache/warmup
Start cache warming process by simulating webmap request patterns.

**Request Body:**
```json
{
  "layer": "landsat",
  "params": {},
  "max_tiles": 500,
  "batch_size": 50,
  "patterns": ["spiral", "grid"],
  "regions": [
    {"min_lat": -20, "max_lat": -10, "min_lon": -50, "max_lon": -40}
  ]
}
```

**Response:**
```json
{
  "status": "scheduled",
  "message": "Cache warmup scheduled for 500 tiles",
  "data": {
    "task_id": "abc123",
    "total_tiles": 500,
    "batches": 10,
    "estimated_time_minutes": 20
  },
  "timestamp": "2024-01-15T10:30:00"
}
```

#### POST /api/cache/analyze-patterns
Analyze usage patterns for cache optimization.

**Query Parameters:**
- `days` (default: 7): Number of days to analyze (1-30)

**Response:**
```json
{
  "status": "analyzing",
  "message": "Analyzing patterns from last 7 days",
  "data": {"task_id": "xyz789"},
  "timestamp": "2024-01-15T10:30:00"
}
```

#### GET /api/cache/recommendations
Get recommendations for cache optimization based on usage patterns.

**Response:**
```json
{
  "recommendations": [
    {
      "type": "popular_region",
      "priority": "high",
      "region_id": 0,
      "bounds": {
        "min_lat": -20,
        "max_lat": -10,
        "min_lon": -50,
        "max_lon": -40
      },
      "recommended_zoom_levels": [11, 12, 13],
      "estimated_tiles": 500
    },
    {
      "type": "zoom_optimization",
      "priority": "medium",
      "recommended_zooms": [12, 13, 14],
      "reason": "Most used zoom levels"
    }
  ],
  "total_recommendations": 2
}
```

### TVI Point/Campaign Management (Protected)

These endpoints require super-admin authentication.

#### POST /api/cache/point/start
Start async cache generation for a specific point.

**Request Body:**
```json
{
  "point_id": "1000_yssp_europe"
}
```

**Response:**
```json
{
  "status": "started",
  "message": "Cache task started for point 1000_yssp_europe",
  "data": {
    "task_id": "task123",
    "point_id": "1000_yssp_europe"
  },
  "timestamp": "2024-01-15T10:30:00"
}
```

#### POST /api/cache/campaign/start
Start async cache generation for all points in a campaign.

**Request Body:**
```json
{
  "campaign_id": "mapbiomas_85k_col5_caatinga2",
  "batch_size": 5
}
```

**Response:**
```json
{
  "status": "started",
  "message": "Cache task started for campaign mapbiomas_85k_col5_caatinga2",
  "data": {
    "task_id": "task456",
    "campaign_id": "mapbiomas_85k_col5_caatinga2",
    "point_count": 150,
    "batch_size": 5
  },
  "timestamp": "2024-01-15T10:30:00"
}
```

#### GET /api/cache/point/{point_id}/status
Get cache status for a specific point.

**Response:**
```json
{
  "status": "success",
  "message": "Cache status for point 1000_yssp_europe",
  "data": {
    "point_id": "1000_yssp_europe",
    "cached": true,
    "cached_at": "2024-01-15T09:00:00",
    "cached_by": "celery-task"
  },
  "timestamp": "2024-01-15T10:30:00"
}
```

#### GET /api/cache/campaign/{campaign_id}/status
Get aggregated cache status for all points in a campaign.

**Response:**
```json
{
  "status": "success",
  "message": "Cache status for campaign mapbiomas_85k_col5_caatinga2",
  "data": {
    "campaign_id": "mapbiomas_85k_col5_caatinga2",
    "total_points": 150,
    "cached_points": 120,
    "cache_percentage": 80.0
  },
  "timestamp": "2024-01-15T10:30:00"
}
```

#### DELETE /api/cache/point/{point_id}
Clear cache for a specific point.

**Response:**
```json
{
  "status": "cleared",
  "message": "Cache cleared for point 1000_yssp_europe",
  "data": {
    "point_id": "1000_yssp_europe"
  },
  "timestamp": "2024-01-15T10:30:00"
}
```

#### DELETE /api/cache/campaign/{campaign_id}
Clear cache for all points in a campaign.

**Response:**
```json
{
  "status": "cleared",
  "message": "Cache cleared for campaign mapbiomas_85k_col5_caatinga2",
  "data": {
    "campaign_id": "mapbiomas_85k_col5_caatinga2",
    "points_cleared": 150
  },
  "timestamp": "2024-01-15T10:30:00"
}
```

### Task Management

#### GET /api/cache/tasks/{task_id}
Get status of any cache-related Celery task.

**Response:**
```json
{
  "status": "success",
  "message": "Task task123 status",
  "data": {
    "task_id": "task123",
    "state": "SUCCESS",
    "ready": true,
    "successful": true,
    "result": {
      "status": "completed",
      "point_id": "1000_yssp_europe",
      "total_tiles": 500,
      "successful_tiles": 480,
      "failed_tiles": 20
    },
    "error": null
  },
  "timestamp": "2024-01-15T10:30:00"
}
```

## Cache Generation Details

When caching points for TVI:

1. **Tiles Generated Per Point:**
   - All `visParamsEnable` options from campaign
   - All years from `initialYear` to `finalYear`
   - Zoom levels: 12, 13, 14

2. **Rate Limiting:**
   - 100ms delay between GEE requests
   - Maximum 10 concurrent GEE requests
   - Batch processing to avoid overwhelming the system

3. **Cache Storage:**
   - Redis for fast access
   - Disk cache for persistence
   - Hybrid approach for optimal performance

## Example Usage

### Complete Flow for Caching a Campaign

```bash
# 1. Start cache generation for campaign
curl -X POST "http://localhost:8000/api/cache/campaign/start" \
  -u "admin:password" \
  -H "Content-Type: application/json" \
  -d '{"campaign_id": "mapbiomas_85k_col5_caatinga2", "batch_size": 10}'

# Response: {"data": {"task_id": "abc123"}, ...}

# 2. Monitor task progress
curl "http://localhost:8000/api/cache/tasks/abc123"

# 3. Check campaign cache status
curl "http://localhost:8000/api/cache/campaign/mapbiomas_85k_col5_caatinga2/status" \
  -u "admin:password"

# 4. Get cache statistics
curl "http://localhost:8000/api/cache/stats"
```

## Backend Integration Examples

### Node.js/JavaScript Example

```javascript
const axios = require('axios');

class TilesCacheAPI {
  constructor(baseURL, username, password) {
    this.client = axios.create({
      baseURL,
      auth: { username, password },
      headers: { 'Content-Type': 'application/json' }
    });
  }

  // Start caching for a point
  async cachePoint(pointId) {
    const response = await this.client.post('/api/cache/point/start', {
      point_id: pointId
    });
    return response.data;
  }

  // Start caching for a campaign
  async cacheCampaign(campaignId, batchSize = 5) {
    const response = await this.client.post('/api/cache/campaign/start', {
      campaign_id: campaignId,
      batch_size: batchSize
    });
    return response.data;
  }

  // Monitor task status
  async getTaskStatus(taskId) {
    const response = await this.client.get(`/api/cache/tasks/${taskId}`);
    return response.data;
  }

  // Poll task until completion
  async waitForTask(taskId, maxRetries = 60, delay = 5000) {
    for (let i = 0; i < maxRetries; i++) {
      const status = await this.getTaskStatus(taskId);
      if (status.data.ready) {
        return status;
      }
      await new Promise(resolve => setTimeout(resolve, delay));
    }
    throw new Error('Task timeout');
  }
}

// Usage
const api = new TilesCacheAPI('http://localhost:8000', 'admin', 'password');

// Cache a campaign with monitoring
async function cacheCampaignWithMonitoring(campaignId) {
  try {
    // Start cache task
    const { data } = await api.cacheCampaign(campaignId, 10);
    console.log(`Task started: ${data.task_id}`);
    
    // Wait for completion
    const result = await api.waitForTask(data.task_id);
    console.log('Task completed:', result);
    
    // Check final status
    const status = await api.client.get(`/api/cache/campaign/${campaignId}/status`);
    console.log('Cache status:', status.data);
  } catch (error) {
    console.error('Error:', error.response?.data || error.message);
  }
}
```

### Python Integration Example

```python
import requests
import time
from typing import Dict, Any, Optional
from requests.auth import HTTPBasicAuth

class TilesCacheAPI:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.auth = HTTPBasicAuth(username, password)
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update({'Content-Type': 'application/json'})
    
    def cache_point(self, point_id: str) -> Dict[str, Any]:
        """Start caching for a specific point"""
        response = self.session.post(
            f"{self.base_url}/api/cache/point/start",
            json={"point_id": point_id}
        )
        response.raise_for_status()
        return response.json()
    
    def cache_campaign(self, campaign_id: str, batch_size: int = 5) -> Dict[str, Any]:
        """Start caching for all points in a campaign"""
        response = self.session.post(
            f"{self.base_url}/api/cache/campaign/start",
            json={"campaign_id": campaign_id, "batch_size": batch_size}
        )
        response.raise_for_status()
        return response.json()
    
    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get status of a cache task"""
        response = self.session.get(f"{self.base_url}/api/cache/tasks/{task_id}")
        response.raise_for_status()
        return response.json()
    
    def wait_for_task(self, task_id: str, timeout: int = 300, poll_interval: int = 5) -> Dict[str, Any]:
        """Wait for a task to complete with timeout"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            status = self.get_task_status(task_id)
            
            if status['data']['ready']:
                return status
            
            time.sleep(poll_interval)
        
        raise TimeoutError(f"Task {task_id} did not complete within {timeout} seconds")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        response = self.session.get(f"{self.base_url}/api/cache/stats")
        response.raise_for_status()
        return response.json()
    
    def clear_cache(self, layer: Optional[str] = None, year: Optional[int] = None) -> Dict[str, Any]:
        """Clear cache with filters"""
        params = {"confirm": "true"}
        if layer:
            params["layer"] = layer
        if year:
            params["year"] = year
            
        response = self.session.delete(
            f"{self.base_url}/api/cache/clear",
            params=params
        )
        response.raise_for_status()
        return response.json()

# Example usage with error handling
def cache_campaign_with_retry(api: TilesCacheAPI, campaign_id: str, max_retries: int = 3):
    """Cache a campaign with retry logic"""
    for attempt in range(max_retries):
        try:
            # Start cache task
            result = api.cache_campaign(campaign_id, batch_size=10)
            task_id = result['data']['task_id']
            print(f"Started cache task: {task_id}")
            
            # Wait for completion
            final_status = api.wait_for_task(task_id, timeout=600)
            
            if final_status['data']['successful']:
                print(f"Cache completed successfully for campaign {campaign_id}")
                return final_status
            else:
                error = final_status['data'].get('error', 'Unknown error')
                print(f"Cache failed: {error}")
                
        except requests.exceptions.RequestException as e:
            print(f"Request error on attempt {attempt + 1}: {e}")
            if attempt == max_retries - 1:
                raise
        except TimeoutError as e:
            print(f"Timeout on attempt {attempt + 1}: {e}")
            if attempt == max_retries - 1:
                raise
    
    return None

# Initialize API client
api = TilesCacheAPI('http://localhost:8000', 'admin', 'password')

# Cache a campaign
try:
    result = cache_campaign_with_retry(api, 'mapbiomas_85k_col5_caatinga2')
    print("Final result:", result)
except Exception as e:
    print(f"Failed to cache campaign: {e}")
```

## MongoDB Configuration

### Connection Settings
The system connects to MongoDB using the following environment variables:
- `MONGODB_URL`: MongoDB connection string (default: `mongodb://localhost:27017`)
- `MONGODB_DB`: Database name (default: `tvi`)

Example connection string for production:
```
MONGODB_URL=mongodb://username:password@host:port/database?authSource=admin
```

## MongoDB Models

The cache system integrates with the following MongoDB collections:

### Users Collection
```javascript
{
  "_id": ObjectId("..."),
  "email": "admin@example.com",
  "password": "hashed_password", // SHA256 or plain text
  "role": "super-admin",          // Required: "super-admin"
  "type": "admin",                // Alternative: "admin"
  "active": true
}
```

### Campaigns Collection
```javascript
{
  "_id": "mapbiomas_85k_col5_caatinga2",
  "name": "MapBiomas Caatinga",
  "initialYear": 2000,
  "finalYear": 2023,
  "visParamsEnable": ["tvi-red", "tvi-green", "tvi-blue"],
  "active": true
}
```

### Points Collection
```javascript
{
  "_id": "1000_yssp_europe",
  "campaign": "mapbiomas_85k_col5_caatinga2",
  "lon": -44.123,
  "lat": -10.456,
  "cached": false,              // Updated to true after caching
  "cachedAt": null,            // Updated with timestamp
  "cachedBy": null,            // Updated with "job"
  "enhance_in_cache": 0        // Cache counter
}
```

## Performance Considerations

### Rate Limiting
- Google Earth Engine requests are rate-limited to prevent API quota exhaustion
- 100ms delay between consecutive GEE requests
- Maximum 10 concurrent GEE requests per task
- Batch processing for campaigns to avoid overwhelming the system

### Tile Generation Strategy
For each point, the system generates:
- **Zoom levels**: 12, 13, 14 (3 levels)
- **Years**: All years from `initialYear` to `finalYear` (e.g., 24 years)
- **VisParams**: All options in `visParamsEnable` (e.g., 3 params)
- **Total tiles per point**: ~216 tiles (3 × 24 × 3)

### Tile URL Format
The tiles are cached and accessible via the following endpoints:
- **Sentinel-2**: `/api/layers/s2_harmonized/{x}/{y}/{z}?period={period}&year={year}&visparam={visparam}`
- **Landsat**: `/api/layers/landsat/{x}/{y}/{z}?period={period}&year={year}&month={month}&visparam={visparam}`

Where:
- `{x}`, `{y}`, `{z}`: Tile coordinates following the Slippy Map convention
- `period`: "WET", "DRY", or "MONTH"
- `year`: Year from campaign's initialYear to finalYear
- `month`: Month number (1-12) when period="MONTH"
- `visparam`: One of the visParamsEnable options from the campaign

### Batch Processing
When caching campaigns:
- Points are processed in configurable batches (default: 5)
- Each batch runs as a separate Celery task
- Prevents memory overflow for large campaigns
- Allows partial completion tracking

## Error Handling

All endpoints return consistent error responses:

```json
{
  "detail": "Error message describing what went wrong"
}
```

Common HTTP status codes:
- `400`: Bad Request (invalid parameters)
- `401`: Unauthorized (authentication required)
- `403`: Forbidden (insufficient permissions)
- `404`: Not Found (resource doesn't exist)
- `500`: Internal Server Error

### Specific Error Scenarios

1. **Authentication Errors**
```json
{
  "detail": "Invalid authentication credentials"
}
```

2. **Permission Errors**
```json
{
  "detail": "User does not have super-admin role"
}
```

3. **Resource Not Found**
```json
{
  "detail": "Point 1000_invalid not found"
}
```

4. **Already Cached**
```json
{
  "status": "already_cached",
  "message": "Point 1000_yssp_europe is already cached",
  "data": {
    "point_id": "1000_yssp_europe",
    "cached_at": "2024-01-15T09:00:00",
    "cached_by": "job"
  }
}
```

## Best Practices

1. **Authentication**
   - Store credentials securely (environment variables, secrets manager)
   - Use HTTPS in production to protect credentials
   - Rotate credentials regularly

2. **Task Management**
   - Always monitor task status for long-running operations
   - Implement timeout logic to avoid infinite waiting
   - Handle partial failures gracefully

3. **Batch Processing**
   - Use appropriate batch sizes based on system capacity
   - Start with smaller batches (5-10) and increase if stable
   - Monitor system resources during large campaign caching

4. **Error Recovery**
   - Implement retry logic for transient failures
   - Log all errors for debugging
   - Clear failed cache entries before retrying

5. **Resource Management**
   - Monitor disk space for cache storage
   - Implement cache cleanup strategies
   - Track cache hit rates to optimize performance