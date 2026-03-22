import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

svc = Service.objects.get(name='ai-router-auth')
print(f"Name: {svc.name}")
print(f"Health: {svc.health_status}")
print(f"Status: {svc.status}")
print(f"Public Domain: {svc.public_domain}")
