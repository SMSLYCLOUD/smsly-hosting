import paramiko
user='root'
pw='agbonsalo'
host='163.245.216.248'
cmd="docker exec pg-replica psql -U postgres -tAc 'select pg_is_in_recovery(), now()'"
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy()); c.connect(host, username=user, password=pw, timeout=10, banner_timeout=10, auth_timeout=10)
stdin,stdout,stderr=c.exec_command(cmd, timeout=20)
print(stdout.read().decode())
print(stderr.read().decode())
c.close()
