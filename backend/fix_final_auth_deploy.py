import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.tasks import deploy_service_task
from apps.deployments.models import Service

try:
    proxy_name = "llama-proxy-fdd07af0"
    svc = Service.objects.get(name=proxy_name)
    svc.docker_image = "nginx:alpine"
    svc.save()
    
    print("Triggering official platform deployment for the proxy so it gets exact correct labels...")
    deploy_service_task.delay(svc.id)
    print("Deployment triggered!")

except Exception as e:
    print(e)
