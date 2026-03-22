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
        
        print("Downgrading Docker image to v1.75.5-stable...")
        svc.docker_image = "ghcr.io/berriai/litellm:main-v1.75.5-stable"
        svc.save(update_fields=['docker_image'])

        print("Reverting to fully stateless...")
        env_store, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="STORE_MODEL_IN_DB")
        env_store.value = "False"
        env_store.save()

        env_disable, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DISABLE_SCHEMA_UPDATE")
        env_disable.value = "true"
        env_disable.save()

        print("Removing postgres add-ons and database URL...")
        svc.addons.set([])
        EnvironmentVariable.objects.filter(service=svc, key="DATABASE_URL").delete()

        # Older LiteLLM versions didn't enforce sk- on master key.
        # But we'll leave it as sk-agbonsalo just in case.

        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")
            
except Exception as e:
    print(f"Error: {e}")
