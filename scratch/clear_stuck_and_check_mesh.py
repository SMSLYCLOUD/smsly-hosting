import paramiko
import sys

def run_ssh(host, password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username="root", password=password)
        
        script = """
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
import django
django.setup()
from apps.deployments.models import Deployment, ManagedServer
updated = Deployment.objects.filter(status='BUILDING').update(status='FAILED', ai_diagnosis='Manually cancelled')
print(f'Cancelled {updated} deployments.')

server = ManagedServer.objects.get(host='69.164.244.51')
print("Server:", server)
for peer in server.wg_peers.all():
    print("Peer IP:", peer.address, "Mesh:", peer.mesh.name, "Active:", peer.is_active)
"""
        
        stdin, stdout, stderr = client.exec_command('docker exec -i smsly-hosting-backend-1 python manage.py shell')
        stdin.write(script)
        stdin.channel.shutdown_write()
        
        print(stdout.read().decode())
        print(stderr.read().decode())
    finally:
        client.close()

run_ssh("209.159.152.123", "agbonsalo")
