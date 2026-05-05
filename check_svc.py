from apps.deployments.models import Service
for s in Service.objects.all():
    print(f"Name: {s.name} | Deploy Type: {s.deploy_type} | Server: {s.server}")
