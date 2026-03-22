import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.filter(name__icontains='qwen2').first()
    print("Qwen Name:", svc.name)
    print("Qwen Domain:", svc.public_domain)
    
except Exception as e:
    print(f"Error: {e}")
