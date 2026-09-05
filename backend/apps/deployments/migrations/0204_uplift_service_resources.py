"""One-time uplift of services left on the legacy resource defaults.

Rows still exactly at the old platform defaults (1.0 CPU and 1024/2048 MB
??? values the platform assigned, never the operator) are raised to the
host-scaled defaults so running services stop being throttled. Services
with any custom value are untouched. Reverse is a no-op by design.
"""
from decimal import Decimal

from django.db import migrations
from django.db.models import Q


def uplift_service_resources(apps, schema_editor):
    from apps.deployments.models.service import default_service_resources

    Service = apps.get_model("deployments", "Service")
    host_cpu, host_mem = default_service_resources()
    cpu_count = Service.objects.filter(cpu_cores=Decimal("1.0")).update(cpu_cores=Decimal(str(host_cpu)))
    mem_count = Service.objects.filter(
        Q(memory_mb=1024) | Q(memory_mb=2048)
    ).update(memory_mb=int(host_mem))
    print(f"uplifted {cpu_count} service(s) to {host_cpu} CPU, {mem_count} service(s) to {host_mem}MB RAM")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("deployments", "0203_alter_service_cpu_cores_alter_service_memory_mb"),
    ]

    operations = [
        migrations.RunPython(uplift_service_resources, noop_reverse, elidable=True),
    ]
