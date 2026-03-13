import paramiko
HOST='163.245.214.62'; PW='agbonsalo'
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(HOST, username='root', password=PW, timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
cmd = "docker exec ai-router-b4635e3a sh -c 'cd /app && npx prisma@6.7.0 db push --accept-data-loss'"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=300)
print(stdout.read().decode('utf-8','replace'))
print('ERR', stderr.read().decode('utf-8','replace'))
ssh.close()
