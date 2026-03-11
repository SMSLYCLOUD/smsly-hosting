import os
import django
import sys

# Setup Django
sys.path.append('/opt/smsly-hosting/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Deployment, Service

services_to_check = ['litellm', 'ollama', 'anythingllm', 'ai-router']

print("🔍 --- Intelligence Service Deployment Audit ---")
for s_name in services_to_check:
    services = Service.objects.filter(name__icontains=s_name)
    if not services.exists():
        continue
    
    for s in services:
        print(f"\n📦 SERVICE: {s.name} (UUID: {s.id})")
        print(f"Status: {s.status}")
        
        last_dep = s.deployments.order_by('-created_at').first()
        if not last_dep:
            print("No deployments found for this service.")
            continue
            
        print(f"Latest Deployment ID: {last_dep.id}")
        print(f"Deployment Status: {last_dep.status}")
        print(f"AI Diagnosis: {last_dep.ai_diagnosis}")
        
        print("\n--- ERROR LOGS (Last 100 lines) ---")
        logs = last_dep.build_logs or ""
        lines = logs.split('\n')
        for line in lines[-100:]:
            print(line)
        print("-----------------------------------")
