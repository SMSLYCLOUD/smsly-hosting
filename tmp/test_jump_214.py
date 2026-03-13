import paramiko
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.216.249', username='root', password='agbonsalo', timeout=30, banner_timeout=60, auth_timeout=30, allow_agent=False, look_for_keys=False)
cmd='ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/root/.ssh/known_hosts -o ConnectTimeout=15 root@163.245.214.62 hostname'
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=40)
print('OUT', stdout.read().decode())
print('ERR', stderr.read().decode())
ssh.close()
