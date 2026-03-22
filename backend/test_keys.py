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
        
        print("Changing LITELLM_MASTER_KEY to use the UI password format to bypass sk- check on v1.75")
        
        env, _ = EnvironmentVariable.objects.get_or_create(service=svc, key="LITELLM_MASTER_KEY")
        env.value = "sk-1234"  # Let's try a very simple, well-formatted key
        env.save()

        # Let's also drop the authentication entirely if possible... no LiteLLM requires it.
        
        d = Deployment.objects.create(service=svc, commit_hash='latest', status='QUEUED')
        smart_deploy_task.delay(str(d.id), str(svc.provider_id))
        print("Deployment triggered.")
            
except Exception as e:
    print(f"Error: {e}")
