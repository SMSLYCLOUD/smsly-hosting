import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.filter(name__icontains='ai-router-cc22a7a5').first()
    if not svc:
        print("Router service missing, creating...")
        from apps.deployments.models import Service
        # We'll just assume it exists
        svc = Service.objects.get(name='ai-router-cc22a7a5')
        
    env_dict = {str(e.key): str(e.value) for e in svc.env_vars.all()}
    
    yaml_config = """model_list:
- model_name: llama3.1
  litellm_params:
    model: ollama/llama3.1
    api_base: http://llama3-1-7b-a818c603:11434
litellm_settings:
  drop_params: true
  telemetry: false
"""
    subprocess.run(["docker", "exec", "smsly-hosting-celery-1", "bash", "-c", f"echo '{yaml_config}' > /tmp/config.yaml"])
    
    cmd = [
        "docker", "run", "-d", "--name", "ai-router-cc22a7a5",
        "--restart", "unless-stopped",
        "--network", "smsly-net",
        "-e", "LITELLM_MASTER_KEY=sk-agbonsalo",
        "-e", "STORE_MODEL_IN_DB=False",
        "-e", f"LITELLM_CONFIG_PATH=/app/config.yaml",
        "-l", "smsly.service=true",
        "-l", f"smsly.service_id={svc.id}",
        "-l", "smsly.public_domain=ai-router-cc22a7a5-17ead9.pcloud.linadeluxe.com",
        "-l", "smsly.internal_port=4000",
        "-l", "traefik.enable=true",
        "-l", "traefik.http.routers.ai-router-cc22a7a5.rule=Host(`ai-router-cc22a7a5-17ead9.pcloud.linadeluxe.com`)",
        "-l", "traefik.http.services.ai-router-cc22a7a5.loadbalancer.server.port=4000",
    ]
        
    cmd.append("ghcr.io/berriai/litellm:main-v1.45.0")
    
    subprocess.run(["docker", "rm", "-f", "ai-router-cc22a7a5"])
    subprocess.run(cmd, check=True)
    
    subprocess.run(["docker", "exec", "ai-router-cc22a7a5", "bash", "-c", f"echo '{yaml_config}' > /app/config.yaml"])
    subprocess.run(["docker", "restart", "ai-router-cc22a7a5"])
    
    print("Started stateless LiteLLM v1.45.0 for Llama 3.1 authentication.")

except Exception as e:
    print(f"Error: {e}")
