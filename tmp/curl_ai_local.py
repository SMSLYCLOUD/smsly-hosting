import paramiko
host='163.245.216.248'
user='root'
pw='agbonsalo'
cmd="curl -s -w '\nHTTP:%{http_code}\n' -H 'Authorization: Bearer agbonsalo' http://localhost:4000/v1/models"
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(host, username=user, password=pw, timeout=10)
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=20)
print(stdout.read().decode())
print(stderr.read().decode())
ssh.close()
