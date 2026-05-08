import os
import sys
import django

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import ManagedServer

print("--- ManagedServers ---")
for s in ManagedServer.objects.all():
    print(f"ID: {s.id}")
    print(f"Host: {s.host}")
    print(f"API URL: {s.api_url}")
    print("---")
