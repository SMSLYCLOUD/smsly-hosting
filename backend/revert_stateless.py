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
        
        # It's STILL failing in v1.75! LiteLLM has genuinely removed all support for passing a Master Key in stateless mode with YAML models if the DB isn't there, or if they are both defined. 
        # Let's restore the Postgres DB connection to fix it completely, using v1.75!
        
        print("Restoring database connection for v1.75...")
        env_store, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="STORE_MODEL_IN_DB")
        env_store.value = "True"
        env_store.save()

        # Disable schema update on boot
        env_disable, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DISABLE_SCHEMA_UPDATE")
        env_disable.value = "true"
        env_disable.save()

        # Find the postgres container
        addon = svc.addons.filter(addon_type="POSTGRES").first()
        if not addon:
            # We created one earlier, let's link it
            from apps.deployments.models import Addon
            addon = Addon.objects.filter(addon_type="POSTGRES").first()
            if addon:
                svc.addons.add(addon)
        
        if addon:
            print(f"Linking back to addon {addon.name}")
            direct_url = f"postgresql://smsly_admin:fe6d8a65e32282ed9928e3632c28f7f6@{addon.name}:5432/ai_router_cc22a7a5"
            env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DATABASE_URL")
            env.value = direct_url
            env.save()

        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")
            
except Exception as e:
    print(f"Error: {e}")
