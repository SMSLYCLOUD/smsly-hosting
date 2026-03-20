import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    for svc in Service.objects.filter(docker_image__icontains='ollama'):
        if svc.name != "llama3-1-7b-a818c603":
            svc.delete()
    print("DB cleanup complete. Only llama3-1-7b-a818c603 is left.")
except Exception as e:
    print(f"Error: {e}")
