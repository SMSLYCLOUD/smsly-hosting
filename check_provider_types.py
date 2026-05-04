from apps.cloud.models import CloudProvider

try:
    print(f"CHOICES: {CloudProvider.ProviderType.choices}")
    print(f"DIR: {dir(CloudProvider.ProviderType)}")
except Exception as e:
    print(f"ERROR: {str(e)}")
