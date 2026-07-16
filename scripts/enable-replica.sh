#!/usr/bin/env bash
# Enable PostgreSQL streaming replication on a single-node SMSLY
# Hosting deployment.
#
# This script is the operator-facing entry point for the opt-in read
# replica. It is intentionally idempotent: running it twice does not
# break the deployment.
#
# What it does:
#   1. Generates a strong REPLICATION_PASSWORD (unless one is already
#      set in .env).
#   2. Ensures the primary ``db`` container is running with WAL
#      settings that allow streaming replication (wal_level=replica,
#      max_wal_senders>=1, max_replication_slots>=1).
#   3. Creates the ``replicator`` role on the primary with the
#      matching password (idempotent — uses IF NOT EXISTS).
#   4. Brings up the ``db-replica`` service via the
#      ``docker-compose.replica.yml`` overlay.
#   5. Sets DB_REPLICA_HOSTS in .env so pgcat routes SELECTs to the
#      replica.
#   6. Restarts pgcat so it picks up the new DB_REPLICA_HOSTS value.
#   7. Waits for the replica to finish its initial basebackup and
#      enter ``streaming`` state.
#   8. Prints a verification summary.
#
# Pre-conditions:
#   * You are on the master VPS with the existing deployment healthy.
#   * /opt/smsly-hosting contains the working tree (this script lives
#     in scripts/ and is run from there).
#   * The primary postgres has at least ~20% free disk space for the
#     basebackup. On a small DB (~1GB) the basebackup takes 1-2
#     minutes; on a large DB (100GB+) it can take an hour.
#
# Usage:
#   sudo ./scripts/enable-replica.sh
#
# Rollback:
#   sudo docker compose -f docker-compose.prod.yml -f docker-compose.replica.yml down db-replica
#   # Then unset REPLICATION_PASSWORD and DB_REPLICA_HOSTS in .env and restart pgcat.

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/smsly-hosting}"
COMPOSE_BASE="${COMPOSE_BASE:-docker-compose.prod.yml}"
COMPOSE_REPLICA="${COMPOSE_REPLICA:-docker-compose.replica.yml}"
ENV_FILE="${ENV_FILE:-$INSTALL_DIR/.env}"
PG_USER="${POSTGRES_USER:-smsly_admin}"
PG_DB="${POSTGRES_DB:-smsly_hosting}"

red()   { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
blue()  { printf "\033[34m%s\033[0m\n" "$*"; }
bold()  { printf "\033[1m%s\033[0m\n" "$*"; }

require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        red "ERROR: must run as root (sudo $0)"
        exit 1
    fi
}

require_env_file() {
    if [ ! -f "$ENV_FILE" ]; then
        red "ERROR: $ENV_FILE not found. Run install.sh first."
        exit 1
    fi
}

env_get() {
    # Returns the value of a KEY=... line in $ENV_FILE, or empty.
    awk -F= -v key="$1" '
        $1 == key { sub(/^[^=]*=/, "", $0); print; exit }
    ' "$ENV_FILE"
}

env_set() {
    # Idempotent: updates an existing KEY= line, or appends one.
    local key="$1" value="$2"
    if grep -qE "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        printf "\n%s=%s\n" "$key" "$value" >> "$ENV_FILE"
    fi
    blue "  -> $key updated"
}

gen_password() {
    python3 -c "import secrets; print(secrets.token_hex(16))"
}

ensure_replication_role() {
    blue "[1/6] Ensuring replicator role exists on the primary..."
    local repl_pass="$1"
    # Escape single quotes to prevent SQL injection
    repl_pass="${repl_pass//\'/\'\'}"
    local db_container
    db_container="$(docker compose -f "$COMPOSE_BASE" ps -q db | head -1)"
    if [ -z "$db_container" ]; then
        red "ERROR: db container not running. Start the stack first."
        exit 1
    fi
    docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$db_container" \
        psql -U "$PG_USER" -d "$PG_DB" -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'replicator') THEN
        CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '$repl_pass';
    ELSE
        ALTER ROLE replicator WITH REPLICATION LOGIN PASSWORD '$repl_pass';
    END IF;
END
\$\$;
SQL
    green "  OK replicator role ready"
}

ensure_wal_settings() {
    blue "[2/6] Verifying primary has WAL settings for streaming replication..."
    local db_container
    db_container="$(docker compose -f "$COMPOSE_BASE" ps -q db | head -1)"
    local wal_level
    wal_level="$(docker exec "$db_container" psql -U "$PG_USER" -d "$PG_DB" -tAc "SHOW wal_level")"
    if [ "$wal_level" = "replica" ] || [ "$wal_level" = "logical" ]; then
        green "  OK wal_level=$wal_level"
    else
        red "ERROR: primary wal_level is '$wal_level' — must be 'replica' or 'logical'."
        red "  The production compose uses the default postgres:16-alpine image which has wal_level=replica by default."
        red "  If you have a custom command, append: -c wal_level=replica -c max_wal_senders=5"
        exit 1
    fi
}

start_replica() {
    blue "[3/6] Bringing up the db-replica container (initial basebackup may take 1-30 minutes)..."
    docker compose -f "$COMPOSE_BASE" -f "$COMPOSE_REPLICA" up -d db-replica
    green "  OK db-replica started"
}

set_replica_env() {
    blue "[4/6] Setting DB_REPLICA_HOSTS in .env so pgcat routes SELECTs to the replica..."
    # Additive: if the operator already configured other replicas
    # (e.g. a remote-replica:5432 they manage themselves), preserve
    # them and append db-replica:5432. Duplicates are de-duped so the
    # final list is canonical. If unset, the value is just the new
    # entry.
    local existing
    existing="$(env_get DB_REPLICA_HOSTS)"
    local entry="db-replica:5432"
    local merged
    if [ -z "$existing" ]; then
        merged="$entry"
    else
        # De-dupe while preserving order.
        merged="$(printf '%s\n%s\n' "$existing" "$entry" \
            | awk '!seen[$0]++' \
            | paste -sd, -)"
    fi
    env_set "DB_REPLICA_HOSTS" "$merged"
    green "  OK DB_REPLICA_HOSTS=$merged"
}

restart_pgcat() {
    blue "[5/6] Restarting pgcat to pick up DB_REPLICA_HOSTS..."
    docker compose -f "$COMPOSE_BASE" restart pgcat
    green "  OK pgcat restarted"
}

wait_for_streaming() {
    blue "[6/6] Waiting for replica to enter streaming state (up to 10 minutes)..."
    local db_container
    db_container="$(docker compose -f "$COMPOSE_BASE" ps -q db | head -1)"
    local i
    for i in $(seq 1 60); do
        local state
        state="$(docker exec "$db_container" psql -U "$PG_USER" -d "$PG_DB" -tAc "SELECT state FROM pg_stat_replication LIMIT 1"  || true)"
        if [ "$state" = "streaming" ]; then
            green "  OK replica is streaming (state=streaming) after ${i}0-second polls"
            return 0
        fi
        sleep 10
    done
    red "ERROR: replica did not enter streaming state within 10 minutes."
    red "  Check: docker logs smsly-hosting-db-replica-1"
    exit 1
}

print_summary() {
    bold ""
    bold "============================================================"
    bold "  Read replica enabled"
    bold "============================================================"
    cat <<EOF

Next steps:
  1. Confirm reads are going to the replica:
       docker exec smsly-hosting-db-replica-1 \
         psql -U $PG_USER -d $PG_DB \
         -c "SELECT pg_is_in_recovery();"
     (should return 't' — meaning "yes, I'm a replica")

  2. Confirm pgcat knows about it:
       docker exec smsly-hosting-pgcat-1 cat /etc/pgcat/pgcat.toml | grep -A1 shards
     (the shards.0.servers list should now include ["db-replica", 5432, "replica"])

  3. Roll back if needed:
       docker compose -f $COMPOSE_BASE -f $COMPOSE_REPLICA down db-replica
       # then unset DB_REPLICA_HOSTS in .env and restart pgcat
EOF
}

main() {
    require_root
    require_env_file

    cd "$INSTALL_DIR"

    bold "SMSLY Hosting — Enable PostgreSQL Read Replica"
    echo

    # Step 1: REPLICATION_PASSWORD
    local repl_pass
    repl_pass="$(env_get REPLICATION_PASSWORD)"
    if [ -z "$repl_pass" ]; then
        repl_pass="$(gen_password)"
        env_set "REPLICATION_PASSWORD" "$repl_pass"
        green "  -> generated new REPLICATION_PASSWORD"
    else
        blue "  -> REPLICATION_PASSWORD already set, reusing"
    fi

    ensure_wal_settings
    ensure_replication_role "$repl_pass"
    start_replica
    set_replica_env
    restart_pgcat
    wait_for_streaming
    print_summary
}

main "$@"
