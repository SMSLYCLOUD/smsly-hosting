#!/bin/sh
set -e

# pgcat-tenants (PgBouncer engine): pool shared logical databases behind
# stable aliases. The backend renders /etc/pgcat/pgbouncer.ini +
# /etc/pgcat/userlist.txt (named volume pgcat_tenants_config) and pushes
# them on every shared provision/delete/rotate. We wait for the first
# push instead of booting with an empty config.
CONFIG="/etc/pgcat/pgbouncer.ini"

echo "pgbouncer-tenants: waiting for backend-rendered config at $CONFIG..."
# -s (non-empty), not -f: an empty file makes pgbouncer exit immediately
# and the container crash-loops instead of waiting (observed live).
while [ ! -s "$CONFIG" ]; do sleep 5; done

echo "pgbouncer-tenants: starting..."
exec pgbouncer "$CONFIG"
