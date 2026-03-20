import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable, Deployment, Addon
from apps.deployments.tasks import smart_deploy_task
from django.db import transaction

try:
    with transaction.atomic():
        svc = Service.objects.get(name='ai-router-cc22a7a5')

        # We grabbed a random existing addon "postgres-buyforfront-frontend" that isn't running!
        # Let's unlink all addons and create a brand new dedicated one for the AI Router.
        svc.addons.clear()

        addon_name = f"smsly-addon-postgres-{svc.id}"
        addon, created = Addon.objects.get_or_create(
            name=addon_name,
            defaults={"addon_type": "POSTGRES", "provider": svc.provider, "owner": svc.owner}
        )
        svc.addons.add(addon)
        print(f"Created and linked dedicated POSTGRES addon: {addon_name}")

        # The addon provisioning will happen during the deploy pipeline, but we must set the DATABASE_URL to use it directly
        direct_url = f"postgresql://smsly_admin:fe6d8a65e32282ed9928e3632c28f7f6@{addon_name}:5432/ai_router_cc22a7a5"

        env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DATABASE_URL")
        env.value = direct_url
        env.save()
        print(f"Updated DATABASE_URL: {env.value}")

        EnvironmentVariable.objects.filter(service=svc, key="DISABLE_SCHEMA_UPDATE").delete()

        env3, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="STORE_MODEL_IN_DB")
        env3.value = "True"
        env3.save()

        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")

except Exception as e:
    print(f"Error: {e}")
