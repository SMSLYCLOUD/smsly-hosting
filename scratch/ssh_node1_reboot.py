import paramiko
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect('69.164.244.51', username='root', password='agbonsalo', timeout=15)

print("=== Rebooting Node 1 ===")
stdin, stdout, stderr = client.exec_command('sudo reboot')
# reboot closes the connection immediately, so we don't expect a lot of output.
try:
    print(stdout.read().decode('utf-8'))
except Exception as e:
    print(f"Connection closed as expected: {e}")

client.close()
