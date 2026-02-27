import paramiko

# === Fix .248 migration issue ===
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('163.245.216.248', username='root', password='agbonsalo', timeout=10)

fix_cmd = """
cd /opt/smsly-hosting

# Fix the migration history: fake-apply the missing dependency
echo ">>> Fixing migration history"
docker compose -f docker-compose.prod.yml exec -T backend python manage.py migrate deployments 0028 --fake 2>&1 || echo "already applied or error"

# Run all pending migrations
echo ">>> Running all migrations"  
docker compose -f docker-compose.prod.yml exec -T backend python manage.py migrate --run-syncdb 2>&1 | tail -10

# Restart backend + celery
echo ">>> Restarting services"
docker compose -f docker-compose.prod.yml up -d --force-recreate backend 2>&1
sleep 30

# Start celery (depends on healthy backend)
docker compose -f docker-compose.prod.yml up -d celery celery-beat 2>&1
sleep 10

# Verify
echo ">>> Verify"
docker compose -f docker-compose.prod.yml ps --format "{{.Name}} {{.Status}}" 2>&1
"""
_, out, err = c.exec_command(fix_cmd, timeout=300)
result = out.read().decode().strip()
errors = err.read().decode().strip()
c.close()

# === Check .249 frontend issue ===
c2 = paramiko.SSHClient()
c2.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c2.connect('163.245.216.249', username='root', password='agbonsalo', timeout=10)

check_cmd = """
# Check nginx -> backend proxy
curl -s -o /dev/null -w "%{http_code}" http://localhost:8090/ 2>&1
echo ""
curl -s -o /dev/null -w "%{http_code}" http://localhost:8090/api/v1/licensing/status/ 2>&1
echo ""
# Check if backend is responding directly
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ 2>&1
echo ""
docker compose -f /opt/smsly-hosting/docker-compose.prod.yml ps backend --format "{{.Name}} {{.Status}}" 2>&1
"""
_, out2, _ = c2.exec_command(check_cmd, timeout=30)
result2 = out2.read().decode().strip()
c2.close()

with open(r'C:\Users\osaretin\Downloads\smslycloud-master\smsly-hosting\fix_final.txt', 'w', encoding='utf-8') as f:
    f.write(f"=== .248 FIX ===\n{result[:2000]}\n\nSTDERR:\n{errors[:500]}\n\n=== .249 CHECK ===\n{result2}")
print("Done")
