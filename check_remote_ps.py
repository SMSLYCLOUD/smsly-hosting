from apps.deployments.services.ssh_client import SSHClient
from apps.deployments.models import ManagedServer
try:
    s = ManagedServer.objects.get(host='153.75.247.117')
    client = SSHClient(ip=s.host, user=s.ssh_user or 'root', key_content=s.gateway_secret)
    stdout, stderr, code = client.exec_command('docker ps --format "{{.Names}}"')
    print(f"REMOTE_PS_STDOUT:\n{stdout}")
    print(f"REMOTE_PS_STDERR:\n{stderr}")
    print(f"REMOTE_PS_CODE:{code}")
except Exception as e:
    print(f"ERROR: {str(e)}")
