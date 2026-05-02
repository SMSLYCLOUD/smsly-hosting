import os
import django
import sys

# Setup django
sys.path.append('c:/Users/osaretin/Documents/SMSLY/SMSLY_CORE/smsly-hosting/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models_core import ManagedServer

primary = ManagedServer.objects.filter(is_primary=True).first()
if primary:
    print(f"Primary Server: {primary.name} ({primary.host}) ID: {primary.id}")
else:
    print("No Primary Server found.")
