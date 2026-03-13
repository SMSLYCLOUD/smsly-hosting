import paramiko
host='163.245.216.249'
user='root'
pw='agbonsalo'
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(host, username=user, password=pw, timeout=10)
cmd="docker exec smsly-hosting-backend-1 python manage.py shell -c \"from apps.deployments.models_mesh import MeshNetwork; import json; print(json.dumps(list(MeshNetwork.objects.values('id','name','subnet','listen_port','interface_name')), indent=2))\""
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print(stdout.read().decode())
print(stderr.read().decode())
ssh.close()
