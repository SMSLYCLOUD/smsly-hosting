import paramiko, sys
host='163.245.216.248'
user='root'
pw='agbonsalo'
cmd="docker logs --tail=80 ai-router"
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(host, username=user, password=pw, timeout=10)
stdin,stdout,stderr=ssh.exec_command(cmd, timeout=20)
sys.stdout.buffer.write(stdout.read())
sys.stdout.buffer.write(stderr.read())
ssh.close()
