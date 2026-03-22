import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service
import services.caddy_manager as cm

try:
    svc = Service.objects.get(name='llama3-1-7b-a818c603')
    svc.public_domain = "llama3-1-7b.pcloud.linadeluxe.com"
    svc.health_status = 'healthy'
    svc.status = 'ACTIVE'
    svc.save()
    
    # Reload via caddy
    c = cm.CaddyConfigManager()
    c.generate_global_caddyfile()
    print("Re-mapped standard Llama3 via Caddy.")

except Exception as e:
    print(f"Error: {e}")
