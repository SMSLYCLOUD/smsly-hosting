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
        
        # We need to set STORE_MODEL_IN_DB to False to just get the YAML to load without UI issues,
        # but the user requested DB to be fixed. So we will make it True.
        
        # The issue is the postgres database connection. 
        # I'll just change the DATABASE_URL to use the actual container name instead of pgbouncer.
        # How to find the container name? We can just use the exact internal postgres URL 
        # if there's only one. But there are many. We can use python to fetch it.
        addon = svc.addons.filter(addon_type="POSTGRES").first()
        if not addon:
            print("No Postgres addon linked.")
        else:
            container_name = f"smsly-addon-postgres-{addon.id}"
            print("Direct DB Container:", container_name)
            env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DATABASE_URL")
            
            # Replace pgbouncer with container name
            if "pgbouncer" in env.value:
                # remove ?pgbouncer=true
                new_url = env.value.replace("pgbouncer:5432", f"{container_name}:5432")
                new_url = new_url.replace("?pgbouncer=true", "").replace("&pgbouncer=true", "")
                env.value = new_url
                env.save()
                print("Updated DB URL to direct link.")

            # Ensure schema update is true so it migrates on boot
            env2, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DISABLE_SCHEMA_UPDATE")
            env2.value = "false"
            env2.save()

            # Ensure DB is enabled
            env3, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="STORE_MODEL_IN_DB")
            env3.value = "True"
            env3.save()
            
            d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
            smart_deploy_task.delay(str(d.id), str(svc.provider_id))
            print("Deployment triggered.")
            
except Exception as e:
    print(f"Error: {e}")
