import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    proxy_name = "llama-proxy-fdd07af0"
    svc = Service.objects.get(name=proxy_name)
    
    # CloudNeuron Caddy needs to know about this domain to generate the SSL certificate.
    # We will trigger the caddy config generation and reload.
    from services.caddy_manager import CaddyConfigManager
    mgr = CaddyConfigManager()
    mgr.generate_global_caddyfile()
    print("Caddy config generated to include proxy domain.")

except Exception as e:
    print(e)
