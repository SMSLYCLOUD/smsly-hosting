import os
import sys
import django

# Setup Django environment
sys.path.append(os.path.join(os.getcwd(), 'backend'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models_core import ManagedServer

def dump_server_env():
    servers = ManagedServer.objects.all()
    print(f"--- Found {len(servers)} servers ---")
    for s in servers:
        print(f"ID: {s.id} | Name: {s.name} | Host: {s.host} | Lite: {s.is_lite_agent}")

if __name__ == "__main__":
    dump_server_env()
