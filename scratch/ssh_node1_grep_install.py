import paramiko
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('69.164.244.51', username='root', password='agbonsalo', timeout=15)

print("=== Checking Nginx check inside remote install.sh ===")
stdin, stdout, stderr = client.exec_command('grep -n -C 5 "NGINX_CONFIG_CHECK" /opt/smsly-hosting/install.sh')
print(stdout.read().decode('utf-8'))

client.close()
