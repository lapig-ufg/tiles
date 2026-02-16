"""
Monitoring and analytics tasks
Handles metrics collection, pattern analysis, and reporting
"""
import asyncio
from collections import defaultdict, Counter
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from celery.schedules import crontab
from loguru import logger

from app.cache.cache_hybrid import tile_cache
from app.core.config import settings
from app.core.mongodb import connect_to_mongo, get_database
from app.tasks.celery_app import celery_app


@celery_app.task(bind=True, queue='low_priority')
def monitor_collect_metrics(self, hours: int = 24) -> Dict[str, Any]:
    """
    Collect system metrics for monitoring
    
    Args:
        hours: Number of hours to collect metrics for
        
    Returns:
        Dict with collected metrics
    """
    async def _collect_metrics():
        metrics = {
            "period_hours": hours,
            "timestamp": datetime.now().isoformat(),
            "cache": {},
            "tasks": {},
            "performance": {},
            "errors": []
        }
        
        try:
            await connect_to_mongo()
            await tile_cache.initialize()
            
            # Cache metrics
            metrics["cache"] = await _collect_cache_metrics()
            
            # Task metrics
            metrics["tasks"] = await _collect_task_metrics(hours)
            
            # Performance metrics
            metrics["performance"] = await _collect_performance_metrics(hours)
            
            # Error metrics
            metrics["errors"] = await _collect_error_metrics(hours)
            
            # Store metrics in database
            await _store_metrics(metrics)
            
            return metrics
            
        except Exception as e:
            logger.exception(f"Error collecting metrics: {e}")
            return {"error": str(e)}
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_collect_metrics())
    finally:
        loop.close()


@celery_app.task(bind=True, queue='low_priority')
def monitor_analyze_patterns(self, days: int = 7) -> Dict[str, Any]:
    """
    Analyze usage patterns for optimization
    
    Args:
        days: Number of days to analyze
        
    Returns:
        Dict with pattern analysis
    """
    async def _analyze_patterns():
        analysis = {
            "period_days": days,
            "timestamp": datetime.now().isoformat(),
            "usage_patterns": {},
            "peak_times": {},
            "popular_regions": {},
            "recommendations": []
        }
        
        try:
            await connect_to_mongo()
            db = get_database()
            
            # Get metrics from the past N days
            start_date = datetime.now() - timedelta(days=days)
            
            metrics_collection = db.system_metrics
            cursor = metrics_collection.find({
                "timestamp": {"$gte": start_date.isoformat()}
            })
            
            all_metrics = await cursor.to_list(length=None)
            
            if not all_metrics:
                analysis["message"] = "No metrics available for analysis"
                return analysis
            
            # Analyze usage patterns
            analysis["usage_patterns"] = _analyze_usage_patterns(all_metrics)
            
            # Analyze peak times
            analysis["peak_times"] = _analyze_peak_times(all_metrics)
            
            # Analyze popular regions
            analysis["popular_regions"] = await _analyze_popular_regions(days)
            
            # Generate recommendations
            analysis["recommendations"] = _generate_pattern_recommendations(analysis)
            
            return analysis
            
        except Exception as e:
            logger.exception(f"Error analyzing patterns: {e}")
            return {"error": str(e)}
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_analyze_patterns())
    finally:
        loop.close()


@celery_app.task(bind=True, queue='low_priority')
def monitor_generate_report(self, report_type: str = "daily",
                          recipient_email: Optional[str] = None) -> Dict[str, Any]:
    """
    Generate monitoring report
    
    Args:
        report_type: Type of report (daily, weekly, monthly)
        recipient_email: Optional email to send report to
        
    Returns:
        Dict with report data
    """
    async def _generate_report():
        report = {
            "type": report_type,
            "generated_at": datetime.now().isoformat(),
            "period": _get_report_period(report_type),
            "sections": {}
        }
        
        try:
            await connect_to_mongo()
            
            # System health
            report["sections"]["system_health"] = await _generate_health_section()
            
            # Cache performance
            report["sections"]["cache_performance"] = await _generate_cache_section()
            
            # Task statistics
            report["sections"]["task_statistics"] = await _generate_task_section()
            
            # Error summary
            report["sections"]["error_summary"] = await _generate_error_section()
            
            # Recommendations
            report["sections"]["recommendations"] = await _generate_recommendations_section()
            
            # Store report
            await _store_report(report)
            
            # Send email if requested
            if recipient_email:
                # TODO: Implement email sending
                report["email_status"] = "Email notification not implemented"
            
            return report
            
        except Exception as e:
            logger.exception(f"Error generating report: {e}")
            return {"error": str(e)}
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_generate_report())
    finally:
        loop.close()


@celery_app.task(queue='low_priority')
def monitor_check_health() -> Dict[str, Any]:
    """
    Quick health check of the system
    
    Returns:
        Dict with health status
    """
    async def _check_health():
        health = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "components": {}
        }
        
        try:
            # Initialize tile cache if needed
            await tile_cache.initialize()
            
            # Check Redis
            health["components"]["redis"] = await _check_redis_health()
            
            # Check S3
            health["components"]["s3"] = await _check_s3_health()
            
            # Check MongoDB
            health["components"]["mongodb"] = await _check_mongodb_health()
            
            # Check Celery
            health["components"]["celery"] = _check_celery_health()
            
            # Overall status
            unhealthy = [k for k, v in health["components"].items() 
                        if v.get("status") != "healthy"]
            
            if unhealthy:
                health["status"] = "degraded"
                health["unhealthy_components"] = unhealthy
            
            return health
            
        except Exception as e:
            logger.exception(f"Error checking health: {e}")
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_check_health())
    finally:
        loop.close()


# Helper functions
async def _collect_cache_metrics() -> Dict[str, Any]:
    """Collect cache-related metrics"""
    metrics = {
        "redis": {},
        "s3": {},
        "hit_rate": 0
    }
    
    try:
        # Redis metrics
        async with tile_cache._get_redis() as r:
            info = await r.info()
            metrics["redis"] = {
                "used_memory_mb": info.get("used_memory", 0) / (1024 * 1024),
                "connected_clients": info.get("connected_clients", 0),
                "total_commands": info.get("total_commands_processed", 0),
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
            }
            
            # Calculate hit rate
            hits = metrics["redis"]["keyspace_hits"]
            misses = metrics["redis"]["keyspace_misses"]
            if hits + misses > 0:
                metrics["hit_rate"] = (hits / (hits + misses)) * 100
        
        # S3 metrics (simplified - in production would use CloudWatch)
        metrics["s3"] = {
            "estimated_objects": await _estimate_s3_objects(),
            "estimated_size_gb": await _estimate_s3_size()
        }
        
    except Exception as e:
        logger.error(f"Error collecting cache metrics: {e}")
    
    return metrics


async def _collect_task_metrics(hours: int) -> Dict[str, Any]:
    """Collect task-related metrics"""
    metrics = {
        "total_tasks": 0,
        "successful": 0,
        "failed": 0,
        "pending": 0,
        "by_type": defaultdict(int)
    }
    
    try:
        # Get task statistics from Celery
        inspect = celery_app.control.inspect()
        
        # Active tasks
        active = inspect.active() or {}
        for worker, tasks in active.items():
            metrics["total_tasks"] += len(tasks)
            for task in tasks:
                metrics["by_type"][task["name"]] += 1
        
        # Reserved tasks
        reserved = inspect.reserved() or {}
        for worker, tasks in reserved.items():
            metrics["pending"] += len(tasks)
        
        # Convert defaultdict to regular dict
        metrics["by_type"] = dict(metrics["by_type"])
        
    except Exception as e:
        logger.error(f"Error collecting task metrics: {e}")
    
    return metrics


async def _collect_performance_metrics(hours: int) -> Dict[str, Any]:
    """Collect performance metrics"""
    metrics = {
        "average_response_time_ms": 0,
        "p95_response_time_ms": 0,
        "requests_per_second": 0,
        "tile_generation_avg_ms": 0
    }
    
    # TODO: Implement actual performance metric collection
    # This would typically come from APM tools or custom logging
    
    return metrics


async def _collect_error_metrics(hours: int) -> List[Dict[str, Any]]:
    """Collect error metrics"""
    errors = []
    
    try:
        db = get_database()
        start_time = datetime.now() - timedelta(hours=hours)
        
        # Get recent errors
        errors_collection = db.tile_errors
        cursor = errors_collection.find({
            "createdAt": {"$gte": start_time}
        }).limit(100)
        
        error_list = await cursor.to_list(length=100)
        
        # Summarize errors by type
        error_counts = Counter(e.get("errorType", "unknown") for e in error_list)
        
        errors = [
            {"type": error_type, "count": count}
            for error_type, count in error_counts.most_common(10)
        ]
        
    except Exception as e:
        logger.error(f"Error collecting error metrics: {e}")
    
    return errors


async def _store_metrics(metrics: Dict[str, Any]):
    """Store metrics in database"""
    try:
        db = get_database()
        metrics_collection = db.system_metrics
        
        await metrics_collection.insert_one(metrics)
        
        # Clean up old metrics (keep 30 days)
        cutoff_date = datetime.now() - timedelta(days=30)
        await metrics_collection.delete_many({
            "timestamp": {"$lt": cutoff_date.isoformat()}
        })
        
    except Exception as e:
        logger.error(f"Error storing metrics: {e}")


def _analyze_usage_patterns(metrics_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze usage patterns from metrics"""
    patterns = {
        "daily_average_requests": 0,
        "peak_day": None,
        "most_used_features": []
    }
    
    if not metrics_list:
        return patterns
    
    # Calculate daily averages
    total_requests = sum(
        m.get("performance", {}).get("requests_per_second", 0) * 3600
        for m in metrics_list
    )
    patterns["daily_average_requests"] = total_requests / len(metrics_list)
    
    # Find peak day
    daily_requests = defaultdict(float)
    for metric in metrics_list:
        date = metric["timestamp"][:10]  # Extract date
        daily_requests[date] += metric.get("performance", {}).get("requests_per_second", 0) * 3600
    
    if daily_requests:
        peak_day = max(daily_requests.items(), key=lambda x: x[1])
        patterns["peak_day"] = {
            "date": peak_day[0],
            "requests": peak_day[1]
        }
    
    # Most used features (from task metrics)
    task_counts = Counter()
    for metric in metrics_list:
        for task_type, count in metric.get("tasks", {}).get("by_type", {}).items():
            task_counts[task_type] += count
    
    patterns["most_used_features"] = [
        {"feature": task, "count": count}
        for task, count in task_counts.most_common(5)
    ]
    
    return patterns


def _analyze_peak_times(metrics_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze peak usage times"""
    hourly_usage = defaultdict(list)
    
    for metric in metrics_list:
        timestamp = datetime.fromisoformat(metric["timestamp"])
        hour = timestamp.hour
        requests = metric.get("performance", {}).get("requests_per_second", 0)
        hourly_usage[hour].append(requests)
    
    # Calculate average requests per hour
    peak_times = {}
    for hour, requests in hourly_usage.items():
        avg_requests = sum(requests) / len(requests) if requests else 0
        peak_times[f"{hour:02d}:00"] = round(avg_requests, 2)
    
    return peak_times


async def _analyze_popular_regions(days: int) -> List[Dict[str, Any]]:
    """Analyze popular geographic regions"""
    # TODO: Implement based on actual tile request logs
    # This would analyze which geographic areas are most frequently requested
    
    return [
        {"region": "São Paulo", "requests": 15000},
        {"region": "Rio de Janeiro", "requests": 12000},
        {"region": "Brasília", "requests": 8000}
    ]


def _generate_pattern_recommendations(analysis: Dict[str, Any]) -> List[str]:
    """Generate recommendations based on pattern analysis"""
    recommendations = []
    
    # Peak time recommendations
    peak_times = analysis.get("peak_times", {})
    if peak_times:
        peak_hour = max(peak_times.items(), key=lambda x: x[1])[0]
        recommendations.append(
            f"Consider pre-warming cache before peak hour at {peak_hour}"
        )
    
    # Popular region recommendations
    popular_regions = analysis.get("popular_regions", [])
    if popular_regions:
        top_region = popular_regions[0]["region"]
        recommendations.append(
            f"Prioritize caching for {top_region} region with highest usage"
        )
    
    # Feature usage recommendations
    usage_patterns = analysis.get("usage_patterns", {})
    most_used = usage_patterns.get("most_used_features", [])
    if most_used:
        top_feature = most_used[0]["feature"]
        recommendations.append(
            f"Optimize performance for '{top_feature}' - most frequently used"
        )
    
    return recommendations


async def _check_redis_health() -> Dict[str, Any]:
    """Check Redis health"""
    try:
        async with tile_cache._get_redis() as r:
            # Ping Redis
            await r.ping()
            
            # Get basic info
            info = await r.info()
            
            return {
                "status": "healthy",
                "uptime_days": info.get("uptime_in_seconds", 0) / 86400,
                "connected_clients": info.get("connected_clients", 0),
                "used_memory_mb": info.get("used_memory", 0) / (1024 * 1024)
            }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


async def _check_s3_health() -> Dict[str, Any]:
    """Check S3 health"""
    try:
        async with tile_cache.s3_session.client(
            's3',
            endpoint_url=tile_cache.s3_endpoint,
            aws_access_key_id=settings.get("S3_ACCESS_KEY"),
            aws_secret_access_key=settings.get("S3_SECRET_KEY"),
            use_ssl=settings.get("S3_USE_SSL",True),  # <-- ADICIONE ISSO
            verify=settings.get("S3_VERIFY_SSL", True) 
        ) as s3:
            # List buckets to verify connection
            await s3.list_buckets()
            
            return {
                "status": "healthy",
                "bucket": tile_cache.s3_bucket
            }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


async def _check_mongodb_health() -> Dict[str, Any]:
    """Check MongoDB health"""
    try:
        from app.core.mongodb import mongodb
        
        # Check if MongoDB is initialized
        if mongodb.client is None or mongodb.database is None:
            return {
                "status": "unhealthy",
                "error": "MongoDB not connected"
            }
        
        # Ping database
        await mongodb.database.command("ping")
        
        return {
            "status": "healthy",
            "database": mongodb.database.name
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


def _check_celery_health() -> Dict[str, Any]:
    """Check Celery health"""
    try:
        inspect = celery_app.control.inspect()
        stats = inspect.stats()
        
        if stats:
            return {
                "status": "healthy",
                "workers": len(stats),
                "active_workers": list(stats.keys())
            }
        else:
            return {
                "status": "unhealthy",
                "error": "No active workers"
            }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


async def _estimate_s3_objects() -> int:
    """Estimate number of S3 objects"""
    # Simplified estimation - in production would use S3 inventory
    return 0


async def _estimate_s3_size() -> float:
    """Estimate S3 storage size in GB"""
    # Simplified estimation - in production would use CloudWatch metrics
    return 0.0


def _get_report_period(report_type: str) -> Dict[str, str]:
    """Get report period based on type"""
    now = datetime.now()
    
    if report_type == "daily":
        start = now - timedelta(days=1)
    elif report_type == "weekly":
        start = now - timedelta(days=7)
    elif report_type == "monthly":
        start = now - timedelta(days=30)
    else:
        start = now - timedelta(days=1)
    
    return {
        "start": start.isoformat(),
        "end": now.isoformat()
    }


async def _generate_health_section() -> Dict[str, Any]:
    """Generate health section for report"""
    health = await monitor_check_health()
    return health


async def _generate_cache_section() -> Dict[str, Any]:
    """Generate cache section for report"""
    metrics = await _collect_cache_metrics()
    return metrics


async def _generate_task_section() -> Dict[str, Any]:
    """Generate task section for report"""
    metrics = await _collect_task_metrics(24)
    return metrics


async def _generate_error_section() -> List[Dict[str, Any]]:
    """Generate error section for report"""
    errors = await _collect_error_metrics(24)
    return errors


async def _generate_recommendations_section() -> List[str]:
    """Generate recommendations section for report"""
    # Analyze recent patterns
    analysis = await monitor_analyze_patterns(7)
    return analysis.get("recommendations", [])


async def _store_report(report: Dict[str, Any]):
    """Store report in database"""
    try:
        db = get_database()
        reports_collection = db.monitoring_reports
        
        await reports_collection.insert_one(report)
        
        # Keep only last 90 days of reports
        cutoff = datetime.now() - timedelta(days=90)
        await reports_collection.delete_many({
            "generated_at": {"$lt": cutoff.isoformat()}
        })
        
    except Exception as e:
        logger.error(f"Error storing report: {e}")


# Schedule monitoring tasks
celery_app.conf.beat_schedule.update({
    'collect-metrics-hourly': {
        'task': 'app.tasks.monitoring_tasks.monitor_collect_metrics',
        'schedule': crontab(minute=0),  # Every hour
        'kwargs': {'hours': 1},
    },
    'analyze-patterns-daily': {
        'task': 'app.tasks.monitoring_tasks.monitor_analyze_patterns',
        'schedule': crontab(hour=1, minute=0),  # 1 AM daily
        'kwargs': {'days': 7},
    },
    'generate-daily-report': {
        'task': 'app.tasks.monitoring_tasks.monitor_generate_report',
        'schedule': crontab(hour=6, minute=0),  # 6 AM daily
        'kwargs': {'report_type': 'daily'},
    },
    'health-check-frequent': {
        'task': 'app.tasks.monitoring_tasks.monitor_check_health',
        'schedule': crontab(minute='*/5'),  # Every 5 minutes
    },
})