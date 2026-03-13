import paramiko
user='root'
pw='agbonsalo'
replica='163.245.216.248'
cmds=[
    "docker rm -f pg-replica >/dev/null 2>&1 || true",
    "docker volume rm pgdata-replica >/dev/null 2>&1 || true",
    "docker volume create pgdata-replica >/dev/null",
    "docker run --rm --name pg-replica-bootstrap -e PGPASSWORD=replpass -v pgdata-replica:/var/lib/postgresql/data pgvector/pgvector:pg16 pg_basebackup -h 10.10.0.1 -U replicator -D /var/lib/postgresql/data -Fp -Xs -P -R",
    "docker run -d --name pg-replica --restart unless-stopped -v pgdata-replica:/var/lib/postgresql/data -p 5432:5432 pgvector/pgvector:pg16 -c hot_standby=on",
]

c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy()); c.connect(replica, username=user, password=pw, timeout=10, banner_timeout=10, auth_timeout=10)
for cmd in cmds:
    stdin,stdout,stderr=c.exec_command(cmd, timeout=90)
    rc=stdout.channel.recv_exit_status(); out=stdout.read().decode(); err=stderr.read().decode()
    print(cmd.split()[0], 'rc', rc, 'out', out.strip(), 'err', err.strip())
c.close()
