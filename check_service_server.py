from apps.deployments.models import Service

try:
    s = Service.objects.get(id='6f22cd8c-3ca3-40d4-8584-bc0031871173')
    print(f"ID:{s.id} NAME:{s.name} SERVER_ID:{s.server_id} PROVIDER_ID:{s.provider_id}")
    if s.server:
        print(f"SERVER_NAME:{s.server.name} HOST:{s.server.host} IS_PRIMARY:{s.server.is_primary}")
except Exception as e:
    print(f"ERROR: {str(e)}")
