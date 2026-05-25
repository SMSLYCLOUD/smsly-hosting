import paramiko
import sys

def main():
    ip = "209.159.152.123"
    user = "root"
    password = "agbonsalo"
    
    print(f"Connecting to {ip} as {user}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        client.connect(hostname=ip, port=22, username=user, password=password, timeout=15)
        print("Connected successfully!")
    except Exception as e:
        print(f"Failed to connect: {e}")
        sys.exit(1)

    path = "/opt/smsly-hosting"

    django_code = """
import django
django.setup()
from apps.deployments.models import Deployment

d = Deployment.objects.get(id="426f6304-f417-4f62-8a76-bb33be33f53d")
print("Deployment Status:", d.status)
print("Build Logs:")
print(d.build_logs)
"""

    print("Running Django shell query inside backend container...")
    cmd = f"cd {path} && docker compose exec -T backend python manage.py shell"
    stdin, stdout, stderr = client.exec_command(cmd)
    
    stdin.write(django_code)
    stdin.close()
    
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    
    print("STDOUT:")
    print(out)
    if err:
        print("STDERR:")
        print(err)

if __name__ == "__main__":
    main()
