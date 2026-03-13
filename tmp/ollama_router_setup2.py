import paramiko, sys
host='163.245.216.248'
user='root'
pw='agbonsalo'
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(host, username=user, password=pw, timeout=10)
cmds=[
"docker exec ollama ollama pull llama3:8b",
"docker exec ollama ollama pull mistral",
"docker exec ollama ollama pull gemma",
"docker run -d --name ai-router --restart unless-stopped --network bridge -e LITELLM_MASTER_KEY=agbonsalo -e OLLAMA_BASE_URL=http://ollama:11434 -e AI_ROUTER_API_BASE=/api -e AI_ROUTER_UI_BASE=/ -p 4000:4000 ghcr.io/berriai/litellm:main-stable"
]
for cmd in cmds:
    stdin,stdout,stderr=ssh.exec_command(cmd, timeout=400)
    rc=stdout.channel.recv_exit_status(); out=stdout.read(); err=stderr.read();
    sys.stdout.buffer.write(f"CMD: {cmd}\nRC: {rc}\n".encode()); sys.stdout.buffer.write(out); sys.stdout.buffer.write(err); sys.stdout.buffer.write(b"\n---\n")
ssh.close()
