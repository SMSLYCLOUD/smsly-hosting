#!/bin/bash
# Render patroni.yml from env (same vars the compose file sets for Spilo)
# and exec Patroni. Runs as root; Patroni drops privileges via pg_ctl as
# the data-dir owner (postgres).
set -euo pipefail

: "${SCOPE:=smsly-cluster}"
: "${ETCD3_HOST:=etcd:2379}"
: "${PATRONI_NAME:=$(hostname)}"
: "${PGUSER_SUPERUSER:=postgres}"
: "${PGPASSWORD_SUPERUSER:?PGPASSWORD_SUPERUSER required}"
: "${PGUSER_ADMIN:=smsly_admin}"
: "${PGPASSWORD_ADMIN:?PGPASSWORD_ADMIN required}"
: "${PGPASSWORD_STANDBY:?PGPASSWORD_STANDBY required}"
POD_IP="${POD_IP:-$(hostname -i | awk '{print $1}')}"

export PGDATA=/var/lib/postgresql/data/patroni

cat > /etc/patroni.yml <<EOF
scope: ${SCOPE}
namespace: /smsly/
name: ${PATRONI_NAME}

restapi:
  listen: 0.0.0.0:8008
  connect_address: ${POD_IP}:8008

etcd3:
  hosts: ${ETCD3_HOST}

bootstrap:
  dcs:
    ttl: 30
    loop_wait: 10
    retry_timeout: 10
    maximum_lag_on_failover: 1048576
    postgresql:
      use_pg_rewind: true
      parameters:
        max_connections: 200
        shared_buffers: 1GB
        effective_cache_size: 3GB
        wal_level: replica
        max_wal_senders: 10
        max_replication_slots: 10
        hot_standby: "on"
        wal_keep_size: 1GB
  initdb:
    - encoding: UTF8
    - data-checksums
  pg_hba:
    - host replication replicator 172.16.0.0/12 scram-sha-256
    - host replication replicator 10.0.0.0/8 scram-sha-256
    - host all all 0.0.0.0/0 scram-sha-256
  users:
    ${PGUSER_ADMIN}:
      password: ${PGPASSWORD_ADMIN}
      options:
        - createdb
        - createrole

postgresql:
  listen: 0.0.0.0:5432
  connect_address: ${POD_IP}:5432
  data_dir: ${PGDATA}
  bin_dir: /usr/lib/postgresql/16/bin
  pgpass: /tmp/pgpass
  authentication:
    superuser:
      username: ${PGUSER_SUPERUSER}
      password: ${PGPASSWORD_SUPERUSER}
    replication:
      username: replicator
      password: ${PGPASSWORD_STANDBY}
  parameters:
    unix_socket_directories: '/var/run/postgresql'
  create_replica_methods: []

tags:
  nofailover: false
  noloadbalance: false
  clonefrom: false
  nosync: false
EOF

chown -R postgres:postgres /var/lib/postgresql/data /var/log/postgresql
exec patroni /etc/patroni.yml
