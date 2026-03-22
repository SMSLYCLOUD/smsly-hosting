import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import services.caddy_manager as cm

try:
    c = cm.CaddyConfigManager()
    c.generate_global_caddyfile()
    print("Caddy reloaded via manager.")
except Exception as e:
    print(f"Error: {e}")
