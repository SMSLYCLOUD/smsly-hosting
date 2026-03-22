import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc, created = Service.objects.get_or_create(
        name='llama-proxy-fdd07af0',
        defaults={
            'health_status': 'healthy',
            'status': 'ACTIVE',
            'public_domain': 'llama-proxy-fdd07af0-proxy.pcloud.linadeluxe.com',
            'port': 4000
        }
    )
    if not created:
        svc.health_status = 'healthy'
        svc.status = 'ACTIVE'
        svc.public_domain = 'llama-proxy-fdd07af0-proxy.pcloud.linadeluxe.com'
        svc.port = 4000
        svc.save()
    print("DB service correctly registered.")
except Exception as e:
    print(f"Error: {e}")
