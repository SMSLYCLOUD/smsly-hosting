from apps.deployments.models import Service
from apps.cloud.models import CloudProvider

try:
    s = Service.objects.filter(name='friend_maker').first()
    if s:
        print(f"ID:{s.id} PROVIDER:{s.provider}")
    else:
        # Check all services to be sure
        all_s = Service.objects.all()
        for svc in all_s:
            print(f"EXISTING_SERVICE:{svc.name} ID:{svc.id}")
except Exception as e:
    print(f"ERROR: {str(e)}")
