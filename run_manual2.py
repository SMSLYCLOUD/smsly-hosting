import os
import django
import subprocess
import yaml

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service, EnvironmentVariable
from apps.deployments.ai_router import generate_ai_router_proxy_config

svc = Service.objects.get(name='ai-router-cc22a7a5')
env_dict = {str(e.key): str(e.value) for e in svc.env_vars.all()}
env_dict['DATABASE_URL'] = "postgresql://smsly_admin:fe6d8a65e32282ed9928e3632c28f7f6@pgbouncer:5432/ai_router_cc22a7a5?pgbouncer=true"

yaml_config = generate_ai_router_proxy_config(svc)
with open("/tmp/proxy_server_config.yaml", "w") as f:
    f.write(yaml_config)

cmd = [
    "docker", "run", "-d", "--name", "ai-router-cc22a7a5",
    "--restart", "unless-stopped",
    "--network", "smsly-net",
    "-v", "/tmp/proxy_server_config.yaml:/app/proxy_server_config.yaml",
]
for k, v in env_dict.items():
    cmd.extend(["-e", f"{k}={v}"])

cmd.append("ghcr.io/berriai/litellm:main-stable")
print("Running", " ".join(cmd))
subprocess.run(["docker", "rm", "-f", "ai-router-cc22a7a5"])
subprocess.run(cmd, check=True)

import time; time.sleep(3)
print("Running migrations...")
subprocess.run(["docker", "exec", "ai-router-cc22a7a5", "prisma", "migrate", "db", "push", "--accept-data-loss"])

print("Restarting to pick up migrations...")
subprocess.run(["docker", "restart", "ai-router-cc22a7a5"])
