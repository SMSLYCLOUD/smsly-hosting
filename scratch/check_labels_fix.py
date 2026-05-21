"""Check container Traefik labels and fix stuck deployments on Node 1."""
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

# 1. Get ALL labels from user containers (JSON format, parsed nicely)
for cname in ['smsly-frontend-demo', 'smsly-frontend-node', 'smsly-frontend']:
    run(f"docker inspect {cname} --format '{{{{json .Config.Labels}}}}' 2>/dev/null | python3 -m json.tool 2>/dev/null || echo 'Container {cname} not found'", f"LABELS: {cname}")

# 2. Enable Traefik API temporarily to check routers
run("curl -s http://127.0.0.1:8082/api/http/routers 2>/dev/null | python3 -m json.tool 2>/dev/null | head -100 || echo 'Traefik API not reachable on 8082'", "TRAEFIK API ROUTERS (8082)")
run("curl -s http://127.0.0.1:8081/api/http/routers 2>/dev/null | python3 -m json.tool 2>/dev/null | head -100 || echo 'Traefik API not reachable on 8081'", "TRAEFIK API ROUTERS (8081)")

# 3. Try to access services via Traefik with Host header
for domain in ['smsly-frontend-demok-dfcbc2.grid.smsly.cloud', 'smsly-frontend-demo.grid.smsly.cloud']:
    run(f"curl -s -o /dev/null -w '%{{http_code}}' -H 'Host: {domain}' http://127.0.0.1:80/ 2>/dev/null || echo 'failed'", f"TRAEFIK HOST TEST: {domain}")

# 4. Check what host rules are on user containers
for cname in ['smsly-frontend-demo', 'smsly-frontend-node', 'smsly-frontend']:
    run(f"docker inspect {cname} --format '{{{{index .Config.Labels \"traefik.enable\"}}}} | {{{{range $k,$v := .Config.Labels}}}}{{{{if eq (printf \"%.27s\" $k) \"traefik.http.routers.\"}}}}{{{{$k}}}}={{{{$v}}}}; {{{{end}}}}{{{{end}}}}' 2>/dev/null || echo '{cname} not found'", f"TRAEFIK RULES: {cname}")

# 5. Check env vars on the backend (SMSLY_NODE_MODE, IS_LITE_AGENT, etc)
run("docker exec smsly-hosting-backend-1 env 2>/dev/null | grep -iE 'lite|agent|node_mode|caddy|master|is_primary|smsly_enable' | sort", "BACKEND ENV (node mode)")

# 6. Check the .env file on the node
run("cat /opt/smsly-hosting/.env 2>/dev/null | grep -iE 'lite|agent|node_mode|caddy|master|is_primary|domain|server_ip|traefik|smsly_enable' | sort", "NODE .ENV (mode vars)")

# 7. Cancel stuck deployments via Django manage.py
run('''cat > /tmp/fix_stuck.py << 'PYEOF'
import os, sys
os.chdir("/app")
sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from apps.deployments.models_core import Deployment
from django.utils import timezone

stuck = Deployment.objects.filter(status__in=["BUILDING", "QUEUED", "DEPLOYING"])
count = stuck.count()
print(f"Found {count} stuck deployment(s)")
for d in stuck:
    print(f"  Cancelling: {d.id} | {d.status} | service={d.service.name} | created={d.created_at}")
    d.status = "CANCELLED"
    d.finished_at = timezone.now()
    d.build_logs = (d.build_logs or "") + "\\n[System] Auto-cancelled stuck deployment."
    d.save(update_fields=["status", "finished_at", "build_logs", "updated_at"])
print(f"Cancelled {count} stuck deployment(s)")
PYEOF
docker cp /tmp/fix_stuck.py smsly-hosting-backend-1:/tmp/fix_stuck.py
docker exec smsly-hosting-backend-1 python /tmp/fix_stuck.py 2>&1''', "CANCEL STUCK DEPLOYMENTS")

# 8. Check WireGuard status on node
run("wg show 2>/dev/null || echo 'WireGuard not active'", "WIREGUARD STATUS")

# 9. List ALL containers with their smsly labels
run("docker ps --format '{{.Names}}' | while read c; do echo \"--- $c ---\"; docker inspect $c --format '{{range $k,$v := .Config.Labels}}{{if or (eq (printf \"%.7s\" $k) \"traefik\") (eq (printf \"%.5s\" $k) \"smsly.\")}}  {{$k}}={{$v}}\n{{end}}{{end}}' 2>/dev/null; done", "ALL CONTAINER TRAEFIK+SMSLY LABELS")

client.close()
print("\n\n=== LABEL CHECK + FIX COMPLETE ===")
