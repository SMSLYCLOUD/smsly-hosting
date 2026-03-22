import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service
from services.caddy_manager import CaddyConfigManager

try:
    svc = Service.objects.get(name='llama3-1-7b-a818c603')
    
    # Give it a public domain directly so Caddy will proxy to port 11434.
    svc.public_domain = "llama3-1-7b-a818c603.pcloud.linadeluxe.com"
    svc.health_status = 'healthy'
    svc.port = 11434
    svc.save()
    
    print(f"Service {svc.name} mapped to {svc.public_domain} on port {svc.port}")
    
    # Reload Caddy config to immediately pick up the new route
    manager = CaddyConfigManager()
    manager.generate_global_caddyfile()
    print("Caddy reloaded.")
    
except Exception as e:
    print(f"Error: {e}")
