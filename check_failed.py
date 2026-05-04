from apps.deployments.models import Service
from apps.deployments.models_addons import Addon
import sys

failed_services = Service.objects.filter(status__icontains='FAILED')
failed_addons = Addon.objects.filter(status__icontains='FAILED')

print(f"FAILED_SERVICES_COUNT:{failed_services.count()}")
print(f"FAILED_ADDONS_COUNT:{failed_addons.count()}")

for s in failed_services:
    print(f"SERVICE:{s.id} NAME:{s.name} STATUS:{s.status} ERROR:{s.deployment_error}")

for a in failed_addons:
    print(f"ADDON:{a.id} NAME:{a.name} STATUS:{a.status} ERROR:{a.deployment_error}")
