from apps.deployments.models import Deployment
for d in Deployment.objects.all().order_by("-created_at")[:10]:
    print(f"ID: {d.id} | Status: {d.status} | Rollback: {d.is_rollback} | Created: {d.created_at}")
