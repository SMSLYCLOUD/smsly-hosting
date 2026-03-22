import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.get(name='llama3-1-7b-a818c603')
    svc.public_domain = ''
    svc.save(update_fields=['public_domain'])
    print("Reverted llama3 DB domain.")
except Exception as e:
    print(f"Error: {e}")
