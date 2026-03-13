import paramiko
HOST='163.245.214.62'; PW='agbonsalo'
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(HOST, username='root', password=PW, timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
cmd = "docker exec postgres-ai-router-b4635e3a psql -U postgres_ai_router_b4635e3a -d ai_router_b4635e3a -c \"\\d \\\"LiteLLM_ProxyModelTable\\\";\""
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print(stdout.read().decode())
print('ERR', stderr.read().decode())
ssh.close()
