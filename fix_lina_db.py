import paramiko
import sys

sys.stdout.reconfigure(encoding='utf-8')

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    c.connect('163.245.214.62', username='root', password='agbonsalo', timeout=10)
    print("Connected. Restarting lina-deluxe container...")

    _, stdout, _ = c.exec_command('docker restart lina-deluxe')
    print(stdout.read().decode('utf-8', errors='replace'))

    print("\nWaiting 5 seconds for container to start...")
    import time
    time.sleep(5)

    print("\n--- Checking lina-deluxe logs ---")
    _, stdout, _ = c.exec_command('docker logs lina-deluxe --tail 30 2>&1')
    print(stdout.read().decode('utf-8', errors='replace'))

except Exception as e:
    print(f"Connection failed: {e}")
finally:
    c.close()
