
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
