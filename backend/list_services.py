import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

for svc in Service.objects.all():
    print(f"Name: {svc.name}, Health: {svc.health_status}")
