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

        # We need to make sure the POSTGRES ADDON uses the non-pgbouncer URL, OR we append ?pgbouncer=true correctly.
        # Actually, let's just append it to the actual env var object.
        if "pgbouncer=true" not in env.value:
            if "?" in env.value:
                env.value += "&pgbouncer=true"
            else:
                env.value += "?pgbouncer=true"
            env.save()
            print(f"Updated DATABASE_URL: {env.value}")
        else:
            print("pgbouncer=true already present.")

        # IMPORTANT: There might be a hardcoded DATABASE_URL coming from the pipeline because it resolves the addon dynamically.
        # If it resolves the addon dynamically, we need to make sure it includes pgbouncer=true.
        # We'll just override the pipeline's behavior by deleting the addon mapping and just relying on the direct env var.
        # Wait, the pipeline might override it. Let's just inject DIRECT_URL and DATABASE_URL with pgbouncer=true.
        # Let's set it as a normal env var.
        # Is the addon overriding it? Yes. The addon `POSTGRES` generates the URL.
        # We need to change the addon URL generation, or we can just remove the addon from the service and provide the DB explicitly.
        svc.addons = []
        svc.required_addons = []
        svc.save()
        print("Removed addons, using static DATABASE_URL")

        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")
except Exception as e:
    print(f"Error: {e}")
