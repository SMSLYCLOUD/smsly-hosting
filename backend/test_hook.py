import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
django.setup()

from apps.deployments.models import Deployment, Service
from django.utils import timezone

svc = Service.objects.first()
if not svc:
    print("No service found")
else:
    dep = Deployment.objects.create(service=svc, status='ACTIVE', target_server=svc.server, commit_hash='test1')
    print("Created dep:", dep.id, dep.status)
    dep.status = 'ACTIVE'
    dep.save()
    
    dep.refresh_from_db()
    print("After save dep status:", dep.status)
    
    dep.delete()
