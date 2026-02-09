"""Health check endpoint for production readiness."""
from django.http import JsonResponse
from django.db import connection
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


def health(request):
    """
    Health check endpoint for load balancers and orchestration systems.
    
    Returns:
        200 OK if all systems are operational
        503 Service Unavailable if critical dependencies are down
    """
    status = {
        "status": "healthy",
        "database": "unknown",
        "cache": "unknown"
    }
    http_status = 200
    
    # Check database connection
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        status["database"] = "healthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        status["database"] = "unhealthy"
        status["status"] = "unhealthy"
        http_status = 503
    
    # Check Redis/cache connection
    try:
        cache.set("health_check", "ok", timeout=10)
        if cache.get("health_check") == "ok":
            status["cache"] = "healthy"
        else:
            raise Exception("Cache set/get mismatch")
    except Exception as e:
        logger.error(f"Cache health check failed: {e}")
        status["cache"] = "unhealthy"
        status["status"] = "unhealthy"
        http_status = 503
    
    return JsonResponse(status, status=http_status)
