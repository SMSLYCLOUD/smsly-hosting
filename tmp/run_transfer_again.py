import base64, paramiko, textwrap
HOST='163.245.216.249'
PW='agbonsalo'
code=textwrap.dedent("""
import os
os.environ['ALLOW_SSH_AUTOADD']='true'
from apps.deployments.models_transfer import ServerTransfer
from apps.deployments.services.transfer_service import ServerTransferService

ServerTransfer.objects.filter(service_id='74a02812-8931-4f9a-bcd8-05303f9f0903').delete()

t = ServerTransfer.objects.create(
    source_server_ip='163.245.216.249',
    target_server_ip='163.245.216.248',
    target_ssh_password='agbonsalo',
    transfer_type='SERVICE',
    service_id='74a02812-8931-4f9a-bcd8-05303f9f0903',
)
print('Transfer ID:', t.id)
svc = ServerTransferService(t)
svc.execute()
t.refresh_from_db()
print('Status:', t.status, 'Error:', t.error_message)
""")
b64=base64.b64encode(code.encode()).decode()
cmd=f"docker exec -e ALLOW_SSH_AUTOADD=true smsly-hosting-backend-1 python manage.py shell -c \"import base64; exec(base64.b64decode('{b64}'))\""
ssh=paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy()); ssh.connect(HOST, username='root', password=PW, timeout=25, banner_timeout=25, auth_timeout=25, allow_agent=False, look_for_keys=False)
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=240)
print(stdout.read().decode())
print('ERR', stderr.read().decode())
ssh.close()
