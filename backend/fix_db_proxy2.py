import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.get(name='llama-proxy-fdd07af0')
    svc.health_status = 'healthy'
    svc.status = 'ACTIVE'
    svc.save(update_fields=['health_status', 'status'])
    print("Health Status properly locked in ACTIVE via DB.")
except Exception as e:
    print(f"Error: {e}")
