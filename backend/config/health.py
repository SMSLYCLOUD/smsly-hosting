"""Health check endpoints for SMSLY Hosting — production hardened."""
import time

from apps.core.circuit_breaker import database_breaker, redis_breaker
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse

# Track process start time for uptime calculation
_PROCESS_START = time.monotonic()
_VERSION = getattr(settings, 'SMSLY_VERSION', '2.0.0')


def health_check(request):
    """
    Comprehensive health check — checks all critical dependencies.
    Used by load balancers and monitoring systems.
    Returns 200 if healthy, 503 if any dependency is down.
    """
    db_status = 'healthy'
    cache_status = 'healthy'
    deployment_count = 0
    checks_passed = True

    # Check database
    try:
        with connection.cursor() as cursor:
            # Protect DB call with circuit breaker
            @database_breaker
            def check_db():
                cursor.execute('SELECT 1')
            check_db()
    except Exception:
        db_status = 'unhealthy'
        checks_passed = False

    # Check cache (Redis)
    try:
        @redis_breaker
        def check_cache():
            cache.set('_health_check', '1', 10)
            val = cache.get('_health_check')
            if val != '1':
                raise ValueError('Cache read mismatch')
        check_cache()
    except Exception:
        cache_status = 'unhealthy'
        checks_passed = False

    # Count recent deployments (non-critical, won't fail health check)
    if db_status == 'healthy':
        try:
            from apps.deployments.models import Deployment
            deployment_count = Deployment.objects.count()
        except Exception:
            pass

    uptime_seconds = int(time.monotonic() - _PROCESS_START)

    payload = {
        'status': 'healthy' if checks_passed else 'unhealthy',
        'version': _VERSION,
        'uptime_seconds': uptime_seconds,
        'database': db_status,
        'cache': cache_status,
        'deployments_total': deployment_count,
    }

    status_code = 200 if checks_passed else 503
    return JsonResponse(payload, status=status_code)


def health_check_verbose(request):
    """
    Verbose health check — includes RabbitMQ, Celery, disk, SSL, and DNS.
    Uses HealthCheckService for comprehensive infrastructure checks.
    """
    try:
        from apps.core.services.health_check_service import HealthCheckService
        result = HealthCheckService.run_all_checks()
        result['version'] = _VERSION
        result['uptime_seconds'] = int(time.monotonic() - _PROCESS_START)
        status_code = 200 if result['ok'] else 503
        return JsonResponse(result, status=status_code)
    except Exception as exc:
        return JsonResponse({
            'status': 'error',
            'error': str(exc),
            'version': _VERSION,
        }, status=503)


def liveness_check(request):
    """
    Liveness probe — is the process alive and responsive?
    Should NOT check external deps (DB, cache). Only checks the process itself.
    Used by Kubernetes liveness probes.
    """
    return JsonResponse({
        'status': 'alive',
        'version': _VERSION,
        'uptime_seconds': int(time.monotonic() - _PROCESS_START),
    })


def readiness_check(request):
    """
    Readiness probe — can the process handle traffic?
    Checks database and cache connectivity.
    Used by Kubernetes readiness probes.
    """
    ready = True

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        ready = False

    try:
        cache.set('_readiness_check', '1', 10)
        if cache.get('_readiness_check') != '1':
            raise ValueError('Cache mismatch')
    except Exception:
        ready = False

    if ready:
        return JsonResponse({'status': 'ready'})
    return JsonResponse({'status': 'not_ready'}, status=503)
