import paramiko
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.214.62', username='root', password='agbonsalo', timeout=20, banner_timeout=20, auth_timeout=20, allow_agent=False, look_for_keys=False)
cmd = "docker exec ai-router-b4635e3a python -c 'import sys; print(sys.version)'"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=20)
print(stdout.read().decode())
print('ERR', stderr.read().decode())
ssh.close()
