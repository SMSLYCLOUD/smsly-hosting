import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.filter(name__icontains='ai-router').first()
    
    subprocess.run(["docker", "rm", "-f", svc.name])
    
    svc.docker_image = "ghcr.io/berriai/litellm:main-v1.45.0"
    svc.save(update_fields=['docker_image'])
    
    env_config, _ = svc.env_vars.get_or_create(key="LITELLM_CONFIG_PATH")
    env_config.value = "/app/proxy_server_config.yaml"
    env_config.save()
    
    env_store, _ = svc.env_vars.get_or_create(key="STORE_MODEL_IN_DB")
    env_store.value = "False"
    env_store.save()
    
    env_disable, _ = svc.env_vars.get_or_create(key="DISABLE_SCHEMA_UPDATE")
    env_disable.value = "true"
    env_disable.save()
    
    env_key, _ = svc.env_vars.get_or_create(key="LITELLM_MASTER_KEY")
    env_key.value = "sk-agbonsalo"
    env_key.save()

    svc.addons.set([])
    svc.env_vars.filter(key="DATABASE_URL").delete()
    
    from apps.deployments.models import Deployment
    from apps.deployments.tasks import smart_deploy_task
    
    d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
    smart_deploy_task.delay(str(d.id), str(svc.provider_id))
    print(f"Deployment triggered to fix SSL and restore litellm proxy for {svc.name}!")

except Exception as e:
    print(f"Error: {e}")
