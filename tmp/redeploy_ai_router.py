import paramiko
host='163.245.216.248'
user='root'
pw='agbonsalo'
cmd="docker rm -f ai-router >/dev/null 2>&1 || true; docker run -d --name ai-router --restart unless-stopped -e LITELLM_MASTER_KEY=agbonsalo -e OLLAMA_BASE_URL=http://ollama:11434 -e AI_ROUTER_API_BASE=/api -e AI_ROUTER_UI_BASE=/ -p 4000:4000 ghcr.io/berriai/litellm:main-stable"
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(host, username=user, password=pw, timeout=10)
stdin,stdout,stderr=ssh.exec_command(cmd, timeout=120)
print(stdout.read().decode())
print(stderr.read().decode())
ssh.close()
