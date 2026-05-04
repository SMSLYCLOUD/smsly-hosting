from apps.deployments.models import Service, ManagedServer

try:
    primary = ManagedServer.objects.filter(is_primary=True).first()
    if primary:
        print(f"PRIMARY_SERVER:{primary.name} ({primary.host})")
        services = Service.objects.filter(server=primary)
        for s in services:
            print(f"PRIMARY_SERVICE:{s.name}")
    
    all_services = Service.objects.all()
    for s in all_services:
        server_name = s.server.name if s.server else 'None'
        server_host = s.server.host if s.server else 'N/A'
        print(f"SERVICE:{s.name} SERVER:{server_name} HOST:{server_host}")
except Exception as e:
    print(f"ERROR: {str(e)}")
