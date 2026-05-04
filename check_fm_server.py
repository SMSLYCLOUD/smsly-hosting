from apps.deployments.models import Service
try:
    s = Service.objects.get(name='friend_maker')
    print(f"SERVICE:{s.name} SERVER:{s.server.name if s.server else 'None'} HOST:{s.server.host if s.server else 'N/A'}")
except Exception as e:
    print(f"ERROR: {str(e)}")
