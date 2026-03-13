import paramiko
host='163.245.216.248'
user='root'
pw='agbonsalo'
cmd="docker exec ai-router curl -s -w '\nHTTP:%{http_code}\n' http://localhost:4000/v1/models"
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(host, username=user, password=pw, timeout=10)
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print(stdout.read().decode())
print(stderr.read().decode())
ssh.close()
