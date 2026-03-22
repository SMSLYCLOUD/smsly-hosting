import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.get(name='llama3-1-7b-a818c603')
    
    svc.public_domain = "llama3-1-7b.pcloud.linadeluxe.com"
    svc.health_status = 'healthy'
    svc.port = 11434
    svc.save()
    
    print(f"Service {svc.name} saved. Domain: {svc.public_domain}")

except Exception as e:
    print(f"Error: {e}")
