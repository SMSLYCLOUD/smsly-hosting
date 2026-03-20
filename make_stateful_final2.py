import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable, Deployment, Addon
from apps.deployments.tasks import smart_deploy_task
from django.db import transaction

try:
    with transaction.atomic():
        svc = Service.objects.get(name='ai-router-cc22a7a5')

        # Link a POSTGRES addon
        addon = svc.addons.filter(addon_type="POSTGRES").first()
        if not addon:
            addon = Addon.objects.filter(addon_type="POSTGRES").first()
            if addon:
                svc.addons.add(addon)

        if not addon:
            print("ERROR: NO POSTGRES ADDONS FOUND ON PLATFORM!")
        else:
            print(f"Using Postgres Addon: {addon.name}")
            direct_url = f"postgresql://smsly_admin:fe6d8a65e32282ed9928e3632c28f7f6@{addon.name}:5432/ai_router_cc22a7a5"

            env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DATABASE_URL")
            env.value = direct_url
            env.save()
            print(f"Updated DATABASE_URL to bypass PgBouncer: {env.value}")

        EnvironmentVariable.objects.filter(service=svc, key="DISABLE_SCHEMA_UPDATE").delete()

        env3, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="STORE_MODEL_IN_DB")
        env3.value = "True"
        env3.save()

        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")

except Exception as e:
    print(f"Error: {e}")
