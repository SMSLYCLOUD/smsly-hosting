"""Tasks Metrics module."""
import random
from celery import shared_task
from django.utils import timezone
from .models import Service
from .models_metrics import ServiceMetric


@shared_task
def collect_metrics_task():
    """
    Collects metrics for all active services.
    In prod, this would query Prometheus or K8s Metrics API.
    For MVP, we simulate realistic usage data.
    """
    now = timezone.now()
    services = Service.objects.all()

    for service in services:
        # Simulate usage based on limits
        # Random usage between 10% and 80% of limit
        cpu_limit = float(service.cpu_cores)
        mem_limit = service.memory_mb

        cpu_used = cpu_limit * random.uniform(0.1, 0.8)
        mem_used = mem_limit * random.uniform(0.1, 0.8)

        ServiceMetric.objects.create(
            service=service,
            cpu_usage=cpu_used,
            memory_usage=int(mem_used),
            timestamp=now
        )
