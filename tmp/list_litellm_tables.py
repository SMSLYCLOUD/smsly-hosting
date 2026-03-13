import paramiko
HOST='163.245.214.62'; PW='agbonsalo'
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(HOST, username='root', password=PW, timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
query = "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'LiteLLM_%';"
cmd = f"docker exec postgres-ai-router-b4635e3a psql -U postgres_ai_router_b4635e3a -d ai_router_b4635e3a -c \"{query}\""
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print(stdout.read().decode('utf-8','replace'))
print('ERR', stderr.read().decode('utf-8','replace'))
ssh.close()
