import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.filter(name__icontains='ai-router-auth').first()
    print("Checking domain:", svc.public_domain)
except Exception as e:
    print(f"Error: {e}")
