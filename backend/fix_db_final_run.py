import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable, Deployment
from apps.deployments.tasks import smart_deploy_task
from django.db import transaction

try:
    with transaction.atomic():
        svc = Service.objects.get(name='ai-router-cc22a7a5')
        
        print("Disabling schema update to fix PgBouncer crash on boot...")
        # Since pgbouncer=true alone doesn't fix LiteLLM's internal Prisma client boot check!
        EnvironmentVariable.objects.update_or_create(
            service=svc, key="DISABLE_SCHEMA_UPDATE",
            defaults={"value": "true", "is_secret": False}
        )
        
        print("Triggering deployment with disabled schema updates.")
        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")
except Exception as e:
    print(f"Error: {e}")
