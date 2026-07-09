import os

import paramiko

VPS_PASSWORD = os.environ['VPS_PASSWORD']  # SECURITY: Never hardcode passwords

# ===== .62 VPS — Final verification =====
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('163.245.214.62', username='root', password=VPS_PASSWORD, timeout=10)

_, out, _ = c.exec_command(r"""
echo "=== SMSLY-MARKETER ==="
docker ps --filter name=SMSLY-MARKETER --format "{{.Names}}: {{.Status}}"
echo ""

echo "=== TRAEFIK HEALTH LOGS (latest) ==="
docker logs smsly-hosting-traefik-1 --since 60s 2>&1 | grep -i health | tail -5
echo ""

echo "=== VIA TRAEFIK ==="
curl -s -o /dev/null -w "HTTP %{http_code}" -H "Host: smsly-marketer-77887a.pcloud.linadeluxe.com" http://127.0.0.1:80/ 2>&1
echo "  SMSLY-MARKETER"
curl -s -o /dev/null -w "HTTP %{http_code}" -H "Host: smsly-helper-dbc499.pcloud.linadeluxe.com" http://127.0.0.1:80/ 2>&1
echo "  smsly-helper"
echo ""

echo "=== MARKETER DIRECT ==="
MARKETER_IP=$(docker inspect SMSLY-MARKETER --format '{{range $k,$v := .NetworkSettings.Networks}}{{$v.IPAddress}}{{end}}' 2>/dev/null)
curl -s -o /dev/null -w "HTTP %{http_code}" -H "Host: localhost" http://$MARKETER_IP:8000/ 2>&1
echo "  (localhost host)"
curl -s -o /dev/null -w "HTTP %{http_code}" -H "Host: localhost" http://$MARKETER_IP:8000/health 2>&1
echo "  (/health localhost)"
curl -s -o /dev/null -w "HTTP %{http_code}" -H "Host: smsly-marketer-77887a.pcloud.linadeluxe.com" http://$MARKETER_IP:8000/ 2>&1
echo "  (domain host)"
""", timeout=15)
r = out.read().decode().strip()
c.close()

# ===== .249 VPS — Final verification =====
c2 = paramiko.SSHClient()
c2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c2.connect('163.245.216.249', username='root', password=VPS_PASSWORD, timeout=10)

_, out2, _ = c2.exec_command(r"""
echo ""
echo "=== .249 VPS BUYFORFRONT ==="
echo "=== VIA TRAEFIK ==="
curl -s -o /dev/null -w "HTTP %{http_code}" -H "Host: buyforfront-0398be.pcloud.distinctionlabs.org" http://127.0.0.1:80/ 2>&1
echo "  buyforfront (via Traefik)"
curl -s -o /dev/null -w "HTTP %{http_code}" -H "Host: buyforfront.com" http://127.0.0.1:80/ 2>&1
echo "  buyforfront.com (via Traefik)"
echo ""
echo "=== TRAEFIK HEALTH LOGS ==="
docker logs smsly-hosting-traefik-1 --since 120s 2>&1 | grep -i health | tail -5
echo ""
echo "=== LEGAL ENDPOINT TEST ==="
BF_IP=$(docker inspect buyforfront --format '{{range $k,$v := .NetworkSettings.Networks}}{{$v.IPAddress}}{{end}}' 2>/dev/null)
curl -s -o /dev/null -w "HTTP %{http_code}" -H "Host: buyforfront.com" "http://$BF_IP:8000/api/legal/agreements/active/" 2>&1
echo "  /api/legal/agreements/active/"
""", timeout=15)
r2 = out2.read().decode().strip()
c2.close()

full = r + "\n" + r2
with open(r'C:\Users\osaretin\Downloads\smslycloud-master\smsly-hosting\env_review.txt', 'w') as f:
    f.write(full)
print(f"Done - {len(full)} bytes")
