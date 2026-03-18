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

        # We'll just trigger it via smart_deploy, the pipeline will provision the addons if we set addons.
        print("Attaching POSTGRES Addon via model requirements...")
        addons = svc.addons or []
        if "POSTGRES" not in addons:
            addons.append("POSTGRES")
            svc.addons = addons
            svc.save(update_fields=['addons'])

        # 2. Update Environment Variables
        print("Updating Environment Variables...")
        # Remove DISABLE_SCHEMA_UPDATE
        EnvironmentVariable.objects.filter(service=svc, key="DISABLE_SCHEMA_UPDATE").delete()

        # Set STORE_MODEL_IN_DB to True
        EnvironmentVariable.objects.update_or_create(
            service=svc, key="STORE_MODEL_IN_DB",
            defaults={"value": "True", "is_secret": False}
        )

        print("Stateful configuration successfully applied.")

        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")
except Exception as e:
    print(f"Error: {e}")
