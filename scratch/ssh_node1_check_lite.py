import paramiko
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('69.164.244.51', username='root', password='agbonsalo', timeout=15)

print("=== Contents of /opt/smsly-hosting/.env ===")
stdin, stdout, stderr = client.exec_command('cat /opt/smsly-hosting/.env')
print(stdout.read().decode('utf-8'))

print("=== Checking if early parser matches ===")
stdin, stdout, stderr = client.exec_command('''
if [ -f "/opt/smsly-hosting/.env" ]; then
    _ENV_NODE_TYPE="$(grep -m1 '^NODE_TYPE=' /opt/smsly-hosting/.env | cut -d= -f2- | tr -d '"'\\'' ' || true)"
    echo "Parsed NODE_TYPE: $_ENV_NODE_TYPE"
else
    echo ".env not found"
fi
''')
print(stdout.read().decode('utf-8'))

client.close()
