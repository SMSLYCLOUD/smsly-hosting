import paramiko, time
user='root'
pw='agbonsalo'
primary='163.245.216.249'
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy()); c.connect(primary, username=user, password=pw, timeout=10, banner_timeout=10, auth_timeout=10)
cmds=[
    "docker exec pg-primary sleep 3",
    "docker exec pg-primary bash -c 'echo \"host replication replicator 10.10.0.2/32 md5\" >> /var/lib/postgresql/data/pg_hba.conf'",
    "docker exec pg-primary bash -c 'echo \"host all all 10.10.0.0/24 md5\" >> /var/lib/postgresql/data/pg_hba.conf'",
    "docker exec pg-primary psql -U postgres -c \"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='replicator') THEN CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replpass'; END IF; END $$;\"",
    "docker exec pg-primary psql -U postgres -c 'select pg_reload_conf();'",
]
for cmd in cmds:
    stdin,stdout,stderr=c.exec_command(cmd, timeout=30)
    rc=stdout.channel.recv_exit_status(); out=stdout.read().decode(); err=stderr.read().decode()
    print(cmd.split()[0], 'rc', rc, 'out', out.strip(), 'err', err.strip())

c.close()
