import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.get(name='ai-router-cc22a7a5')
    
    # Clean up any bad manual docker containers
    subprocess.run(["docker", "rm", "-f", "ai-router-cc22a7a5"])
    
    # We will trigger a platform restart. The platform will spawn it as litellm, 
    # but we can edit the docker_image so it pulls nginx instead!
    
    svc.docker_image = "ghcr.io/berriai/litellm:main-v1.45.0"
    svc.save(update_fields=['docker_image'])
    
    # To use Basic Auth in Traefik natively via the platform:
    # We can inject labels directly into the Service's environment variables or metadata!
    # Let's just deploy LiteLLM normally. Why did it fail earlier? Because we deleted LITELLM_CONFIG_PATH.
    
    env_config, _ = svc.env_vars.get_or_create(key="LITELLM_CONFIG_PATH")
    env_config.value = "/app/proxy_server_config.yaml"
    env_config.save()
    
    env_store, _ = svc.env_vars.get_or_create(key="STORE_MODEL_IN_DB")
    env_store.value = "False"
    env_store.save()
    
    env_disable, _ = svc.env_vars.get_or_create(key="DISABLE_SCHEMA_UPDATE")
    env_disable.value = "true"
    env_disable.save()
    
    from apps.deployments.models import Deployment
    from apps.deployments.tasks import smart_deploy_task
    
    d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
    smart_deploy_task.delay(str(d.id), str(svc.provider_id))
    print("Deployment triggered to fix SSL and restore litellm proxy!")

except Exception as e:
    print(f"Error: {e}")
