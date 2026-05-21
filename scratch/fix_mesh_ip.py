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
from apps.deployments.models import ManagedServer
server = ManagedServer.objects.get(host='69.164.244.51')

p1 = server.wg_peers.get(wg_address='10.150.0.2/32')
p1.is_active = False
p1.save()

p2 = server.wg_peers.get(wg_address='10.100.0.2/32')
p2.is_active = True
p2.save()

print("Swapped active WireGuard peers.")

from apps.deployments.tasks import _regenerate_caddyfile
_regenerate_caddyfile()
print("Regenerated Caddyfile.")
"""
        
        stdin, stdout, stderr = client.exec_command('docker exec -i smsly-hosting-backend-1 python manage.py shell')
        stdin.write(script)
        stdin.channel.shutdown_write()
        
        print(stdout.read().decode())
        print(stderr.read().decode())
    finally:
        client.close()

run_ssh("209.159.152.123", "agbonsalo")
