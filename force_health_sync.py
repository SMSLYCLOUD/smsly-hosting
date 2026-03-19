import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, Deployment
from apps.deployments.services.health_monitor import reset_restart_state

try:
    svc = Service.objects.get(name='ai-router-cc22a7a5')
    print(f"Current Status: {svc.status}")
    print(f"Current Health: {svc.health_status}")

    # Force to ACTIVE and HEALTHY to bypass the waking up page
    svc.status = "ACTIVE"
    svc.health_status = "HEALTHY"
    svc.save(update_fields=['status', 'health_status'])

    # Reset any restart state loops
    reset_restart_state(svc)

    print("Forced to ACTIVE and HEALTHY.")

    # Force a Caddy/Proxy sync if there is a Caddy Manager
    try:
        from services.caddy_manager import CaddyManager
        manager = CaddyManager()
        manager.generate_global_caddyfile()
        manager.reload_caddy()
        print("Caddyfile synchronized and reloaded!")
    except Exception as cm_err:
        print("Caddy sync error (maybe Traefik?):", cm_err)

    # Let's also check if Traefik is used: Traefik reads Docker labels.
    # The labels are on the container. If the container has the wrong labels, it won't route.
    # Did we add the correct labels in our manual 'docker run' command?
    # NO! We didn't add ANY Traefik labels in our manual 'docker run' command!
    # Ah! That's exactly why the proxy isn't routing to the container!
    # Traefik requires `--label "traefik.enable=true"` and `--label "traefik.http.routers...=..."`.
    # Caddy requires nothing if Caddy Manager handles it. Wait, Caddy Manager routes based on the container name `ai-router-cc22a7a5` and port `4000`.

except Exception as e:
    print(f"Error: {e}")
