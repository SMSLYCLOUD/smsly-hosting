import paramiko
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('69.164.244.51', username='root', password='agbonsalo', timeout=15)

print("=== Nginx Config inside Container ===")
stdin, stdout, stderr = client.exec_command('docker exec smsly-hosting-nginx-1 nginx -T')
print(stdout.read().decode('utf-8'))
print(stderr.read().decode('utf-8'))

client.close()
