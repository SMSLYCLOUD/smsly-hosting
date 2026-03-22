import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable
from django.db import transaction
import json
import subprocess

try:
    with transaction.atomic():
        services_to_delete = Service.objects.filter(
            name__icontains='qwen'
        ) | Service.objects.filter(
            name__icontains='nomic'
        ) | Service.objects.filter(
            name__icontains='llama-3-2'
        ) | Service.objects.filter(
            name__icontains='llama3-1'
        )
        
        # Protect only llama3-1-7b-a818c603
        for svc in list(services_to_delete):
            if "llama3-1-7b-a818c603" in svc.name:
                services_to_delete = services_to_delete.exclude(id=svc.id)
                
        for svc in services_to_delete:
            if "ai-router" not in svc.name:
                print(f"Deleting {svc.name} from database...")
                subprocess.run(["docker", "stop", svc.name])
                subprocess.run(["docker", "rm", svc.name])
                svc.delete()
                
        # Update AI Router to ONLY point to llama3-1-7b-a818c603
        router = Service.objects.filter(name='ai-router-cc22a7a5').first()
        llama = Service.objects.filter(name='llama3-1-7b-a818c603').first()
        
        if router and llama:
            env, _ = EnvironmentVariable.objects.get_or_create(service=router, key="AI_ROUTER_SELECTED_SERVICE_IDS")
            env.value = json.dumps([str(llama.id)])
            env.save()
            
            env2, _ = EnvironmentVariable.objects.get_or_create(service=router, key="AI_ROUTER_BRAID_ENABLED")
            env2.value = "false"
            env2.save()
            
        print("Cleanup complete!")
            
except Exception as e:
    print(f"Error: {e}")
