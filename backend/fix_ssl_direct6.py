import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    proxy_name = "llama-proxy-fdd07af0"
    svc = Service.objects.create(
        name=proxy_name,
        docker_image="caddy:alpine",
        port=4000,
        public_domain=f"{proxy_name}-proxy.pcloud.linadeluxe.com",
        health_status='healthy',
        status='ACTIVE',
        deployment_type='DOCKER'
    )
    
    # Reload SSL mapping via Caddy manager directly
    subprocess.run(["python", "manage.py", "shell", "-c", "from services.caddy_manager import CaddyConfigManager; c = CaddyConfigManager(); c.generate_global_caddyfile()"])
    print("Database record saved and Caddy proxy rebuilt.")

except Exception as e:
    print(f"Error: {e}")
