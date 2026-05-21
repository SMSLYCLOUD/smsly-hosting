import paramiko
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('209.159.152.123', username='root', password='agbonsalo', timeout=15)

print("=== Contents of /opt/smsly-hosting/.env on Master ===")
stdin, stdout, stderr = client.exec_command('cat /opt/smsly-hosting/.env')
print(stdout.read().decode('utf-8'))

client.close()
