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

        # Okay, the database IS connected, but the Prisma schema literally has NO tables in it because
        # `prisma migrate deploy` isn't doing anything ("No migration found in prisma/migrations").
        # In LiteLLM, you have to use `prisma db push` to push the schema directly.
        # But even with that, the models aren't loading from the YAML.
        # WHY? Because the newest version of LiteLLM requires --config to be passed in the command line
        # to load the proxy_server_config.yaml when STORE_MODEL_IN_DB is true.

        cmd = [
            "docker", "run", "-d", "--name", "ai-router-cc22a7a5",
            "--restart", "unless-stopped",
            "--network", "smsly-net",
            "-v", "/tmp/proxy_server_config.yaml:/app/proxy_server_config.yaml",
        ]
        for k, v in env_dict.items():
            cmd.extend(["-e", f"{k}={v}"])

        # Here is the golden fix: adding --config !
        cmd.append("ghcr.io/berriai/litellm:main-stable")
        cmd.extend(["--config", "/app/proxy_server_config.yaml"])

        subprocess.run(["docker", "rm", "-f", "ai-router-cc22a7a5"])
        subprocess.run(cmd, check=True)

        import time; time.sleep(4)
        subprocess.run(["docker", "exec", "ai-router-cc22a7a5", "prisma", "db", "push", "--accept-data-loss"])
        print("Started final container with --config.")

except Exception as e:
    print(f"Error: {e}")
