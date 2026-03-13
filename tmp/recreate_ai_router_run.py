import paramiko, textwrap
HOST='163.245.214.62'
PW='agbonsalo'
config = textwrap.dedent('''
model_list:
  - model_name: phi3
    litellm_params:
      model: ollama/phi3
      provider: ollama
      api_base: http://ollama-smsly:11434
      api_key: ""

litellm_settings:
  require_auth_for_server_healthcheck: false
  require_auth_for_openai_routes: false
''')
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(HOST, username='root', password=PW, timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
# write config
sftp=ssh.open_sftp(); f=sftp.file('/root/proxy_server_config.yaml','w'); f.write(config); f.close(); sftp.close()
cmds=[
    "docker rm -f ai-router-b4635e3a || true",
    "docker run -d --name ai-router-b4635e3a --network smsly-net --memory=2g -p 4000:4000 "
    "-e PORT=4000 -e UVICORN_WORKERS=1 -e LITELLM_MASTER_KEY=agbonsalo "
    "-e PUBLIC_DOMAIN=ai-router-b4635e3a-454fd9.pcloud.linadeluxe.com "
    "-e DATABASE_URL=postgresql://postgres_ai_router_b4635e3a:_VNR2ZEiYa_7XUsnbJ7LvJO9hvdnTOYG@postgres-ai-router-b4635e3a:5432/ai_router_b4635e3a "
    "-e HOSTNAME=0.0.0.0 -e LITELLM_PROXY_CONFIG=/app/proxy_server_config.yaml "
    "-v /root/proxy_server_config.yaml:/app/proxy_server_config.yaml:ro "
    "ghcr.io/berriai/litellm:main-stable --config /app/proxy_server_config.yaml --port 4000 --host 0.0.0.0"
]
for c in cmds:
    stdin, stdout, stderr = ssh.exec_command(c, timeout=180)
    print(c)
    print(stdout.read().decode())
    print('ERR', stderr.read().decode())
ssh.close()
