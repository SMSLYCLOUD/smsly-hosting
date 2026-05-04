from apps.deployments.models import ManagedServer

servers = ManagedServer.objects.all()

for s in servers:
    print(f"SERVER:{s.id} NAME:{s.name} HOST:{s.host} STATUS:{s.provision_status}")
    print(f"LOGS_PREVIEW:{s.provision_logs[-500:] if s.provision_logs else 'None'}")
    print("-" * 40)
