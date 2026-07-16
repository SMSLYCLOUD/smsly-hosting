#!/bin/bash



set -e







# Create replication user so the replica can connect for streaming



psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL



    DO \$\$



    BEGIN



        IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'replicator') THEN



            CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD:?REPLICATION_PASSWORD must be set}';



        END IF;



    END



    \$\$;



EOSQL







# Append replication auth to pg_hba.conf so the replica can connect.



# Restrict to Docker's private network range (172.16.0.0/12) to prevent



# replication access from external networks if the port is accidentally exposed.



if ! grep -q '^host\s\+replication\s\+replicator' "$PGDATA/pg_hba.conf" ; then



    cat >> "$PGDATA/pg_hba.conf" <<'HBAEOF'



# Allow replication user from Docker private networks only



host    replication     replicator      172.16.0.0/12      scram-sha-256



host    replication     replicator      10.0.0.0/8         scram-sha-256



host    replication     replicator      192.168.0.0/16     scram-sha-256



HBAEOF



fi



