from apps.deployments.models import Deployment

try:
    deps = Deployment.objects.filter(service_id='6f22cd8c-3ca3-40d4-8584-bc0031871173').order_by('-created_at')[:10]
    for d in deps:
        print(f"DEP_ID:{d.id} STATUS:{d.status} CREATED:{d.created_at}")
        print(f"BUILD_LOGS:\n{d.build_logs}\n---")
except Exception as e:
    print(f"ERROR: {str(e)}")
