import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.get(name='ai-router-auth')
    # Since Caddy requires manual reloads sometimes, let's explicitly trigger CaddyManager
    
    from services.caddy_manager import CaddyManager
    cm = CaddyManager()
    cm.generate_global_caddyfile()
    cm.reload_caddy()
    print("Caddyfile synchronized and reloaded!")

except Exception as e:
    print(f"Error: {e}")
