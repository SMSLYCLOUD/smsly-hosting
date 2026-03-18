import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

for s in Service.objects.all():
    print(f"ID: {s.id}, Name: {s.name}, Image: {s.docker_image}")
