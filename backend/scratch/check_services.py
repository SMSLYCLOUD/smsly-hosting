import os
import django
import sys

# Setup django
sys.path.append('c:/Users/osaretin/Documents/SMSLY/SMSLY_CORE/smsly-hosting/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

print(f"{'Name':<30} | {'Status':<15} | {'Server':<20} | {'ID'}")
print("-" * 80)
for s in Service.objects.all():
    server_name = s.server.name if s.server else "Unassigned"
    print(f"{s.name:<30} | {s.status:<15} | {server_name:<20} | {s.id}")
