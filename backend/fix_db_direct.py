import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc, created = Service.objects.get_or_create(
        name='ai-router-direct-b23d9',
        defaults={
            'health_status': 'healthy',
            'public_domain': 'ai-router-direct-b23d9.pcloud.linadeluxe.com',
            'port': 4000
        }
    )
    if not created:
        svc.health_status = 'healthy'
        svc.public_domain = 'ai-router-direct-b23d9.pcloud.linadeluxe.com'
        svc.port = 4000
        svc.save(update_fields=['health_status', 'public_domain', 'port'])
    print("DB record synced for ai-router-direct-b23d9.")
except Exception as e:
    print(f"Error saving to DB: {e}")
