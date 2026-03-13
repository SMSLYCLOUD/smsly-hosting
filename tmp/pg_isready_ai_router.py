import paramiko
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.214.62', username='root', password='agbonsalo', timeout=25, banner_timeout=25, auth_timeout=25, allow_agent=False, look_for_keys=False)
cmd="docker run --rm --network smsly-net postgres:16-alpine pg_isready -h postgres-ai-router-b4635e3a -U postgres_ai_router_b4635e3a -d ai_router_b4635e3a"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print(stdout.read().decode())
print('ERR', stderr.read().decode())
ssh.close()
