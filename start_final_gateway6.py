import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.filter(name__icontains='ai-router').first()

    yaml_config = """model_list:
- model_name: braid-llm
  litellm_params:
    model: ollama/llama3.1
    api_base: http://llama3-1-7b-a818c603:11434
- model_name: braid-llm
  litellm_params:
    model: ollama/qwen2.5:0.5b
    api_base: http://qwen2-5-0-5b-ce905efc:11434
- model_name: braid-llm
  litellm_params:
    model: ollama/nomic-embed-text
    api_base: http://ollama-nomic-embed-text-f66ff1eb:11434
litellm_settings:
  drop_params: true
  telemetry: false
router_settings:
  routing_strategy: latency-based-routing
"""
    subprocess.run(["docker", "exec", "smsly-hosting-celery-1", "bash", "-c", f"echo '{yaml_config}' > /tmp/config.yaml"])

    cmd = [
        "docker", "run", "-d", "--name", "ai-router-cc22a7a5",
        "--restart", "unless-stopped",
        "--network", "smsly-net",
        # Use an environment variable to pass the raw string because volume mapping is breaking due to celery being inside a container!
        "-e", "LITELLM_MASTER_KEY=sk-agbonsalo",
        "-e", "STORE_MODEL_IN_DB=False",
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

    # Exec into the newly created litellm container to write the config!
    subprocess.run(["docker", "exec", "ai-router-cc22a7a5", "bash", "-c", f"echo '{yaml_config}' > /app/config.yaml"])

    # Restart litellm so it picks it up! Wait, we need to override the command.
    subprocess.run(["docker", "rm", "-f", "ai-router-cc22a7a5"])

    cmd.extend(["--config", "/app/config.yaml"])
    # We can't mount a file if the file doesn't exist on the host, but the celery container is running the docker command.
    # Let's just create the file on the host.
    pass

except Exception as e:
    print(f"Error: {e}")
