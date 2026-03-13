import paramiko
HOST='163.245.214.62'
PW='agbonsalo'
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(HOST, username='root', password=PW, timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
for cmd in [
    "docker exec ai-router-b4635e3a sh -c 'ls /usr/lib/python3.13/site-packages/litellm_proxy_extras/prisma'",
    "docker exec ai-router-b4635e3a sh -c 'ls /app'",
]:
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
    print('CMD:', cmd)
    print(stdout.read().decode())
    print('ERR', stderr.read().decode())
ssh.close()
