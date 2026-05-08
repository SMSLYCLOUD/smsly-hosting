import os
import sys
import django

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

print("--- Services ---")
for s in Service.objects.all():
    print(f"ID: {s.id}")
    print(f"Name: {s.name}")
    print(f"Public Domain: {s.public_domain}")
    print(f"Custom Domains: {s.custom_domains}")
    print(f"Server: {s.server.host if s.server else 'Local'}")
    print("---")
