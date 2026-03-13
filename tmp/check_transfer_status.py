import paramiko
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect('163.245.216.249', username='root', password='agbonsalo', timeout=25, banner_timeout=25, auth_timeout=25, allow_agent=False, look_for_keys=False)
cmd="docker exec smsly-hosting-backend-1 python manage.py shell -c \"from apps.deployments.models_transfer import ServerTransfer; import json; print(list(ServerTransfer.objects.order_by('-created_at').values('id','status','error_message')[:5]))\""
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=40)
print(stdout.read().decode())
print('ERR', stderr.read().decode())
ssh.close()
