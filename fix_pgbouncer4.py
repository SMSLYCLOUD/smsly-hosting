import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable, Deployment
from apps.deployments.tasks import smart_deploy_task
from django.db import transaction

try:
    with transaction.atomic():
        svc = Service.objects.get(name='ai-router-cc22a7a5')

        # Override the ADDON DATABASE_URL directly by deleting the addon mapping and injecting static
        print("Removing addons to prevent URL overwrite...")
        svc.addons.clear()

        print("Updating Environment Variables...")
        env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DATABASE_URL")

        # Set explicitly
        env.value = "postgresql://smsly_admin:fe6d8a65e32282ed9928e3632c28f7f6@pgbouncer:5432/ai_router_cc22a7a5?pgbouncer=true"
        env.save()
        print(f"Updated DATABASE_URL: {env.value}")

        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")
except Exception as e:
    print(f"Error: {e}")
