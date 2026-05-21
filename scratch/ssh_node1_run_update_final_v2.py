import paramiko
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('69.164.244.51', username='root', password='agbonsalo', timeout=15)

print("=== Cleaning up any stuck docker builds / update processes on Node 1 ===")
# Kill old docker compose build / install.sh processes to ensure fresh lock release
client.exec_command('pkill -f "install.sh"; pkill -f "docker compose build"')

print("=== Discarding any local changes on Node 1 repository ===")
stdin, stdout, stderr = client.exec_command('cd /opt/smsly-hosting && git checkout install.sh && git checkout infrastructure/docker/docker-compose.agent-lite.yml && git pull origin main')
print(stdout.read().decode('utf-8'))
print(stderr.read().decode('utf-8'))

print("=== Running ./install.sh --update ===")
stdin, stdout, stderr = client.exec_command('cd /opt/smsly-hosting && sudo ./install.sh --update 2>&1')
for line in iter(stdout.readline, ""):
    print(line, end="")

client.close()
