from apps.deployments.models import Deployment

try:
    d = Deployment.objects.first()
    if d:
        fields = [f.name for f in d._meta.get_fields()]
        print(f"FIELDS:{fields}")
except Exception as e:
    print(f"ERROR: {str(e)}")
