import paramiko

script = """
import os
import subprocess

token = '3ZUj1TGJrWF1JT7NqJzwfWmdt18Pu0MReq_HIy2p'
override_dir = '/etc/systemd/system/caddy.service.d'
override_file = os.path.join(override_dir, 'override.conf')

os.makedirs(override_dir, exist_ok=True)

content = f'''[Service]
Environment="CLOUDFLARE_API_TOKEN={token}"
'''

with open(override_file, 'w') as f:
    f.write(content)

subprocess.run(['systemctl', 'daemon-reload'], check=True)
subprocess.run(['systemctl', 'restart', 'caddy'], check=True)
print('Caddy restarted with Cloudflare token.')
"""

with open('fix_caddy_env_remote.py', 'w') as f:
    f.write(script)

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('163.245.216.248', username='root', password='agbonsalo')

sftp = c.open_sftp()
sftp.put('fix_caddy_env_remote.py', '/tmp/fix_caddy_env.py')
sftp.close()

_, stdout, stderr = c.exec_command('python3 /tmp/fix_caddy_env.py')
print(stdout.read().decode('utf-8', errors='replace'))
print(stderr.read().decode('utf-8', errors='replace'))

print('\\nTesting cloud.smsly.cloud resolution...')
_, stdout, stderr = c.exec_command('curl -s -o /dev/null -w "%{http_code}" https://cloud.smsly.cloud')
print('HTTP Code:', stdout.read().decode('utf-8', errors='replace'))
