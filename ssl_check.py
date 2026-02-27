import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('163.245.216.249', username='root', password='agbonsalo', timeout=10)

cmds = [
    # Check all container status
    ('containers', 'docker compose -f /opt/smsly-hosting/docker-compose.prod.yml ps --format "{{.Name}} {{.Status}}" 2>&1'),
    # Restart nginx to fix proxy
    ('restart_nginx', 'docker compose -f /opt/smsly-hosting/docker-compose.prod.yml restart nginx 2>&1'),
    ('wait', 'sleep 5'),
    # Test via nginx
    ('test_nginx', 'curl -s -o /dev/null -w "%{http_code}" http://localhost:8090/ 2>&1'),
    # Test backend directly
    ('test_backend', 'curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ 2>&1'),
    # Backend logs (last 5 lines)
    ('backend_logs', 'docker compose -f /opt/smsly-hosting/docker-compose.prod.yml logs --tail=5 backend 2>&1 | tail -5'),
]

results = []
for label, cmd in cmds:
    _, out, err = c.exec_command(cmd, timeout=30)
    result = out.read().decode().strip()
    results.append(f"[{label}]: {result[:500]}")
c.close()

with open(r'C:\Users\osaretin\Downloads\smslycloud-master\smsly-hosting\fix_249.txt', 'w', encoding='utf-8') as f:
    f.write("\n".join(results))
print("Done")
