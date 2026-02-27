import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('163.245.216.249', username='root', password='agbonsalo', timeout=10)

cmds = [
    ('caddy_config', 'cat /etc/caddy/Caddyfile 2>&1 || cat /opt/caddy/Caddyfile 2>&1 || find / -name Caddyfile -maxdepth 4 2>/dev/null | head -5'),
    ('caddy_status', 'systemctl status caddy 2>&1 | head -15'),
    ('caddy_version', 'caddy version 2>&1'),
    ('caddy_logs', 'journalctl -u caddy --no-pager -n 20 2>&1'),
    ('curl_https', 'curl -sk -o /dev/null -w "%{http_code}" https://pcloud.distinctionlabs.org/ 2>&1'),
    ('curl_http', 'curl -s -o /dev/null -w "%{http_code}" http://pcloud.distinctionlabs.org/ 2>&1'),
]

results = []
for label, cmd in cmds:
    _, out, err = c.exec_command(cmd, timeout=15)
    result = out.read().decode().strip()
    errors = err.read().decode().strip()
    results.append(f"[{label}]: {result[:800]}")
    if errors:
        results.append(f"[{label} stderr]: {errors[:300]}")

c.close()

with open(r'C:\Users\osaretin\Downloads\smslycloud-master\smsly-hosting\caddy_diag.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(results))
print("Done")
