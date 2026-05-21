"""Quick focused diagnostics - Traefik routers, Caddy status, stuck deployments."""
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

# 1. Traefik config
run('docker exec smsly-hosting-traefik-1 cat /etc/traefik/traefik.yml 2>/dev/null || echo "No traefik.yml"', "TRAEFIK STATIC CONFIG")

# 2. Traefik API - routers
run('curl -s http://localhost:8080/api/http/routers 2>/dev/null | python3 -c "import sys,json; data=json.load(sys.stdin); [print(f\'{r[\"name\"]}: rule={r.get(\"rule\",\"?\")[:80]} service={r.get(\"service\",\"?\")} status={r.get(\"status\",\"?\")}\') for r in data]" 2>/dev/null || echo "Traefik API unavailable"', "TRAEFIK ROUTERS")

# 3. Traefik API - services
run('curl -s http://localhost:8080/api/http/services 2>/dev/null | python3 -c "import sys,json; data=json.load(sys.stdin); [print(f\'{s[\"name\"]}: {s.get(\"type\",\"?\")} servers={[sv.get(\"url\") for sv in s.get(\"loadBalancer\",{}).get(\"servers\",[])]} status={s.get(\"status\",\"?\")}\') for s in data]" 2>/dev/null || echo "Traefik services API unavailable"', "TRAEFIK SERVICES")

# 4. Check Caddy container specifically
run('docker ps -a --filter "name=caddy" --format "table {{.Names}}\\t{{.Status}}\\t{{.Ports}}"', "CADDY CONTAINERS (all)")

# 5. Check what the compose file says about Caddy
run('grep -A 30 "caddy:" /opt/smsly-hosting/docker-compose.prod.yml 2>/dev/null | head -35', "COMPOSE CADDY DEF")

# 6. Stuck deployments - use a script file to avoid quoting issues
run('''cat > /tmp/check_stuck.py << 'PYEOF'
import os, django
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
os.chdir("/app")
django.setup()
from apps.deployments.models_core import Deployment
stuck = Deployment.objects.filter(status__in=["BUILDING", "QUEUED", "DEPLOYING"])
print(f"Stuck deployments: {stuck.count()}")
for d in stuck:
    print(f"  {d.id} | {d.status} | service={d.service.name} | created={d.created_at}")
PYEOF
docker cp /tmp/check_stuck.py smsly-hosting-backend-1:/tmp/check_stuck.py
docker exec smsly-hosting-backend-1 python /tmp/check_stuck.py 2>&1''', "STUCK DEPLOYMENTS")

# 7. Check docker-compose ps
run('cd /opt/smsly-hosting && docker compose -f docker-compose.prod.yml ps 2>/dev/null || docker-compose -f docker-compose.prod.yml ps 2>/dev/null', "DOCKER COMPOSE PS")

# 8. Check if caddy is defined in compose but not started
run('cd /opt/smsly-hosting && docker compose -f docker-compose.prod.yml config --services 2>/dev/null | sort', "ALL COMPOSE SERVICES")

# 9. Check Traefik container networks
run("docker inspect smsly-hosting-traefik-1 --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}: {{$v.IPAddress}}{{\"\\n\"}}{{end}}' 2>/dev/null", "TRAEFIK NETWORKS")

# 10. Check user container networks
run("docker inspect smsly-frontend --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}: {{$v.IPAddress}}{{\"\\n\"}}{{end}}' 2>/dev/null", "smsly-frontend NETWORKS")

# 11. Check Traefik entrypoints
run("docker inspect smsly-hosting-traefik-1 --format '{{json .Config.Cmd}}' 2>/dev/null", "TRAEFIK CMD/ARGS")

# 12. Check the Caddyfile content on node
run('cat /opt/smsly-hosting/caddy-config/Caddyfile 2>/dev/null', "CADDYFILE CONTENT")

# 13. Check if port 443 is bound
run('ss -tlnp | grep -E ":80|:443|:8080|:8081" 2>/dev/null || netstat -tlnp | grep -E ":80|:443|:8080|:8081" 2>/dev/null', "LISTENING PORTS (80/443/8080/8081)")

client.close()
print("\n\n=== FOCUSED DIAGNOSIS COMPLETE ===")
