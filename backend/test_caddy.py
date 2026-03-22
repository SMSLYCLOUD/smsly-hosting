import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.filter(name__icontains='ai-router').first()
    print("Found router:", svc.name)
except Exception as e:
    print(f"Error: {e}")
