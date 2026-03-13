import paramiko
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.216.249', username='root', password='agbonsalo', timeout=30, banner_timeout=60, auth_timeout=30, allow_agent=False, look_for_keys=False)
stdin, stdout, stderr = ssh.exec_command('hostname', timeout=20)
print(stdout.read().decode()); print('ERR', stderr.read().decode()); ssh.close()
