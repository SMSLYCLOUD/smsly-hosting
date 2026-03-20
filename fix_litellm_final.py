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

        # Attach the Postgres addon properly
        addon = Addon.objects.filter(addon_type="POSTGRES", name=f"smsly-addon-postgres-{svc.id}").first()
        if addon:
            svc.addons.add(addon)

            env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DATABASE_URL")
            env.value = f"postgresql://smsly_admin:fe6d8a65e32282ed9928e3632c28f7f6@{addon.name}:5432/ai_router_cc22a7a5"
            env.save()
        else:
            print("No dedicated addon found.")

        EnvironmentVariable.objects.update_or_create(
            service=svc, key="STORE_MODEL_IN_DB",
            defaults={"value": "True", "is_secret": False}
        )

        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")

except Exception as e:
    print(f"Error: {e}")
