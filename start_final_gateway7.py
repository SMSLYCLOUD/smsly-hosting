import subprocess

yaml_config = """model_list:
- model_name: braid-llm
  litellm_params:
    model: ollama/llama3.1:7b
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
with open("/tmp/proxy_server_config3.yaml", "w") as f:
    f.write(yaml_config)

cmd = [
    "docker", "run", "-d", "--name", "ai-router-cc22a7a5",
    "--restart", "unless-stopped",
    "--network", "smsly-net",
    "-v", "/tmp/proxy_server_config3.yaml:/app/proxy_server_config3.yaml",
    "-e", "LITELLM_MASTER_KEY=sk-agbonsalo",
    "-e", "STORE_MODEL_IN_DB=False",
    "-l", "smsly.service=true",
    "-l", "smsly.service_id=fb6b6469-ecf4-4261-b46c-c7a86480b2cf",
    "-l", "smsly.public_domain=ai-router-cc22a7a5-17ead9.pcloud.linadeluxe.com",
    "-l", "smsly.internal_port=4000",
    "-l", "traefik.enable=true",
    "-l", "traefik.http.routers.ai-router-cc22a7a5.rule=Host(`ai-router-cc22a7a5-17ead9.pcloud.linadeluxe.com`)",
    "-l", "traefik.http.services.ai-router-cc22a7a5.loadbalancer.server.port=4000",
    "ghcr.io/berriai/litellm:main-v1.45.0",
    "--config", "/app/proxy_server_config3.yaml"
]

subprocess.run(["docker", "rm", "-f", "ai-router-cc22a7a5"])
subprocess.run(cmd, check=True)
print("Started router!")
