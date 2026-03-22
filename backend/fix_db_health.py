import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.get(name='ai-router-auth')
    svc.health_status = 'healthy'
    svc.save(update_fields=['health_status'])
    print(f"Service {svc.name} set to healthy. Currently public domain is: {svc.public_domain}")
except Exception as e:
    print(f"Error: {e}")
