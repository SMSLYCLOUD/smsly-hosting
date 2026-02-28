import paramiko
import sys

sys.stdout.reconfigure(encoding='utf-8')

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    c.connect('163.245.214.62', username='root', password='agbonsalo', timeout=10)
    print("Connected to 163.245.214.62")

    # List ALL containers
    print("\n=== ALL running containers ===")
    _, stdout, _ = c.exec_command('docker ps --format "{{.Names}}\t{{.Status}}" | sort')
    print(stdout.read().decode('utf-8', errors='replace'))

    # Specifically look for tailor
    print("\n=== Tailor containers (including stopped) ===")
    _, stdout, _ = c.exec_command('docker ps -a --format "{{.Names}}\t{{.Status}}" | grep -i tailor')
    tailor_out = stdout.read().decode('utf-8', errors='replace').strip()
    print(tailor_out if tailor_out else "NONE FOUND")

    # Check if there IS a tailor app container and its DATABASE_URL
    if tailor_out:
        for line in tailor_out.split('\n'):
            cname = line.split('\t')[0].strip()
            if cname:
                _, stdout, _ = c.exec_command(f'docker exec {cname} env 2>/dev/null | grep -iE "DATABASE|POSTGRES"')
                env_out = stdout.read().decode('utf-8', errors='replace').strip()
                if env_out:
                    print(f"\n{cname} DB env:")
                    print(env_out)

except Exception as e:
    print(f"Connection failed: {e}")
finally:
    c.close()
