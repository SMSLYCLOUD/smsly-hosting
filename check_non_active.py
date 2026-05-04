from apps.deployments.models import Service
from apps.deployments.models_addons import Addon

non_active_services = Service.objects.exclude(status__in=['RUNNING', 'ACTIVE', 'DELETED'])
non_active_addons = Addon.objects.exclude(status__in=['RUNNING', 'ACTIVE', 'DELETED'])

print(f"NON_ACTIVE_SERVICES_COUNT:{non_active_services.count()}")
print(f"NON_ACTIVE_ADDONS_COUNT:{non_active_addons.count()}")

for s in non_active_services:
    print(f"SERVICE:{s.id} NAME:{s.name} STATUS:{s.status} ERROR:{s.deployment_error}")

for a in non_active_addons:
    print(f"ADDON:{a.id} NAME:{a.name} STATUS:{a.status} ERROR:{a.deployment_error}")
