import os
import sys
import django

sys.path.append(os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.domains.models import Domain

print("--- Domains ---")
for d in Domain.objects.all():
    print(f"ID: {d.id}")
    print(f"Domain Name: {d.domain_name}")
    print(f"Service: {d.service.name if d.service else 'None'}")
    print(f"Status: {d.status}")
    print(f"Verified: {d.verified}")
    print("---")
