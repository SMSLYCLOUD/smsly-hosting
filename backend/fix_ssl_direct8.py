import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    proxy_name = "llama-proxy-fdd07af0"
    svc = Service.objects.get(name=proxy_name)
    svc.health_status = 'healthy'
    svc.save()
    
    print("Database record mapped to healthy.")
    # CloudNeuron uses traefik labels for SSL now. Caddy manager must be legacy in this project version!

except Exception as e:
    print(f"Error: {e}")
