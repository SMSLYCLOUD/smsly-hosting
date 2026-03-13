import paramiko
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.214.62', username='root', password='agbonsalo', timeout=25, banner_timeout=25, auth_timeout=25, allow_agent=False, look_for_keys=False)
cmds = [
  "docker rm -f postgres-ai-router-b4635e3a || true",
  "docker volume create postgres-ai-router-b4635e3a-data >/dev/null",
  "docker run -d --name postgres-ai-router-b4635e3a --network smsly-net \
     -e POSTGRES_DB=ai_router_b4635e3a \
     -e POSTGRES_USER=postgres_ai_router_b4635e3a \
     -e POSTGRES_PASSWORD=_VNR2ZEiYa_7XUsnbJ7LvJO9hvdnTOYG \
     -v postgres-ai-router-b4635e3a-data:/var/lib/postgresql/data \
     postgres:16-alpine"
]
for c in cmds:
    stdin, stdout, stderr = ssh.exec_command(c, timeout=60)
    out = stdout.read().decode(); err = stderr.read().decode()
    print(c)
    print(out)
    print('ERR', err)
ssh.close()
