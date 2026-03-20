import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable, Deployment
from apps.deployments.tasks import smart_deploy_task
from django.db import transaction

try:
    with transaction.atomic():
        svc = Service.objects.get(name='ai-router-cc22a7a5')

        env_dict = {str(e.key): str(e.value) for e in svc.env_vars.all()}

        cmd = [
            "docker", "run", "-d", "--name", "ai-router-cc22a7a5",
            "--restart", "unless-stopped",
            "--network", "smsly-net",
            "-v", "/tmp/proxy_server_config.yaml:/app/proxy_server_config.yaml",
        ]
        for k, v in env_dict.items():
            cmd.extend(["-e", f"{k}={v}"])

        cmd.append("ghcr.io/berriai/litellm:main-v1.45.0")

        subprocess.run(["docker", "rm", "-f", "ai-router-cc22a7a5"])
        subprocess.run(cmd, check=True)
        print("Started temporary v1.45.0 container to test chat.")

except Exception as e:
    print(f"Error: {e}")
