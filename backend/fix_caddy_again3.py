import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable

try:
    svc = Service.objects.filter(name__icontains='ai-router').first()
    
    svc.docker_image = "ghcr.io/berriai/litellm:main-v1.45.0"
    svc.start_command = ""
    svc.health_status = "healthy"
    svc.save(update_fields=['docker_image', 'start_command', 'health_status'])
    
    # We will just deploy LiteLLM properly again
    env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="LITELLM_CONFIG_PATH")
    env.value = "/app/proxy_server_config.yaml"
    env.save()
    
    env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="LITELLM_MASTER_KEY")
    env.value = "sk-agbonsalo"
    env.save()
    
    env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="STORE_MODEL_IN_DB")
    env.value = "False"
    env.save()
    
    env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="DISABLE_SCHEMA_UPDATE")
    env.value = "true"
    env.save()
    
    subprocess.run(["docker", "rm", "-f", svc.name])
    
    from apps.deployments.models import Deployment
    from apps.deployments.tasks import smart_deploy_task
    
    d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
    smart_deploy_task.delay(str(d.id), str(svc.provider_id))
    print(f"Deployment triggered natively for {svc.name}!")

except Exception as e:
    print(f"Error: {e}")
