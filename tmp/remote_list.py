import paramiko
host='163.245.216.249'
user='root'
pw='agbonsalo'
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy()); c.connect(host, username=user, password=pw, timeout=10)
stdin,stdout,stderr=c.exec_command("docker ps --format '{{.Names}}'", timeout=20)
print(stdout.read().decode())
print(stderr.read().decode())
c.close()
