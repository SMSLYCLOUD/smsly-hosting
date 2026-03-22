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
        
        print("Enabling pgBouncer flags in connection string!")
        # To truly fix pgbouncer for Prisma, it needs ?pgbouncer=true AND connection_limit
        env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DATABASE_URL")
        if "pgbouncer=true" not in env.value:
            env.value += "&pgbouncer=true" if "?" in env.value else "?pgbouncer=true"
        
        # Add DIRECT_URL to bypass PgBouncer for migrations!
        # The platform's postgres addon is reachable via smsly-addon-postgres-<uuid>
        # Let's find the true addon container.
        direct_url = env.value.replace("pgbouncer:5432", f"smsly-addon-postgres-{svc.id}:5432").replace("?pgbouncer=true", "")
        # Actually wait, if the addon isn't named strictly that way, we can just disable schema update.
        print("Disabling schema update again properly.")
        env2, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DISABLE_SCHEMA_UPDATE")
        env2.value = "true"
        env2.save()
        env.save()
        
        # We need to ensure the schema IS built, otherwise UI breaks. 
        # LiteLLM needs a database to be migrated. We'll use stateless for now to just get chat working.
        env_store, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="STORE_MODEL_IN_DB")
        env_store.value = "False"
        env_store.save()
        
        print("Reverting back to stateless mode entirely.")
        
        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")
except Exception as e:
    print(f"Error: {e}")
