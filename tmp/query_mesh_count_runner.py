import paramiko

HOST = "163.245.216.249"
USER = "root"
PW = "agbonsalo"

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(HOST, username=USER, password=PW, timeout=20, banner_timeout=20, auth_timeout=20)
    cmd = (
        "docker exec smsly-hosting-backend-1 "
        "python manage.py shell -c "
        "\"from apps.deployments.models_mesh import MeshNetwork; print(MeshNetwork.objects.count())\""
    )
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=20)
    print(stdout.read().decode())
    print(stderr.read().decode())
    ssh.close()

if __name__ == '__main__':
    main()
