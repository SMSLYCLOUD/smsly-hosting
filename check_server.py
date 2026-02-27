import paramiko

def run_on(ip, cmds, timeout=120):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip, username='root', password='agbonsalo', timeout=10)
    results = [f"\n=== {ip} ==="]
    for label, cmd in cmds:
        _, out, err = c.exec_command(cmd, timeout=timeout)
        result = out.read().decode().strip()
        errors = err.read().decode().strip()
        results.append(f"  [{label}]: {result[:800]}")
        if errors:
            results.append(f"  [{label} stderr]: {errors[:300]}")
    c.close()
    return "\n".join(results)

all_results = []

# 1. Fix Caddy on .249: use domain name for auto-HTTPS
caddy_fix = """
cat > /etc/caddy/Caddyfile << 'CADDY'
pcloud.distinctionlabs.org {
    reverse_proxy localhost:8090
}
CADDY
systemctl reload caddy 2>&1
"""

all_results.append(run_on('163.245.216.249', [
    ('fix_caddy', caddy_fix),
    ('caddy_verify', 'sleep 3 && systemctl status caddy 2>&1 | head -8'),
]))

# 2. Pull and rebuild frontend on all 3 servers
for ip in ['163.245.216.249', '163.245.214.62', '163.245.216.248']:
    rebuild_cmds = [
        ('git_pull', 'cd /opt/smsly-hosting && git pull origin main 2>&1'),
        ('rebuild_frontend', 'cd /opt/smsly-hosting && docker compose -f docker-compose.prod.yml build --no-cache frontend 2>&1 | tail -5'),
        ('restart_frontend', 'cd /opt/smsly-hosting && docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate frontend 2>&1'),
        ('verify', 'sleep 5 && docker compose -f /opt/smsly-hosting/docker-compose.prod.yml ps frontend --format "{{.Name}} {{.Status}}" 2>&1'),
    ]
    all_results.append(run_on(ip, rebuild_cmds, timeout=300))

with open(r'C:\Users\osaretin\Downloads\smslycloud-master\smsly-hosting\deploy_results.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(all_results))
print("Done")
