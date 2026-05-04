from apps.deployments.models import ManagedServer
try:
    s = ManagedServer.objects.get(name='prod')
    print(f"SERVER:{s.name} PROVIDER:{s.provider.name if s.provider else 'None'}")
except Exception as e:
    print(f"ERROR: {str(e)}")
