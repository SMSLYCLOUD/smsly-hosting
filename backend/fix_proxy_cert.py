import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, Deployment
from apps.deployments.tasks import smart_deploy_task

try:
    svc = Service.objects.filter(name__icontains='ai-router').first()
    if svc:
        print("Restoring litellm v1.45.0 proxy fully via platform deployment...")
        svc.docker_image = "ghcr.io/berriai/litellm:main-v1.45.0"
        svc.start_command = ""
        svc.save()
        
        # Let's remove any raw docker containers to avoid port conflicts
        subprocess.run(["docker", "rm", "-f", svc.name])
        
        # We trigger the deployment normally so Caddy/Traefik picks up SSL correctly and routes it
        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered successfully. Caddy will restore SSL certificates.")
    else:
        print("AI Router service not found.")
except Exception as e:
    print(f"Error: {e}")
