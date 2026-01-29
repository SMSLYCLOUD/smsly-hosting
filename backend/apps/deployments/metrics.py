"""
Prometheus metrics for SMSLY Hosting platform.
Exposes metrics at /metrics endpoint for monitoring.
"""
from prometheus_client import Counter, Histogram, Gauge, Info
from prometheus_client import REGISTRY
from functools import wraps
import time

# =============================================================================
# Deployment Metrics
# =============================================================================

DEPLOYMENTS_TOTAL = Counter(
    'smsly_hosting_deployments_total',
    'Total number of deployments',
    ['status', 'service_name']
)

DEPLOYMENT_DURATION = Histogram(
    'smsly_hosting_deployment_duration_seconds',
    'Time spent on deployments',
    ['service_name'],
    buckets=[30, 60, 120, 300, 600, 1200, 1800, 3600]
)

ACTIVE_DEPLOYMENTS = Gauge(
    'smsly_hosting_active_deployments',
    'Number of currently building/deploying deployments'
)

# =============================================================================
# Build Metrics
# =============================================================================

BUILD_DURATION = Histogram(
    'smsly_hosting_build_duration_seconds',
    'Time spent building Docker images',
    ['service_name'],
    buckets=[30, 60, 120, 300, 600, 900]
)

# =============================================================================
# Vulnerability Metrics
# =============================================================================

VULNERABILITIES_FOUND = Counter(
    'smsly_hosting_vulnerabilities_total',
    'Total vulnerabilities found in scans',
    ['severity']
)

# =============================================================================
# API Metrics
# =============================================================================

API_REQUESTS = Counter(
    'smsly_hosting_api_requests_total',
    'Total API requests',
    ['method', 'endpoint', 'status_code']
)

API_LATENCY = Histogram(
    'smsly_hosting_api_latency_seconds',
    'API request latency',
    ['method', 'endpoint'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# =============================================================================
# Service Metrics
# =============================================================================

SERVICES_TOTAL = Gauge(
    'smsly_hosting_services_total',
    'Total number of registered services'
)

PLATFORM_INFO = Info(
    'smsly_hosting_platform',
    'Platform version and configuration'
)

# Initialize platform info
PLATFORM_INFO.info({
    'version': '1.0.0',
    'platform': 'smsly-hosting',
    'kubernetes_enabled': 'true'
})


# =============================================================================
# Helper Functions
# =============================================================================

def track_deployment(service_name):
    """Decorator to track deployment metrics."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            ACTIVE_DEPLOYMENTS.inc()
            start_time = time.time()
            status = 'success'
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                status = 'failed'
                raise
            finally:
                duration = time.time() - start_time
                DEPLOYMENT_DURATION.labels(service_name=service_name).observe(duration)
                DEPLOYMENTS_TOTAL.labels(status=status, service_name=service_name).inc()
                ACTIVE_DEPLOYMENTS.dec()
        return wrapper
    return decorator


def record_vulnerability_scan(vulns: dict):
    """Record vulnerability counts from a scan."""
    for severity, count in vulns.items():
        if count > 0:
            VULNERABILITIES_FOUND.labels(severity=severity).inc(count)
