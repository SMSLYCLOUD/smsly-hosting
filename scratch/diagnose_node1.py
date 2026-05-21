"""Diagnose Node 1 - Check for 502 root cause and stuck deployments."""
import paramiko
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('69.164.244.51', username='root', password='agbonsalo', timeout=15)

def run(cmd, label=""):
    if label:
        print(f"\n{'='*60}")
        print(f"=== {label} ===")
        print('='*60)
    stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    if out.strip():
        print(out)
    if err.strip():
        print(f"[STDERR] {err}")
    return out, err

# 1. Docker containers
run('docker ps -a --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"', "ALL DOCKER CONTAINERS")

# 2. Check Caddy status and config
run('docker ps --filter name=caddy --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"', "CADDY CONTAINER")
run('docker exec smsly-hosting-caddy-1 cat /etc/caddy/Caddyfile 2>/dev/null || cat /etc/caddy/Caddyfile 2>/dev/null || echo "No Caddyfile found"', "CADDY CONFIG (Caddyfile)")
run('docker exec smsly-hosting-caddy-1 caddy list-modules 2>/dev/null | head -5 || echo "caddy list-modules failed"', "CADDY MODULES")

# 3. Check Nginx (if used instead of/alongside Caddy)
run('systemctl status nginx 2>/dev/null | head -10 || echo "nginx not running as systemd service"', "NGINX STATUS")
run('docker ps --filter name=nginx --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"', "NGINX CONTAINER")

# 4. Check if there's a reverse proxy config that maps domains to containers
run('ls -la /etc/caddy/ 2>/dev/null || echo "No /etc/caddy dir"', "CADDY CONFIG DIR")
run('cat /etc/caddy/Caddyfile 2>/dev/null | head -100 || echo "No host-level Caddyfile"', "HOST CADDYFILE")

# 5. Check if services are actually running and listening
run('docker ps --filter "status=running" --format "{{.Names}}: {{.Ports}}" | grep -v "caddy\|nginx\|redis\|postgres\|backend\|celery\|registry\|flower\|beat"', "USER SERVICE CONTAINERS")

# 6. Check the SMSLY platform backend on the node
run('docker logs --tail=30 smsly-hosting-backend-1 2>&1', "NODE BACKEND LOGS (last 30)")

# 7. Check Caddy logs for 502 errors
run('docker logs --tail=50 smsly-hosting-caddy-1 2>&1 | grep -i "502\|error\|upstream\|dial" || echo "No 502/error in caddy logs"', "CADDY 502 ERRORS")

# 8. Check if the platform API is responding on the node
run('curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/v1/services/ 2>/dev/null || echo "API not reachable"', "NODE API HEALTH")

# 9. Check the stuck deployment
run('''python3 -c "
import sqlite3, json
import os
db_paths = [
    '/opt/smsly-hosting/backend/db.sqlite3',
    '/root/smsly-hosting/backend/db.sqlite3',
]
for p in db_paths:
    if os.path.exists(p):
        conn = sqlite3.connect(p)
        cur = conn.cursor()
        cur.execute('SELECT id, status, service_id, commit_hash, created_at FROM deployments_deployment WHERE status IN (\"BUILDING\", \"QUEUED\", \"DEPLOYING\") ORDER BY created_at DESC LIMIT 10')
        rows = cur.fetchall()
        print(f'DB: {p}')
        print(f'Active/stuck deployments: {len(rows)}')
        for r in rows:
            print(f'  ID={r[0]}, status={r[1]}, service={r[2]}, commit={r[3]}, created={r[4]}')
        conn.close()
        break
else:
    print('No db found')
" 2>&1''', "STUCK DEPLOYMENTS IN DB")

# 10. Check the docker network
run('docker network ls', "DOCKER NETWORKS")
run('docker network inspect smsly-hosting_smsly-net 2>/dev/null | python3 -c "import sys,json; data=json.load(sys.stdin); containers=data[0].get(\"Containers\",{}); [print(f\'{v[\"Name\"]}: {v[\"IPv4Address\"]}\') for v in containers.values()]" 2>/dev/null || echo "Network not found or parse error"', "SMSLY NETWORK CONTAINERS")

# 11. Check what port services bind to
run('docker ps --format "{{.Names}}: {{.Ports}}" | sort', "ALL CONTAINER PORTS")

# 12. Check Caddy admin API for upstreams
run('curl -s http://localhost:2019/config/ 2>/dev/null | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))" 2>/dev/null | head -200 || echo "Caddy admin API not available"', "CADDY LIVE CONFIG (first 200 lines)")

client.close()
print("\n\n=== DIAGNOSIS COMPLETE ===")
