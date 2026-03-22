import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable
from apps.deployments.services.addon_service import AddonService
from apps.deployments.tasks import smart_deploy_task
from django.db import transaction

try:
    with transaction.atomic():
        svc = Service.objects.get(name='ai-router-cc22a7a5')
        
        # 1. Attach Postgres Addon
        print("Attaching POSTGRES Addon...")
        addons = svc.required_addons or []
        if "POSTGRES" not in addons:
            addons.append("POSTGRES")
            svc.required_addons = addons
            svc.save(update_fields=['required_addons'])
        
        addon_service = AddonService(svc)
        addon_service.provision_addons()
        urls = addon_service.get_addon_urls()
        db_url = urls.get('POSTGRES')
        print(f"Provisioned DB URL: {db_url}")

        # 2. Update Environment Variables
        print("Updating Environment Variables...")
        # Remove DISABLE_SCHEMA_UPDATE
        EnvironmentVariable.objects.filter(service=svc, key="DISABLE_SCHEMA_UPDATE").delete()
        
        # Set STORE_MODEL_IN_DB to True
        EnvironmentVariable.objects.update_or_create(
            service=svc, key="STORE_MODEL_IN_DB",
            defaults={"value": "True", "is_secret": False}
        )

        # Set DATABASE_URL
        if db_url:
            EnvironmentVariable.objects.update_or_create(
                service=svc, key="DATABASE_URL",
                defaults={"value": db_url, "is_secret": True}
            )

        # 3. Set Post-Deploy Hook for Prisma Migration
        hooks = svc.post_deploy_hooks or []
        hook_cmd = "prisma migrate db push --accept-data-loss"
        if hook_cmd not in hooks:
            hooks.append(hook_cmd)
            svc.post_deploy_hooks = hooks
            svc.save(update_fields=['post_deploy_hooks'])
        print(f"Hooks configured: {svc.post_deploy_hooks}")

        print("Stateful configuration successfully applied.")
        
        # 4. Trigger Deploy
        from apps.deployments.models import Deployment
        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")
except Exception as e:
    print(f"Error: {e}")
