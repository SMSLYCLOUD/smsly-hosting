import paramiko
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.214.62', username='root', password='agbonsalo', timeout=25, banner_timeout=25, auth_timeout=25, allow_agent=False, look_for_keys=False)
cmds=["docker start ai-router-b4635e3a", "sleep 5", "docker ps --format '{{.Names}}\t{{.Status}}' | grep ai-router-b4635e3a"]
for c in cmds:
    stdin, stdout, stderr = ssh.exec_command(c, timeout=30)
    print(c)
    print(stdout.read().decode())
    print('ERR', stderr.read().decode())
ssh.close()
