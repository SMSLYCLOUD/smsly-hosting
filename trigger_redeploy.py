from apps.deployments.models import Service, Deployment
from apps.cloud.models import CloudProvider
from apps.deployments.tasks import smart_deploy_task
from apps.deployments.views import _resolve_provider_for_service
import sys

try:
    s = Service.objects.get(id='6f22cd8c-3ca3-40d4-8584-bc0031871173')
    provider = _resolve_provider_for_service(s)
    if not provider:
        print("ERROR: No provider found")
        sys.exit(1)
    
    d = Deployment.objects.create(
        service=s, 
        status='QUEUED', 
        commit_hash='latest', 
        commit_message='Manual Fix Trigger (Hardening Pipeline)'
    )
    # smart_deploy_task is a bound task, so we call it via .delay()
    smart_deploy_task.delay(deployment_id=str(d.id), provider_id=str(provider.id))
    print(f"TRIGGERED_DEPLOYMENT:{d.id} PROVIDER:{provider.id}")
except Exception as e:
    print(f"ERROR: {str(e)}")
    sys.exit(1)
