"""Check why Traefik isn't routing - network and provider issues."""
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

# 1. Check Traefik logs for container discovery issues
run('docker logs --tail=100 smsly-hosting-traefik-1 2>&1', "TRAEFIK LOGS (last 100)")

# 2. Check if Traefik can reach socket-proxy
run("docker exec smsly-hosting-traefik-1 wget -q -O - http://socket-proxy:2375/containers/json 2>&1 | python3 -c \"import sys,json; data=json.load(sys.stdin); [print(f'{c[\\\"Names\\\"][0]}: labels={list(k for k in c.get(\\\"Labels\\\",{}) if k.startswith(\\\"traefik\\\"))}') for c in data]\" 2>/dev/null | head -30 || echo 'Socket proxy query failed'", "TRAEFIK -> SOCKET-PROXY CONTAINER DISCOVERY")

# 3. Check what networks each user container is on vs what Traefik expects
run("echo '--- Traefik configured network: ---' && docker inspect smsly-hosting-traefik-1 --format '{{range .Config.Cmd}}{{.}} {{end}}' 2>/dev/null | tr ' ' '\n' | grep network", "TRAEFIK DOCKER NETWORK CONFIG")

# 4. Verify ALL containers are on smsly-net
for c in ['smsly-frontend-demo', 'smsly-frontend-node', 'smsly-frontend', 'smsly-hosting-traefik-1']:
    run(f"docker inspect {c} --format '{{{{range $k, $v := .NetworkSettings.Networks}}}}{{{{$k}}}} {{{{end}}}}' 2>/dev/null || echo '{c} not found'", f"NETWORKS OF {c}")

# 5. Test with the ACTUAL domains from the labels
run("curl -s -o /dev/null -w '%{http_code}' -H 'Host: smsly-frontend-0b774a.grid.smsly.cloud' http://127.0.0.1:80/ 2>/dev/null", "HOST TEST: smsly-frontend-0b774a.grid.smsly.cloud")
run("curl -s -o /dev/null -w '%{http_code}' -H 'Host: smsly-frontend-demo-fec7b7.grid.smsly.cloud' http://127.0.0.1:80/ 2>/dev/null", "HOST TEST: smsly-frontend-demo-fec7b7.grid.smsly.cloud")
run("curl -s -o /dev/null -w '%{http_code}' -H 'Host: smsly-frontend-node-81ffed.grid.smsly.cloud' http://127.0.0.1:80/ 2>/dev/null", "HOST TEST: smsly-frontend-node-81ffed.grid.smsly.cloud")

# 6. Check the smsly-proxy network (Traefik is on it per diagnostics)
run("docker network inspect smsly-proxy --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null || echo 'smsly-proxy not found'", "SMSLY-PROXY MEMBERS")

# 7. Check if containers have the right docker.network label
for c in ['smsly-frontend-demo', 'smsly-frontend-node', 'smsly-frontend']:
    run(f"docker inspect {c} --format '{{{{index .Config.Labels \"traefik.docker.network\"}}}}' 2>/dev/null || echo 'no label'", f"TRAEFIK DOCKER NETWORK LABEL: {c}")

# 8. Check Traefik entrypoint config from inside container
run("docker exec smsly-hosting-traefik-1 traefik healthcheck 2>&1 || echo 'healthcheck cmd not available'", "TRAEFIK HEALTHCHECK")

client.close()
print("\n\n=== ROUTING DIAGNOSIS COMPLETE ===")
