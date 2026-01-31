"""
Health Check Endpoints for SMSLY Hosting.
Provides liveness, readiness, and comprehensive health checks.
"""
from django.http import JsonResponse
from django.db import connection
from django.core.cache import cache
from django.views import View
import docker
import logging
import time

logger = logging.getLogger(__name__)


class HealthCheckView(View):
    """
    Comprehensive health check endpoint.
    GET /health/ - Returns detailed health status of all dependencies
    """
    
    def get(self, request):
        start_time = time.time()
        health = {
            "status": "healthy",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "version": "2.0.0",
            "checks": {}
        }
        
        # Database check
        health["checks"]["database"] = self._check_database()
        
        # Redis check
        health["checks"]["redis"] = self._check_redis()
        
        # Docker check
        health["checks"]["docker"] = self._check_docker()
        
        # Determine overall status
        all_healthy = all(
            check.get("status") == "healthy" 
            for check in health["checks"].values()
        )
        health["status"] = "healthy" if all_healthy else "degraded"
        health["response_time_ms"] = round((time.time() - start_time) * 1000, 2)
        
        status_code = 200 if all_healthy else 503
        return JsonResponse(health, status=status_code)
    
    def _check_database(self) -> dict:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return {"status": "healthy", "latency_ms": 0}
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}
    
    def _check_redis(self) -> dict:
        try:
            cache.set("health_check", "ok", 10)
            result = cache.get("health_check")
            if result == "ok":
                return {"status": "healthy"}
            return {"status": "unhealthy", "error": "Cache get/set failed"}
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}
    
    def _check_docker(self) -> dict:
        try:
            client = docker.from_env()
            client.ping()
            return {"status": "healthy", "containers": len(client.containers.list())}
        except Exception as e:
            logger.warning(f"Docker health check failed: {e}")
            return {"status": "degraded", "error": str(e)}


class LivenessView(View):
    """
    Kubernetes liveness probe endpoint.
    GET /health/live - Returns 200 if the application is running
    """
    
    def get(self, request):
        return JsonResponse({"status": "alive"}, status=200)


class ReadinessView(View):
    """
    Kubernetes readiness probe endpoint.
    GET /health/ready - Returns 200 if the application is ready to serve traffic
    """
    
    def get(self, request):
        try:
            # Check critical dependencies
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            cache.set("readiness_check", "ok", 5)
            
            return JsonResponse({"status": "ready"}, status=200)
        except Exception as e:
            logger.error(f"Readiness check failed: {e}")
            return JsonResponse({"status": "not_ready", "error": str(e)}, status=503)


# URL patterns to add to urls.py:
# path('health/', HealthCheckView.as_view(), name='health'),
# path('health/live', LivenessView.as_view(), name='liveness'),
# path('health/ready', ReadinessView.as_view(), name='readiness'),
