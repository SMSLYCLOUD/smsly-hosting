import paramiko
import sys

sys.stdout.reconfigure(encoding='utf-8')

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    c.connect('163.245.216.248', username='root', password='agbonsalo', timeout=10)
    print("Connected to CloudNeuron control plane (163.245.216.248)")

    _, stdout, _ = c.exec_command(
        """docker exec smsly-hosting-backend-1 python manage.py shell -c "
from apps.deployments.models import Service
for s in Service.objects.all():
    server_ip = s.server.ip_address if hasattr(s, 'server') and s.server else 'no server'
    print(f'{s.name} | server={server_ip} | status={s.status}')
" 2>&1"""
    )
    print(stdout.read().decode('utf-8', errors='replace'))

except Exception as e:
    print(f"Connection failed: {e}")
finally:
    c.close()
