import os
import django
import sys

# Setup django
sys.path.append('c:/Users/osaretin/Documents/SMSLY/SMSLY_CORE/smsly-hosting/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models_core import Service, ManagedServer

primary = ManagedServer.get_primary()
if not primary:
    print("No primary server found. Cannot auto-assign.")
    exit(0)

unassigned = Service.objects.filter(server__isnull=True)
count = unassigned.count()

if count == 0:
    print("No unassigned services found.")
else:
    print(f"Assigning {count} services to primary server: {primary.name}")
    unassigned.update(server=primary)
    print("Repair complete.")
