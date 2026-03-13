import paramiko, secrets
user='root'
pw='agbonsalo'
host='163.245.216.248'
key=secrets.token_hex(16)
cmds=[
    "docker rm -f ai-router >/dev/null 2>&1 || true",
    f"docker run -d --name ai-router --restart unless-stopped -e LITELLM_MASTER_KEY={key} -e OLLAMA_BASE_URL=http://ollama:11434 -e AI_ROUTER_API_BASE=/api -e AI_ROUTER_UI_BASE=/ -p 4000:4000 ghcr.io/berriai/litellm:main-stable"
]

c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy()); c.connect(host, username=user, password=pw, timeout=10, banner_timeout=10, auth_timeout=10)
for cmd in cmds:
    stdin,stdout,stderr=c.exec_command(cmd, timeout=60)
    rc=stdout.channel.recv_exit_status(); out=stdout.read().decode(); err=stderr.read().decode()
    print(cmd.split()[0], 'rc', rc, 'out', out.strip(), 'err', err.strip())
c.close(); print('master key', key)
