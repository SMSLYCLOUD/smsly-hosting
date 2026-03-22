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

        print("Updating Environment Variables...")
        env = EnvironmentVariable.objects.get(service=svc, key="DATABASE_URL")
        
        # Append pgbouncer query param if it doesn't exist
        if "pgbouncer=true" not in env.value:
            if "?" in env.value:
                env.value += "&pgbouncer=true"
            else:
                env.value += "?pgbouncer=true"
            env.save()
            print(f"Updated DATABASE_URL: {env.value}")
        else:
            print("pgbouncer=true already present.")
        
        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")
except Exception as e:
    print(f"Error: {e}")
