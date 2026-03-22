import os
import django
import subprocess

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.deployments.models import Service

try:
    svc = Service.objects.get(name='ai-router-cc22a7a5')
    
    env_dict = {str(e.key): str(e.value) for e in svc.env_vars.all()}
    
    # Force remove any bad configs
    env_dict['STORE_MODEL_IN_DB'] = "False"
    if "DATABASE_URL" in env_dict: del env_dict["DATABASE_URL"]
    if "REDIS_URL" in env_dict: del env_dict["REDIS_URL"]
    env_dict['LITELLM_MASTER_KEY'] = "sk-agbonsalo"
    
    # We write a custom, perfectly valid config for the two models
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
    with open("/tmp/proxy_server_config.yaml", "w") as f:
        f.write(yaml_config)
    
    cmd = [
        "docker", "run", "-d", "--name", "ai-router-cc22a7a5",
        "--restart", "unless-stopped",
        "--network", "smsly-net",
        "-v", "/tmp/proxy_server_config.yaml:/app/proxy_server_config.yaml",
        "-l", "smsly.service=true",
        "-l", f"smsly.service_id={svc.id}",
        "-l", "smsly.public_domain=ai-router-cc22a7a5-17ead9.pcloud.linadeluxe.com",
        "-l", "smsly.internal_port=4000",
        "-l", "traefik.enable=true",
        "-l", "traefik.http.routers.ai-router-cc22a7a5.rule=Host(`ai-router-cc22a7a5-17ead9.pcloud.linadeluxe.com`)",
        "-l", "traefik.http.services.ai-router-cc22a7a5.loadbalancer.server.port=4000",
    ]
    for k, v in env_dict.items():
        cmd.extend(["-e", f"{k}={v}"])
        
    cmd.append("ghcr.io/berriai/litellm:main-v1.45.0")
    cmd.extend(["--config", "/app/proxy_server_config.yaml"])
    
    subprocess.run(["docker", "rm", "-f", "ai-router-cc22a7a5"])
    subprocess.run(cmd, check=True)
    print("Force started ai-router with LiteLLM v1.45.0 and direct configuration.")

    from services.caddy_manager import CaddyManager
    try:
        cm = CaddyManager()
        cm.generate_global_caddyfile()
        cm.reload_caddy()
        print("Caddyfile reloaded.")
    except:
        pass
            
except Exception as e:
    print(f"Error: {e}")
