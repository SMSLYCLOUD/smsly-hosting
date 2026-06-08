#!/bin/bash
set -e

# Create replication user so the replica can connect for streaming
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'replicator') THEN
            CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD:-repl_change_me}';
        END IF;
    END
    \$\$;
EOSQL

# Append replication auth to pg_hba.conf so the replica can connect
if ! grep -q 'replication.*replicator' "$PGDATA/pg_hba.conf" 2>/dev/null; then
    cat >> "$PGDATA/pg_hba.conf" <<'HBAEOF'
# Allow replication user from any container on the docker network
host    replication     replicator      all             scram-sha-256
HBAEOF
fi
