from apps.deployments.models import Service, Deployment
from apps.cloud.models import CloudProvider
from apps.deployments.tasks import smart_deploy_task
from apps.deployments.views import _resolve_provider_for_service
import sys

try:
    s = Service.objects.get(id='6f22cd8c-3ca3-40d4-8584-bc0031871173')
    print(f"DEBUG: Found service {s.name}")
    
    provider = _resolve_provider_for_service(s)
    if not provider:
        print("ERROR: No provider found")
        sys.exit(1)
    
    print(f"DEBUG: Resolved provider: {provider} (ID: {getattr(provider, 'id', 'N/A')})")
    
    d = Deployment.objects.create(
        service=s, 
        status='QUEUED', 
        commit_hash='latest', 
        commit_message='Manual Fix Trigger (Hardening Pipeline v3)'
    )
    print(f"DEBUG: Created deployment {d.id}")
    
    # smart_deploy_task.delay(deployment_id=str(d.id), provider_id=str(provider.id))
    # Let's try calling it synchronously for debugging if possible, or just print everything
    print(f"TRIGGERED_DEPLOYMENT:{d.id} PROVIDER:{provider.id}")
    
    # Actually trigger it
    smart_deploy_task.delay(deployment_id=str(d.id), provider_id=str(provider.id))
    print("SUCCESS: Task enqueued")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {str(e)}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
