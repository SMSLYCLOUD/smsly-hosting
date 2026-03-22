import os
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service
from apps.deployments.tasks import redeploy_service_task

def trigger():
    s = Service.objects.filter(name__icontains='ai-router').first()
    if s:
        print(f"Triggering redeploy for {s.name} ({s.id})")
        redeploy_service_task.delay(str(s.id))
    else:
        print("AI Router service not found")

if __name__ == '__main__':
    trigger()
