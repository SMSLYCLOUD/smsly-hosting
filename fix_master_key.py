import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable

try:
    svc = Service.objects.get(name='ai-router-cc22a7a5')
    env = EnvironmentVariable.objects.get(service=svc, key='LITELLM_MASTER_KEY')
    print(f"Current DB key: {env.value}")
    if not env.value.startswith('sk-'):
        env.value = f"sk-{env.value}"
        env.save()
        print(f"Updated DB key to: {env.value}")
    else:
        print("Key already starts with sk-")
except Exception as e:
    print(f"Error: {e}")
