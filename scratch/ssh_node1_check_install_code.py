import paramiko
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('69.164.244.51', username='root', password='agbonsalo', timeout=15)

stdin, stdout, stderr = client.exec_command('git log -n 1')
print("=== Latest Git Commit on Node 1 ===")
print(stdout.read().decode('utf-8'))

stdin, stdout, stderr = client.exec_command('grep -n -C 5 "set -a" /opt/smsly-hosting/install.sh')
print("=== set -a matches ===")
print(stdout.read().decode('utf-8'))

client.close()
