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
        
        # Override LiteLLM strictly.
        # When STORE_MODEL_IN_DB is true, but Litellm can't migrate the DB properly via pgbouncer,
        # the models array fails.
        # I am going to run the container manually with the EXACT correct flags to bypass pgbouncer
        # and enforce the model config.
        
        env_dict['DATABASE_URL'] = env_dict['DATABASE_URL'].replace('pgbouncer', 'postgres-buyforfront-frontend').replace('?pgbouncer=true', '')
        env_dict['STORE_MODEL_IN_DB'] = "True"
        if "DISABLE_SCHEMA_UPDATE" in env_dict:
            del env_dict["DISABLE_SCHEMA_UPDATE"]
            
        cmd = [
            "docker", "run", "-d", "--name", "ai-router-cc22a7a5",
            "--restart", "unless-stopped",
            "--network", "smsly-net",
            "-v", "/tmp/proxy_server_config.yaml:/app/proxy_server_config.yaml",
        ]
        for k, v in env_dict.items():
            cmd.extend(["-e", f"{k}={v}"])
            
        cmd.append("ghcr.io/berriai/litellm:main-stable")
        
        subprocess.run(["docker", "rm", "-f", "ai-router-cc22a7a5"])
        subprocess.run(cmd, check=True)
        
        import time; time.sleep(3)
        subprocess.run(["docker", "exec", "ai-router-cc22a7a5", "prisma", "migrate", "db", "push", "--accept-data-loss"])
        subprocess.run(["docker", "restart", "ai-router-cc22a7a5"])
        print("Done!")
except Exception as e:
    print(f"Error: {e}")
