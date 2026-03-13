import paramiko, json
host='163.245.216.249'
user='root'
pw='agbonsalo'
cmd="docker exec smsly-hosting-backend-1 python manage.py shell -c \"from apps.deployments.models import Service; import json; print(json.dumps([(str(s.id), s.name, s.public_domain) for s in Service.objects.all() if 'buyforfront' in (s.public_domain or '')], indent=2))\""

c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy()); c.connect(host, username=user, password=pw, timeout=10)
stdin,stdout,stderr=c.exec_command(cmd, timeout=60)
print(stdout.read().decode())
print(stderr.read().decode())
c.close()
