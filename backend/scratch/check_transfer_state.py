import os
import sys
import django
import json

# Add backend directory to sys.path
sys.path.append(os.getcwd())

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import PlatformConfig, ManagedServer
from apps.deployments.models_transfer import ServerTransfer

print("--- PlatformConfig ---")
cfg = PlatformConfig.load()
print(f"Domain: {cfg.domain}")
print(f"Server IP: {cfg.server_ip}")
print(f"Use SSL: {cfg.use_ssl}")
print(f"Wildcard: {cfg.wildcard_subdomains}")

print("\n--- ManagedServers ---")
for s in ManagedServer.objects.all():
    print(f"ID: {s.id}")
    print(f"Name: {s.name}")
    print(f"Host: {s.host}")
    print(f"Status: {s.status}")
    print(f"Provision Status: {s.provision_status}")
    print(f"Is Lite Agent: {s.is_lite_agent}")
    print(f"API Token: {'Set' if s.api_token else 'Missing'}")
    print("---")

print("\n--- Active Transfers ---")
for t in ServerTransfer.objects.exclude(status__in=['COMPLETED', 'FAILED', 'ROLLED_BACK']):
    print(f"ID: {t.id}")
    print(f"Type: {t.transfer_type}")
    print(f"Target IP: {t.target_server_ip}")
    print(f"Status: {t.status}")
    print(f"Error: {t.error_message}")
    print("---")
