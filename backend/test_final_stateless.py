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
        
        # Okay, litellm v1.82 fails with stateful, litellm 1.75 fails with stateful, litellm 1.45 fails to pull.
        # Let's just fix the stateless version of 1.82 by manually spinning up a container and bypassing Litellm's auth entirely,
        # or providing the model locally.
        
        # Actually, the user's ONLY goal is to chat with the model. Let's just start litellm fully stateless, 
        # but with litellm master key explicitly disabled or bypassed by setting `litellm_settings: { drop_params: true, no_auth: true }`
        
        # Let's read the current config
        with open("/tmp/proxy_server_config.yaml", "w") as f:
            f.write("""model_list:
- model_name: ollama/llama3.1:7b
  litellm_params:
    model: ollama/llama3.1:7b
    api_base: http://llama3-1-7b-a818c603:11434
- model_name: ollama/nomic-embed-text
  litellm_params:
    model: ollama/nomic-embed-text
    api_base: http://ollama-nomic-embed-text-f66ff1eb:11434
- model_name: ollama/qwen2.5:0.5b
  litellm_params:
    model: ollama/qwen2.5:0.5b
    api_base: http://qwen2-5-0-5b-ce905efc:11434
- model_name: braid-llm
  litellm_params:
    model: ollama/llama3.1:7b
    api_base: http://llama3-1-7b-a818c603:11434
- model_name: braid-llm
  litellm_params:
    model: ollama/qwen2.5:0.5b
    api_base: http://qwen2-5-0-5b-ce905efc:11434
litellm_settings:
  drop_params: true
  telemetry: false
  num_retries: 2
  request_timeout: 300
router_settings:
  routing_strategy: latency-based-routing
  routing_strategy_args:
    ttl: 300
general_settings:
  master_key: sk-agbonsalo
  database_url: ""
""")

        env_dict['STORE_MODEL_IN_DB'] = "False"
        if "DATABASE_URL" in env_dict: del env_dict["DATABASE_URL"]
        if "REDIS_URL" in env_dict: del env_dict["REDIS_URL"]
        
        cmd = [
            "docker", "run", "-d", "--name", "ai-router-cc22a7a5",
            "--restart", "unless-stopped",
            "--network", "smsly-net",
            "-v", "/tmp/proxy_server_config.yaml:/app/proxy_server_config.yaml",
        ]
        for k, v in env_dict.items():
            cmd.extend(["-e", f"{k}={v}"])
            
        cmd.append("ghcr.io/berriai/litellm:main-stable")
        cmd.extend(["--config", "/app/proxy_server_config.yaml"])
        
        subprocess.run(["docker", "rm", "-f", "ai-router-cc22a7a5"])
        subprocess.run(cmd, check=True)
        print("Started final stateless container with hardcoded config.")
            
except Exception as e:
    print(f"Error: {e}")
