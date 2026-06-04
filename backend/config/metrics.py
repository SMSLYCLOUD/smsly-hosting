"""Custom Prometheus metrics for the SMSLY Hosting platform.

These metrics are exposed via django_prometheus at /metrics and are
also pushed to cAdvisor/long-term Prometheus via the django_prometheus
client integration.
"""
from prometheus_client import Counter, Gauge, Histogram

SERVICE_DEPLOYMENTS_TOTAL = Counter(
    'smsly_deployments_total',
    'Total deployments triggered',
    ['service_id', 'status'],
)

SERVICE_BUILDS_TOTAL = Counter(
    'smsly_builds_total',
    'Total builds executed',
    ['result'],
)

SERVICES_ACTIVE = Gauge(
    'smsly_services_active',
    'Number of active services',
)

DEPLOYMENT_DURATION = Histogram(
    'smsly_deployment_duration_seconds',
    'Deployment duration in seconds',
    buckets=(30, 60, 120, 300, 600, 1200, 3600),
)

ADDON_PROVISION_DURATION = Histogram(
    'smsly_addon_provision_duration_seconds',
    'Time taken to provision an addon',
    ['addon_type'],
    buckets=(5, 15, 30, 60, 120, 300, 600),
)
