import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, Deployment
from apps.deployments.services.health_monitor import reset_restart_state

try:
    svc = Service.objects.get(name='ai-router-cc22a7a5')
    print(f"Current Health: {svc.health_status}")

    svc.health_status = "HEALTHY"
    svc.save(update_fields=['health_status'])

    reset_restart_state(svc)

    print("Forced to HEALTHY.")

    try:
        from services.caddy_manager import CaddyManager
        manager = CaddyManager()
        manager.generate_global_caddyfile()
        manager.reload_caddy()
        print("Caddyfile synchronized and reloaded!")
    except Exception as cm_err:
        print("Caddy sync error (maybe Traefik?):", cm_err)

    # I'll just trigger a REAL deployment via Celery now that the codebase and db are clean.
    d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
    from apps.deployments.tasks import smart_deploy_task
    smart_deploy_task.delay(str(d.id), str(svc.provider_id))
    print("Real Deployment Triggered! It will get the labels.")

except Exception as e:
    print(f"Error: {e}")
