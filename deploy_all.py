import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('163.245.216.248', username='root', password='agbonsalo', timeout=10)

_, out, _ = c.exec_command("""
docker compose -f /opt/smsly-hosting/docker-compose.prod.yml ps --format "{{.Name}} {{.Status}}" 2>&1
""", timeout=30)
result = out.read().decode().strip()
c.close()
print(result)
