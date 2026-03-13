import paramiko
user='root'
pw='agbonsalo'
primary='163.245.216.249'
cmds=[
    "docker rm -f pg-primary >/dev/null 2>&1 || true",
    "docker run -d --name pg-primary --restart unless-stopped -e POSTGRES_PASSWORD=pgpass -e POSTGRES_DB=app_db -p 5432:5432 pgvector/pgvector:pg16 -c wal_level=replica -c max_wal_senders=10 -c max_replication_slots=10",
    "docker exec pg-primary psql -U postgres -c \"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='replicator') THEN CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replpass'; END IF; END $$;\"",
    "docker exec pg-primary bash -c 'echo \"host replication replicator 10.10.0.2/32 md5\" >> /var/lib/postgresql/data/pg_hba.conf && echo \"host all all 10.10.0.0/24 md5\" >> /var/lib/postgresql/data/pg_hba.conf && pg_ctl -D /var/lib/postgresql/data reload'",
]

c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy()); c.connect(primary, username=user, password=pw, timeout=10, banner_timeout=10, auth_timeout=10)
for cmd in cmds:
    stdin,stdout,stderr=c.exec_command(cmd, timeout=60)
    rc=stdout.channel.recv_exit_status(); out=stdout.read().decode(); err=stderr.read().decode()
    print(cmd.split()[0], 'rc', rc, 'out', out.strip(), 'err', err.strip())
c.close()
