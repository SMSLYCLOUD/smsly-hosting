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
        
        # Litellm model config bypass config?
        # Maybe we should specify the model directly in litellm instead of via config path?
        # No, litellm accepts model config via litellm_config.yaml
        # Ah, look at this! LITELLM_CONFIG_PATH is not evaluated if it's passed as an env var in older versions.
        # Let's remove LITELLM_CONFIG_PATH env var and run litellm directly with --config
        # But we can't change the command easily.
        # Wait, litellm docs say litellm --config /path/to/config.yaml
        # Or you set LITELLM_CONFIG_PATH. It must be valid.
        
        # Wait! Is `AI_ROUTER_SELECTED_SERVICE_IDS` empty or missing?
        print("Checking service IDs...")
        env = EnvironmentVariable.objects.filter(service=svc, key="AI_ROUTER_SELECTED_SERVICE_IDS").first()
        if env:
            print("Selected IDs:", env.value)
            
        print("Forcing LITELLM_LOG to DEBUG to see why it rejects the model.")
        env_log, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="LITELLM_LOG")
        env_log.value = "DEBUG"
        env_log.save()
        
        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")
            
except Exception as e:
    print(f"Error: {e}")
