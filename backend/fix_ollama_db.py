import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.get(name='llama3-1-7b-a818c603')
    svc.health_status = 'healthy'
    svc.status = 'ACTIVE'
    svc.public_domain = 'llama3-1-7b.pcloud.linadeluxe.com'
    svc.port = 11434
    svc.save(update_fields=['health_status', 'status', 'public_domain', 'port'])
    print("Ollama DB set to healthy.")
except Exception as e:
    print(f"Error: {e}")
