import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service
import services.caddy_manager as cm

try:
    proxy_name = "llama-proxy-fdd07af0"
    svc = Service.objects.create(
        name=proxy_name,
        docker_image="caddy:alpine",
        public_domain=f"{proxy_name}-proxy.pcloud.linadeluxe.com",
        health_status='healthy',
    )
    
    # Reload SSL mapping via Caddy manager directly
    c = cm.CaddyConfigManager()
    c.generate_global_caddyfile()
    print("Database record saved and Caddy proxy rebuilt.")

except Exception as e:
    print(f"Error: {e}")
