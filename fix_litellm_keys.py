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

        print("Changing back to main-stable to see if db push works in newest version...")
        svc.docker_image = "ghcr.io/berriai/litellm:main-stable"
        svc.save(update_fields=['docker_image'])

        env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DATABASE_URL")
        print(f"Current DB: {env.value}")

        # Ensure LITELLM_MASTER_KEY is standard
        env_key, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="LITELLM_MASTER_KEY")
        env_key.value = "sk-agbonsalo"
        env_key.save()

        hooks = svc.post_deploy_hooks or []
        if "prisma migrate deploy" not in hooks:
            hooks.append("prisma migrate deploy")
            svc.post_deploy_hooks = hooks
            svc.save(update_fields=['post_deploy_hooks'])

        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")

except Exception as e:
    print(f"Error: {e}")
