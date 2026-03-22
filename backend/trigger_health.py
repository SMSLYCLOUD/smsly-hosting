import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.tasks import sync_docker_services
try:
    sync_docker_services()
    print("Health task forced to run.")
except Exception as e:
    print(e)
