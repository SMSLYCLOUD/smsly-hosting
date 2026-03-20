import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable, Deployment, Addon
from apps.deployments.tasks import smart_deploy_task
from django.db import transaction
import subprocess

try:
    with transaction.atomic():
        svc = Service.objects.get(name='ai-router-cc22a7a5')

        addon = svc.addons.filter(addon_type="POSTGRES").first()

        # Okay, the pipeline doesn't seem to be picking up our changes immediately, or the container isn't running the prisma migration.
        # Let's manually run the migration on the current container because we know it's connected to postgres-buyforfront-frontend
        print("Running manual schema migration inside container...")

except Exception as e:
    print(f"Error: {e}")
