import paramiko
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    c.connect('163.245.214.62', username='root', password='agbonsalo', timeout=10)
    print("Connected. Restarting SMSLY-MARKETER...")
    
    _, stdout, _ = c.exec_command('docker restart SMSLY-MARKETER')
    print(stdout.read().decode('utf-8', errors='replace'))
    
    time.sleep(5)
    
    print("\n--- SMSLY-MARKETER logs after restart ---")
    _, stdout, _ = c.exec_command('docker logs SMSLY-MARKETER --tail 15 2>&1')
    print(stdout.read().decode('utf-8', errors='replace'))

except Exception as e:
    print(f"Connection failed: {e}")
finally:
    c.close()
