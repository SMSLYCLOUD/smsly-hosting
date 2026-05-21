import paramiko
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('69.164.244.51', username='root', password='agbonsalo', timeout=15)

stdin, stdout, stderr = client.exec_command('docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}"')
print("=== docker ps ===")
print(stdout.read().decode('utf-8'))

print("=== docker logs backend-1 ===")
stdin, stdout, stderr = client.exec_command('docker logs --tail=50 smsly-hosting-backend-1')
print(stdout.read().decode('utf-8'))
print(stderr.read().decode('utf-8'))

print("=== docker logs celery-worker-1 ===")
stdin, stdout, stderr = client.exec_command('docker logs --tail=50 smsly-hosting-celery-worker-1')
print(stdout.read().decode('utf-8'))
print(stderr.read().decode('utf-8'))

client.close()
