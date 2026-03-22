#!/bin/bash
# Initialize PostgreSQL Primary for Replication
set -e

# Create replication role
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    -- Create replication user
    CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD:-repl_secret_2024}';
    
    -- Grant necessary permissions
    GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO replicator;
    
    -- Create replication slot for the replica
    SELECT pg_create_physical_replication_slot('replica_slot_1');
    
    -- Confirm setup
    SELECT * FROM pg_replication_slots;
EOSQL

echo "Primary PostgreSQL configured for replication"
