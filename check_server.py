import paramiko

def run_on(ip, cmds, timeout=300):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip, username='root', password='agbonsalo', timeout=10)
    results = [f"\n=== {ip} ==="]
    for label, cmd in cmds:
        _, out, err = c.exec_command(cmd, timeout=timeout)
        result = out.read().decode().strip()
        errors = err.read().decode().strip()
        results.append(f"  [{label}]: {result[:600]}")
        if errors:
            results.append(f"  [{label} stderr]: {errors[:300]}")
    c.close()
    return "\n".join(results)

all_results = []
for ip in ['163.245.216.249', '163.245.214.62', '163.245.216.248']:
    r = run_on(ip, [
        ('git_pull', 'cd /opt/smsly-hosting && git pull origin main 2>&1 | tail -5'),
        ('rebuild_backend', 'cd /opt/smsly-hosting && docker compose -f docker-compose.prod.yml build --no-cache backend celery celery-beat 2>&1 | tail -5'),
        ('restart_backend', 'cd /opt/smsly-hosting && docker compose -f docker-compose.prod.yml up -d --no-deps --force-recreate backend celery celery-beat 2>&1'),
        ('verify', 'sleep 8 && docker compose -f /opt/smsly-hosting/docker-compose.prod.yml ps backend celery celery-beat --format "{{.Name}} {{.Status}}" 2>&1'),
    ])
    all_results.append(r)

with open(r'C:\Users\osaretin\Downloads\smslycloud-master\smsly-hosting\deploy_tier_fix.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(all_results))
print("Done")
