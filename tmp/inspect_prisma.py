import paramiko
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.214.62', username='root', password='agbonsalo', timeout=25, banner_timeout=25, auth_timeout=25, allow_agent=False, look_for_keys=False)
cmds=["docker exec ai-router-b4635e3a sh -c 'ls prisma'", "docker exec ai-router-b4635e3a sh -c 'ls /app'", "docker exec ai-router-b4635e3a sh -c 'which prisma'", "docker exec ai-router-b4635e3a sh -c 'find / -maxdepth 3 -name prisma -type f 2>/dev/null | head'" ]
for c in cmds:
    stdin, stdout, stderr = ssh.exec_command(c, timeout=40)
    print('CMD', c)
    print(stdout.read().decode())
    print('ERR', stderr.read().decode())
ssh.close()
