import paramiko
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('69.164.244.51', username='root', password='agbonsalo', timeout=15)

print("=== Killing any lingering update / docker compose builds on Node 1 ===")
client.exec_command('pkill -9 -f "install.sh"; pkill -9 -f "docker compose"')

print("=== Git Pulling on Node 1 ===")
stdin, stdout, stderr = client.exec_command('cd /opt/smsly-hosting && git checkout install.sh && git checkout infrastructure/docker/docker-compose.agent-lite.yml && git pull origin main')
print(stdout.read().decode('utf-8'))
print(stderr.read().decode('utf-8'))

print("=== Running ./install.sh --update ===")
stdin, stdout, stderr = client.exec_command('cd /opt/smsly-hosting && sudo ./install.sh --update 2>&1')
for line in iter(stdout.readline, ""):
    print(line, end="")

client.close()
