#!/bin/sh
set -e

# ─── PgBouncer entrypoint ────────────────────────────────────────────────────
# Generates pgbouncer.ini and userlist.txt from environment variables,
# then starts pgbouncer in foreground mode.

# Required env vars
: "${POSTGRESQL_HOST:?POSTGRESQL_HOST required}"
: "${POSTGRESQL_PORT:=5432}"
: "${POSTGRESQL_USERNAME:?POSTGRESQL_USERNAME required}"
: "${POSTGRESQL_PASSWORD:?POSTGRESQL_PASSWORD required}"
: "${POSTGRESQL_DATABASE:?POSTGRESQL_DATABASE required}"

# PgBouncer settings (with defaults)
PGBOUNCER_PORT="${PGBOUNCER_PORT:-5432}"
PGBOUNCER_POOL_MODE="${PGBOUNCER_POOL_MODE:-transaction}"
PGBOUNCER_DEFAULT_POOL_SIZE="${PGBOUNCER_DEFAULT_POOL_SIZE:-10}"
PGBOUNCER_MIN_POOL_SIZE="${PGBOUNCER_MIN_POOL_SIZE:-5}"
PGBOUNCER_MAX_CLIENT_CONN="${PGBOUNCER_MAX_CLIENT_CONN:-10000}"
PGBOUNCER_MAX_DB_CONNECTIONS="${PGBOUNCER_MAX_DB_CONNECTIONS:-20}"
PGBOUNCER_SERVER_RESET_QUERY="${PGBOUNCER_SERVER_RESET_QUERY:-DISCARD ALL}"
PGBOUNCER_AUTH_TYPE="${PGBOUNCER_AUTH_TYPE:-scram-sha-256}"

# Create config directory
mkdir -p /etc/pgbouncer

# Generate pgbouncer.ini
cat > /etc/pgbouncer/pgbouncer.ini <<EOF
[databases]
${POSTGRESQL_DATABASE} = host=${POSTGRESQL_HOST} port=${POSTGRESQL_PORT} dbname=${POSTGRESQL_DATABASE}
* = host=${POSTGRESQL_HOST} port=${POSTGRESQL_PORT}

[pgbouncer]
listen_addr = 0.0.0.0
listen_port = ${PGBOUNCER_PORT}
auth_type = ${PGBOUNCER_AUTH_TYPE}
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = ${PGBOUNCER_POOL_MODE}
default_pool_size = ${PGBOUNCER_DEFAULT_POOL_SIZE}
min_pool_size = ${PGBOUNCER_MIN_POOL_SIZE}
max_client_conn = ${PGBOUNCER_MAX_CLIENT_CONN}
max_db_connections = ${PGBOUNCER_MAX_DB_CONNECTIONS}
server_reset_query = ${PGBOUNCER_SERVER_RESET_QUERY}
ignore_startup_parameters = extra_float_digits,options
admin_users = ${POSTGRESQL_USERNAME}
stats_users = ${POSTGRESQL_USERNAME}
log_connections = 0
log_disconnections = 0
log_pooler_errors = 1
EOF

# Generate userlist.txt (plain text auth for scram-sha-256 passthrough)
cat > /etc/pgbouncer/userlist.txt <<EOF
"${POSTGRESQL_USERNAME}" "${POSTGRESQL_PASSWORD}"
EOF

chmod 600 /etc/pgbouncer/userlist.txt

echo "PgBouncer starting: pool_mode=${PGBOUNCER_POOL_MODE}, pool_size=${PGBOUNCER_DEFAULT_POOL_SIZE}, max_clients=${PGBOUNCER_MAX_CLIENT_CONN}"

# Run pgbouncer in foreground
exec pgbouncer /etc/pgbouncer/pgbouncer.ini
