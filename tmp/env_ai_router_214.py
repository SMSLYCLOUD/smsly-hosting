import paramiko, json
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.214.62', username='root', password='agbonsalo', timeout=25, banner_timeout=25, auth_timeout=25, allow_agent=False, look_for_keys=False)
cmd="docker inspect ai-router-b4635e3a --format '{{json .Config.Env}}'"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=20)
out = stdout.read().decode('utf-8','replace').strip()
err = stderr.read().decode('utf-8','replace').strip()
print('OUT', out)
print('ERR', err)
ssh.close()
