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
from apps.deployments.models import Deployment, Service

# Find service
svcs = Service.objects.filter(name__icontains="rate-limit")
for s in svcs:
    print("="*60)
    print("Service:", s.name, s.id)
    deps = Deployment.objects.filter(service=s).order_by('-created_at')
    print("Deployments count:", deps.count())
    for d in deps[:5]:
        print(f"  Deployment ID: {d.id} | Status: {d.status} | Created: {d.created_at}")
        print("  Logs tail:")
        logs = d.build_logs or ""
        print(logs[-2000:])
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
