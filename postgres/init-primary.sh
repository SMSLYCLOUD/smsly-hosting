#!/bin/bash
# Initialize PostgreSQL Primary for Replication
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'replicator') THEN
            CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD:-repl_secret_2024}';
        END IF;
        GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO replicator;
    END
    \$\$;

    DO \$\$
    BEGIN
        PERFORM pg_create_physical_replication_slot('replica_slot_1');
    EXCEPTION
        WHEN duplicate_object THEN
            RAISE NOTICE 'replication slot ''replica_slot_1'' already exists';
    END
    \$\$;
EOSQL

echo "Primary PostgreSQL configured for replication"
