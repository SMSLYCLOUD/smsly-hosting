import paramiko
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('69.164.244.51', username='root', password='agbonsalo', timeout=15)

print("=== Killing wrong build processes on Node 1 ===")
# Kill install.sh and docker compose build processes
client.exec_command('sudo pkill -f "install.sh" || true')
client.exec_command('sudo pkill -f "docker compose" || true')
client.exec_command('sudo pkill -f "docker-compose" || true')
client.exec_command('sudo rm -f /tmp/smsly-install.lock || true')

print("=== Running Processes on Node 1 ===")
stdin, stdout, stderr = client.exec_command('ps aux | grep -E "install.sh|docker compose"')
print(stdout.read().decode('utf-8'))

client.close()
