from apps.cloud.models import CloudProvider

try:
    providers = CloudProvider.objects.all()
    for p in providers:
        print(f"PROVIDER_ID:{p.id} NAME:{p.name} TYPE:{p.provider_type} ACTIVE:{p.is_active}")
except Exception as e:
    print(f"ERROR: {str(e)}")
