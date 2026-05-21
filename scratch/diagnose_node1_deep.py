"""Deep diagnosis - Traefik config, container labels, and networking."""
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
run('docker inspect smsly-hosting-traefik-1 --format "{{json .Config.Cmd}}"', "TRAEFIK CMD")
run('docker inspect smsly-hosting-traefik-1 --format "{{json .Config.Labels}}"', "TRAEFIK LABELS")
run('docker inspect smsly-hosting-traefik-1 --format "{{json .Mounts}}" | python3 -m json.tool 2>/dev/null || docker inspect smsly-hosting-traefik-1 --format "{{json .Mounts}}"', "TRAEFIK MOUNTS")
run('docker logs --tail=50 smsly-hosting-traefik-1 2>&1', "TRAEFIK LOGS (last 50)")

# 2. Traefik dynamic config files
run('docker exec smsly-hosting-traefik-1 cat /etc/traefik/traefik.yml 2>/dev/null || echo "No traefik.yml"', "TRAEFIK STATIC CONFIG")
run('docker exec smsly-hosting-traefik-1 ls -la /etc/traefik/ 2>/dev/null', "TRAEFIK CONFIG DIR")
run('docker exec smsly-hosting-traefik-1 ls -la /etc/traefik/dynamic/ 2>/dev/null || echo "No dynamic dir"', "TRAEFIK DYNAMIC DIR")
run('docker exec smsly-hosting-traefik-1 cat /etc/traefik/dynamic/*.yml 2>/dev/null || docker exec smsly-hosting-traefik-1 cat /etc/traefik/dynamic/*.yaml 2>/dev/null || echo "No dynamic configs"', "TRAEFIK DYNAMIC CONFIGS")

# 3. Check Traefik API for routers and services
run('curl -s http://localhost:8080/api/rawdata 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); routers=d.get(\"routers\",{}); [print(f\"{k}: {v.get(\"rule\",\"?\")}\") for k,v in routers.items()]" 2>/dev/null || echo "Traefik API not available on 8080"', "TRAEFIK ROUTERS")
run('curl -s http://localhost:8080/api/http/routers 2>/dev/null | python3 -c "import sys,json; [print(f\"{r[\"name\"]}: {r.get(\"rule\",\"?\")} -> {r.get(\"service\",\"?\")}\") for r in json.load(sys.stdin)]" 2>/dev/null || echo "Traefik router API failed"', "TRAEFIK HTTP ROUTERS")
run('curl -s http://localhost:8080/api/http/services 2>/dev/null | python3 -c "import sys,json; [print(f\"{s[\"name\"]}: {json.dumps(s.get(\"loadBalancer\",{}))}\") for s in json.load(sys.stdin)]" 2>/dev/null || echo "Traefik service API failed"', "TRAEFIK HTTP SERVICES")

# 4. User container labels (Traefik routing is via labels)
for cname in ['smsly-frontend-demo', 'smsly-frontend-node', 'smsly-frontend']:
    run(f'docker inspect {cname} --format "{{{{json .Config.Labels}}}}" 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "Container {cname} not found"', f"LABELS: {cname}")

# 5. Check container networks
for cname in ['smsly-frontend-demo', 'smsly-frontend-node', 'smsly-frontend', 'smsly-hosting-traefik-1']:
    run(f'docker inspect {cname} --format "{{{{json .NetworkSettings.Networks}}}}" 2>/dev/null | python3 -c "import sys,json; nets=json.load(sys.stdin); [print(f\"  {{k}}: {{v.get(\"IPAddress\",\"?\")}} gateway={{v.get(\"Gateway\",\"?\")}}\") for k,v in nets.items()]" 2>/dev/null || echo "{cname} not found"', f"NETWORKS: {cname}")

# 6. Check docker-compose file on the node
run('cat /opt/smsly-hosting/docker-compose.prod.yml 2>/dev/null | head -100 || cat /root/smsly-hosting/docker-compose.prod.yml 2>/dev/null | head -100 || echo "No compose file found at expected paths"', "DOCKER COMPOSE (first 100 lines)")

# 7. Where is the smsly-hosting installed?
run('find / -name "docker-compose.prod.yml" -path "*/smsly*" 2>/dev/null | head -5', "SMSLY INSTALL LOCATION")

# 8. Check if backend can reach user containers
run('docker exec smsly-hosting-backend-1 curl -s -o /dev/null -w "%{http_code}" http://smsly-frontend:3000/ 2>/dev/null || echo "Backend cant reach smsly-frontend"', "BACKEND -> smsly-frontend connectivity")

# 9. Check smsly-proxy network 
run('docker network inspect smsly-proxy 2>/dev/null | python3 -c "import sys,json; data=json.load(sys.stdin); containers=data[0].get(\"Containers\",{}); [print(f\"  {v[\"Name\"]}: {v[\"IPv4Address\"]}\") for v in containers.values()]" 2>/dev/null || echo "smsly-proxy network not found"', "SMSLY-PROXY NETWORK MEMBERS")

# 10. Check smsly-net network
run('docker network inspect smsly-net 2>/dev/null | python3 -c "import sys,json; data=json.load(sys.stdin); containers=data[0].get(\"Containers\",{}); [print(f\"  {v[\"Name\"]}: {v[\"IPv4Address\"]}\") for v in containers.values()]" 2>/dev/null || echo "smsly-net network not found"', "SMSLY-NET NETWORK MEMBERS")

# 11. Try to reach service containers from Traefik
run('docker exec smsly-hosting-traefik-1 wget -q -O /dev/null -S http://smsly-frontend:3000/ 2>&1 | head -5 || echo "Traefik cant reach smsly-frontend"', "TRAEFIK -> smsly-frontend")
run('docker exec smsly-hosting-traefik-1 wget -q -O /dev/null -S http://smsly-frontend-demo:3000/ 2>&1 | head -5 || echo "Traefik cant reach smsly-frontend-demo"', "TRAEFIK -> smsly-frontend-demo")

# 12. Check the caddy-config dir permissions on host
run('ls -la /opt/smsly-hosting/caddy-config/ 2>/dev/null || ls -la /caddy-config/ 2>/dev/null || echo "No caddy-config dir"', "CADDY-CONFIG DIR PERMS")

# 13. Find and check the database
run('find / -name "db.sqlite3" -path "*/smsly*" 2>/dev/null | head -5', "SQLITE DB LOCATIONS")
run('docker exec smsly-hosting-backend-1 python manage.py shell -c "from apps.deployments.models_core import Deployment; stuck=Deployment.objects.filter(status__in=[\"BUILDING\",\"QUEUED\",\"DEPLOYING\"]); print(f\"Stuck: {stuck.count()}\"); [print(f\"  {d.id} | {d.status} | service={d.service.name} | created={d.created_at}\") for d in stuck]" 2>&1', "STUCK DEPLOYMENTS (via Django)")

client.close()
print("\n\n=== DEEP DIAGNOSIS COMPLETE ===")
