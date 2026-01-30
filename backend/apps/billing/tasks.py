from celery import shared_task
from django.utils import timezone
from .models import UsageRecord
from apps.deployments.models import Deployment, Service
from decimal import Decimal

# Pricing: $0.01 per vCPU/hour, $0.005 per GB-RAM/hour
PRICE_CPU_HOUR = Decimal("0.01")
PRICE_RAM_GB_HOUR = Decimal("0.005")

@shared_task
def collect_usage_task():
    """
    Runs hourly. Snapshots active services and calculates cost.
    """
    active_services = Service.objects.filter(
        deployments__status=Deployment.Status.ACTIVE
    ).distinct()

    for service in active_services:
        cpu = service.cpu_cores
        ram_gb = Decimal(service.memory_mb) / 1024

        cost = (cpu * PRICE_CPU_HOUR) + (ram_gb * PRICE_RAM_GB_HOUR)

        UsageRecord.objects.create(
            service=service,
            cpu_cores=cpu,
            memory_mb=service.memory_mb,
            cost=cost
        )
