from apps.deployments.models import ManagedServer
try:
    servers = ManagedServer.objects.all()
    for s in servers:
        print(f"SERVER:{s.name} HOST:{s.host} PRIMARY:{s.is_primary}")
except Exception as e:
    print(f"ERROR: {str(e)}")
