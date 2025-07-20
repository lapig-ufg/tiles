"""
Cleanup and maintenance tasks
Handles cache cleanup, orphaned object removal, and optimization
"""
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from collections import defaultdict
import aioboto3
from botocore.exceptions import ClientError
from celery.schedules import crontab
from loguru import logger

from app.tasks.celery_app import celery_app
from app.cache.cache_hybrid import tile_cache
from app.core.mongodb import connect_to_mongo, get_tile_errors_collection
from app.core.config import settings
import redis.asyncio as redis


class CleanupStats:
    """Track cleanup operation statistics"""
    def __init__(self):
        self.redis_expired = 0
        self.redis_deleted = 0
        self.s3_orphaned = 0
        self.s3_deleted = 0
        self.s3_errors = 0
        self.total_space_freed_mb = 0
        self.start_time = datetime.now()
        self.errors = []
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert stats to dictionary"""
        duration = (datetime.now() - self.start_time).total_seconds()
        return {
            "redis": {
                "expired_keys": self.redis_expired,
                "deleted_keys": self.redis_deleted,
                "total_cleaned": self.redis_expired + self.redis_deleted
            },
            "s3": {
                "orphaned_objects": self.s3_orphaned,
                "deleted_objects": self.s3_deleted,
                "failed_deletions": self.s3_errors,
                "space_freed_mb": round(self.total_space_freed_mb, 2)
            },
            "summary": {
                "total_items_cleaned": self.redis_deleted + self.s3_deleted,
                "duration_seconds": round(duration, 2),
                "items_per_second": round((self.redis_deleted + self.s3_deleted) / max(duration, 1), 2),
                "errors_count": len(self.errors),
                "completed_at": datetime.now().isoformat()
            },
            "errors": self.errors[:10]  # First 10 errors
        }


@celery_app.task(bind=True, max_retries=3, queue='maintenance')
def cleanup_expired_cache(self, dry_run: bool = False, 
                         max_items: Optional[int] = None) -> Dict[str, Any]:
    """
    Clean up expired cache entries based on TTL
    
    Args:
        dry_run: If True, only report what would be cleaned
        max_items: Maximum items to process (for testing)
        
    Returns:
        Dict with cleanup statistics
    """
    async def _cleanup():
        stats = CleanupStats()
        
        try:
            await connect_to_mongo()
            await tile_cache.initialize()
            
            # Phase 1: Find expired Redis keys
            logger.info("Phase 1: Scanning for expired Redis entries...")
            expired_redis_keys = await _find_expired_redis_keys(stats, max_items)
            
            # Phase 2: Find orphaned S3 objects
            logger.info("Phase 2: Scanning for orphaned S3 objects...")
            orphaned_s3_keys = await _find_orphaned_s3_objects(
                stats, expired_redis_keys, max_items
            )
            
            if not dry_run:
                # Phase 3: Clean up Redis entries
                logger.info("Phase 3: Cleaning up Redis entries...")
                await _cleanup_redis_entries(stats, expired_redis_keys)
                
                # Phase 4: Clean up S3 objects
                logger.info("Phase 4: Cleaning up S3 objects...")
                await _cleanup_s3_objects(stats, orphaned_s3_keys)
                
                # Phase 5: Log cleanup operation
                await _log_cleanup_operation(stats)
            else:
                logger.info("DRY RUN - No actual deletions performed")
                stats.errors.append({
                    "type": "info",
                    "message": "Dry run mode - no deletions performed"
                })
            
            return stats.to_dict()
            
        except Exception as e:
            logger.exception(f"Error in cache cleanup: {e}")
            stats.errors.append({
                "type": "fatal",
                "message": str(e),
                "timestamp": datetime.now().isoformat()
            })
            raise self.retry(exc=e, countdown=300)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_cleanup())
    finally:
        loop.close()


@celery_app.task(bind=True, max_retries=2, queue='maintenance')
def cleanup_orphaned_objects(self, bucket_prefix: Optional[str] = None,
                           max_objects: int = 1000) -> Dict[str, Any]:
    """
    Clean up orphaned S3 objects without Redis metadata
    
    Args:
        bucket_prefix: Optional prefix to limit scope
        max_objects: Maximum objects to process
        
    Returns:
        Dict with cleanup results
    """
    async def _cleanup_orphaned():
        stats = {
            "scanned": 0,
            "orphaned": 0,
            "deleted": 0,
            "errors": 0,
            "space_freed_mb": 0
        }
        
        try:
            await tile_cache.initialize()
            
            async with tile_cache.s3_session.client(
                's3',
                endpoint_url=tile_cache.s3_endpoint,
                aws_access_key_id=settings.get("S3_ACCESS_KEY"),
                aws_secret_access_key=settings.get("S3_SECRET_KEY"),
            ) as s3:
                
                # List objects
                paginator = s3.get_paginator('list_objects_v2')
                params = {"Bucket": tile_cache.s3_bucket}
                if bucket_prefix:
                    params["Prefix"] = bucket_prefix
                
                orphaned_objects = []
                
                async for page in paginator.paginate(**params):
                    if 'Contents' not in page:
                        continue
                    
                    for obj in page['Contents']:
                        if stats["scanned"] >= max_objects:
                            break
                        
                        stats["scanned"] += 1
                        s3_key = obj['Key']
                        
                        # Check if metadata exists
                        if await _is_orphaned_object(s3_key):
                            orphaned_objects.append({
                                'Key': s3_key,
                                'Size': obj['Size']
                            })
                            stats["orphaned"] += 1
                            stats["space_freed_mb"] += obj['Size'] / (1024 * 1024)
                
                # Delete orphaned objects in batches
                if orphaned_objects:
                    for i in range(0, len(orphaned_objects), 100):
                        batch = orphaned_objects[i:i+100]
                        delete_objects = [{'Key': obj['Key']} for obj in batch]
                        
                        try:
                            response = await s3.delete_objects(
                                Bucket=tile_cache.s3_bucket,
                                Delete={'Objects': delete_objects}
                            )
                            stats["deleted"] += len(response.get('Deleted', []))
                            stats["errors"] += len(response.get('Errors', []))
                        except Exception as e:
                            logger.error(f"Error deleting batch: {e}")
                            stats["errors"] += len(batch)
            
            return stats
            
        except Exception as e:
            logger.exception(f"Error cleaning orphaned objects: {e}")
            raise self.retry(exc=e, countdown=180)
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_cleanup_orphaned())
    finally:
        loop.close()


@celery_app.task(bind=True, queue='maintenance')
def cleanup_analyze_usage(self, days: int = 30) -> Dict[str, Any]:
    """
    Analyze cache usage patterns for optimization
    
    Args:
        days: Number of days to analyze
        
    Returns:
        Dict with usage analysis
    """
    async def _analyze():
        analysis = {
            "period_days": days,
            "cache_age_distribution": defaultdict(int),
            "ttl_distribution": defaultdict(int),
            "size_distribution": defaultdict(int),
            "recommendations": []
        }
        
        try:
            await tile_cache.initialize()
            
            sample_count = 0
            total_size_mb = 0
            
            async with tile_cache._get_redis() as r:
                # Sample keys for analysis
                async for key in r.scan_iter(match="tile:*", count=100):
                    if sample_count >= 1000:  # Analyze 1000 samples
                        break
                    
                    sample_count += 1
                    key_str = key.decode() if isinstance(key, bytes) else key
                    
                    # Get metadata
                    meta = await r.hgetall(key)
                    if not meta:
                        continue
                    
                    # Analyze age
                    if meta.get(b'created'):
                        created = datetime.fromisoformat(meta[b'created'].decode())
                        age_days = (datetime.now() - created).days
                        
                        if age_days < 1:
                            analysis["cache_age_distribution"]["< 1 day"] += 1
                        elif age_days < 7:
                            analysis["cache_age_distribution"]["1-7 days"] += 1
                        elif age_days < 30:
                            analysis["cache_age_distribution"]["7-30 days"] += 1
                        elif age_days < 90:
                            analysis["cache_age_distribution"]["30-90 days"] += 1
                        else:
                            analysis["cache_age_distribution"]["> 90 days"] += 1
                    
                    # Analyze TTL
                    ttl = await r.ttl(key)
                    if ttl > 0:
                        ttl_days = ttl // 86400
                        if ttl_days < 7:
                            analysis["ttl_distribution"]["< 7 days"] += 1
                        elif ttl_days < 30:
                            analysis["ttl_distribution"]["7-30 days"] += 1
                        elif ttl_days < 90:
                            analysis["ttl_distribution"]["30-90 days"] += 1
                        else:
                            analysis["ttl_distribution"]["> 90 days"] += 1
                    
                    # Analyze size
                    if meta.get(b'size'):
                        size_bytes = int(meta[b'size'])
                        size_mb = size_bytes / (1024 * 1024)
                        total_size_mb += size_mb
                        
                        if size_mb < 0.1:
                            analysis["size_distribution"]["< 100KB"] += 1
                        elif size_mb < 1:
                            analysis["size_distribution"]["100KB-1MB"] += 1
                        elif size_mb < 10:
                            analysis["size_distribution"]["1MB-10MB"] += 1
                        else:
                            analysis["size_distribution"]["> 10MB"] += 1
            
            # Generate recommendations
            analysis["recommendations"] = _generate_recommendations(
                analysis, sample_count, total_size_mb
            )
            
            # Convert defaultdicts to regular dicts
            analysis["cache_age_distribution"] = dict(analysis["cache_age_distribution"])
            analysis["ttl_distribution"] = dict(analysis["ttl_distribution"])
            analysis["size_distribution"] = dict(analysis["size_distribution"])
            
            analysis["summary"] = {
                "samples_analyzed": sample_count,
                "average_size_mb": round(total_size_mb / max(sample_count, 1), 2),
                "total_size_sampled_mb": round(total_size_mb, 2)
            }
            
            return analysis
            
        except Exception as e:
            logger.exception(f"Error analyzing cache usage: {e}")
            return {"error": str(e)}
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_analyze())
    finally:
        loop.close()


@celery_app.task(queue='maintenance')
def cleanup_optimize_cache() -> Dict[str, Any]:
    """
    Optimize cache by reorganizing and compacting data
    
    Returns:
        Dict with optimization results
    """
    return {
        "status": "not_implemented",
        "message": "Cache optimization will be implemented based on usage patterns"
    }


# Helper functions
async def _find_expired_redis_keys(stats: CleanupStats, 
                                  max_items: Optional[int] = None) -> Dict[str, List[str]]:
    """Find Redis keys that are expired or near expiration"""
    expired_keys = {
        "tile": [],
        "meta": [],
        "other": []
    }
    
    processed = 0
    
    async with tile_cache._get_redis() as r:
        async for key in r.scan_iter(count=100):
            if max_items and processed >= max_items:
                break
            
            processed += 1
            key_str = key.decode() if isinstance(key, bytes) else key
            
            ttl = await r.ttl(key)
            
            # Check for anomalies or near-expiration
            if ttl == -1:  # No TTL set (shouldn't happen)
                stats.redis_expired += 1
                _categorize_key(key_str, expired_keys)
            elif 0 <= ttl < 86400:  # Expiring within 24 hours
                stats.redis_expired += 1
                _categorize_key(key_str, expired_keys)
    
    logger.info(f"Found {stats.redis_expired} expired/expiring Redis keys")
    return expired_keys


async def _find_orphaned_s3_objects(stats: CleanupStats,
                                   expired_redis_keys: Dict[str, List[str]],
                                   max_items: Optional[int] = None) -> List[Dict[str, Any]]:
    """Find S3 objects without corresponding Redis metadata"""
    orphaned_objects = []
    
    try:
        # Get S3 keys from expired entries
        s3_keys_to_check = set()
        
        async with tile_cache._get_redis() as r:
            for tile_key in expired_redis_keys["tile"]:
                meta = await r.hgetall(tile_key)
                if meta and meta.get(b's3_key'):
                    s3_keys_to_check.add(meta[b's3_key'].decode())
        
        # Scan S3 for orphaned objects
        async with tile_cache.s3_session.client(
            's3',
            endpoint_url=tile_cache.s3_endpoint,
            aws_access_key_id=settings.get("S3_ACCESS_KEY"),
            aws_secret_access_key=settings.get("S3_SECRET_KEY"),
        ) as s3:
            paginator = s3.get_paginator('list_objects_v2')
            
            checked = 0
            async for page in paginator.paginate(Bucket=tile_cache.s3_bucket):
                if 'Contents' not in page:
                    continue
                
                for obj in page['Contents']:
                    if max_items and checked >= max_items:
                        break
                    
                    checked += 1
                    s3_key = obj['Key']
                    
                    if s3_key in s3_keys_to_check or await _is_orphaned_object(s3_key):
                        orphaned_objects.append({
                            'Key': s3_key,
                            'Size': obj['Size'],
                            'LastModified': obj['LastModified']
                        })
                        stats.s3_orphaned += 1
                        stats.total_space_freed_mb += obj['Size'] / (1024 * 1024)
    
    except Exception as e:
        logger.error(f"Error scanning S3 objects: {e}")
        stats.errors.append({
            "type": "s3_scan_error",
            "message": str(e)
        })
    
    logger.info(f"Found {stats.s3_orphaned} orphaned S3 objects")
    return orphaned_objects


async def _cleanup_redis_entries(stats: CleanupStats, expired_keys: Dict[str, List[str]]):
    """Delete expired Redis entries"""
    async with tile_cache._get_redis() as r:
        batch_size = 100
        
        for key_type, keys in expired_keys.items():
            for i in range(0, len(keys), batch_size):
                batch = keys[i:i+batch_size]
                if batch:
                    try:
                        deleted = await r.delete(*batch)
                        stats.redis_deleted += deleted
                        logger.info(f"Deleted {deleted} {key_type} keys from Redis")
                    except Exception as e:
                        logger.error(f"Error deleting Redis keys: {e}")
                        stats.errors.append({
                            "type": "redis_delete_error",
                            "message": str(e)
                        })


async def _cleanup_s3_objects(stats: CleanupStats, orphaned_objects: List[Dict[str, Any]]):
    """Delete orphaned S3 objects"""
    if not orphaned_objects:
        return
    
    async with tile_cache.s3_session.client(
        's3',
        endpoint_url=tile_cache.s3_endpoint,
        aws_access_key_id=settings.get("S3_ACCESS_KEY"),
        aws_secret_access_key=settings.get("S3_SECRET_KEY"),
    ) as s3:
        batch_size = 1000
        
        for i in range(0, len(orphaned_objects), batch_size):
            batch = orphaned_objects[i:i+batch_size]
            objects_to_delete = [{'Key': obj['Key']} for obj in batch]
            
            try:
                response = await s3.delete_objects(
                    Bucket=tile_cache.s3_bucket,
                    Delete={'Objects': objects_to_delete}
                )
                
                stats.s3_deleted += len(response.get('Deleted', []))
                stats.s3_errors += len(response.get('Errors', []))
                
                logger.info(f"Deleted {len(response.get('Deleted', []))} S3 objects")
                
            except Exception as e:
                logger.error(f"Error deleting S3 objects: {e}")
                stats.s3_errors += len(batch)
                stats.errors.append({
                    "type": "s3_batch_delete_error",
                    "message": str(e)
                })


async def _log_cleanup_operation(stats: CleanupStats):
    """Log cleanup operation for auditing"""
    try:
        from app.core.mongodb import get_db
        db = await get_db()
        
        cleanup_logs = db.cleanup_logs
        
        await cleanup_logs.insert_one({
            "timestamp": stats.start_time,
            "duration_seconds": (datetime.now() - stats.start_time).total_seconds(),
            "redis_expired": stats.redis_expired,
            "redis_deleted": stats.redis_deleted,
            "s3_orphaned": stats.s3_orphaned,
            "s3_deleted": stats.s3_deleted,
            "s3_errors": stats.s3_errors,
            "space_freed_mb": stats.total_space_freed_mb,
            "errors": stats.errors,
            "completed_at": datetime.now()
        })
        
        logger.info("Cleanup operation logged to MongoDB")
        
    except Exception as e:
        logger.error(f"Failed to log cleanup operation: {e}")


def _categorize_key(key: str, expired_keys: Dict[str, List[str]]):
    """Categorize a Redis key"""
    if key.startswith("tile:"):
        expired_keys["tile"].append(key)
    elif key.startswith("meta:"):
        expired_keys["meta"].append(key)
    else:
        expired_keys["other"].append(key)


async def _is_orphaned_object(s3_key: str) -> bool:
    """Check if S3 object is orphaned"""
    # Extract metadata key from S3 key
    if s3_key.startswith('tiles/'):
        parts = s3_key.split('/', 2)
        if len(parts) == 3:
            tile_key = parts[2]
            
            async with tile_cache._get_redis() as r:
                exists = await r.exists(f"tile:{tile_key}")
                return not exists
    
    return False


def _generate_recommendations(analysis: Dict[str, Any], 
                            sample_count: int,
                            total_size_mb: float) -> List[str]:
    """Generate optimization recommendations"""
    recommendations = []
    
    if sample_count == 0:
        return ["No data available for analysis"]
    
    # Age-based recommendations
    old_items = analysis["cache_age_distribution"].get("> 90 days", 0)
    if old_items > sample_count * 0.2:
        recommendations.append(
            f"Consider reducing TTL: {old_items / sample_count * 100:.1f}% of items are older than 90 days"
        )
    
    # Size-based recommendations
    avg_size = total_size_mb / sample_count
    if avg_size > 5:
        recommendations.append(
            f"Large average tile size ({avg_size:.1f}MB) - consider compression or resolution optimization"
        )
    
    # TTL alignment
    long_ttl = analysis["ttl_distribution"].get("> 90 days", 0)
    if long_ttl > sample_count * 0.8 and old_items < sample_count * 0.1:
        recommendations.append(
            "TTL may be too long - most items expire before being that old"
        )
    
    return recommendations


# Schedule periodic cleanup tasks
celery_app.conf.beat_schedule.update({
    'cleanup-expired-daily': {
        'task': 'app.tasks.cleanup_tasks.cleanup_expired_cache',
        'schedule': crontab(hour=3, minute=0),  # 3 AM daily
        'args': (False, None),  # dry_run=False, max_items=None
    },
    'cleanup-orphaned-weekly': {
        'task': 'app.tasks.cleanup_tasks.cleanup_orphaned_objects',
        'schedule': crontab(day_of_week=0, hour=4, minute=0),  # Sunday 4 AM
        'kwargs': {'max_objects': 10000},
    },
    'analyze-usage-weekly': {
        'task': 'app.tasks.cleanup_tasks.cleanup_analyze_usage',
        'schedule': crontab(day_of_week=1, hour=4, minute=0),  # Monday 4 AM
        'kwargs': {'days': 30},
    },
})