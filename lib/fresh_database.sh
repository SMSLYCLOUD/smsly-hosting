# -----------------------------------------------------------------------------
# 5. Database Setup
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "database_initialized"; then
    echo -e "\n${YELLOW}[5/9] Initializing Database...${NC}"

if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "${BLUE}  → Lite Agent mode: skipping local database initialization; using Master services.${NC}"
    set_checkpoint "database_initialized"
else
echo -e "${BLUE}  → Waiting for Database...${NC}"
# DB access follows the HA mode. local-ha uses the `db` service over the
# local socket (trust) with TCP verify over the Docker network; patroni
# has no pg_isready in haproxy, so readiness runs inside a patroni node
# and traffic is verified through HAProxy's write port; external has no
# local container at all, so checks run over TCP from an ephemeral
# postgres client (image pull is one-time and cached).
_db_mode="${DB_HA_ENABLED:-local-ha}"
_db_exec_svc="db"
_db_exec_user="${POSTGRES_USER:-smsly_admin}"
_db_exec_pass="${POSTGRES_PASSWORD:-}"
_db_check_host="db"
_db_check_port="5432"
_db_check_user="${POSTGRES_USER:-smsly_admin}"
_db_check_pass="${POSTGRES_PASSWORD:-}"
_db_name="${POSTGRES_DB:-smsly_hosting}"
case "$_db_mode" in
    patroni)
        _db_exec_svc=""
        for _patroni_node in patroni1 patroni2 patroni3; do
            if timeout 10 docker compose -f "$COMPOSE_FILE" ps -q "$_patroni_node" 2>/dev/null | grep -q .; then
                _db_exec_svc="$_patroni_node"
                break
            fi
        done
        if [ -z "$_db_exec_svc" ]; then
            echo -e "${RED}  ✗ No patroni node container found (patroni1/2/3). Check: docker compose -f $COMPOSE_FILE ps${NC}"
            exit 1
        fi
        _db_exec_user="postgres"
        _db_exec_pass="${PATRONI_SUPERUSER_PASSWORD:-}"
        _db_check_host="haproxy"
        _db_check_port="5000"
        ;;
    external)
        _db_exec_svc=""
        _db_check_host="${PGCAT_DB_HOST:-}"
        _db_check_port="${PGCAT_DB_PORT:-5432}"
        if [ -z "$_db_check_host" ]; then
            echo -e "${RED}  ✗ External DB mode but PGCAT_DB_HOST is unset in $INSTALL_DIR/.env${NC}"
            exit 1
        fi
        ;;
esac
DB_READY=false
if [ "$_db_mode" = "external" ]; then
    echo -e "${BLUE}  → External mode: probing ${_db_check_host}:${_db_check_port} (no local container)...${NC}"
    for i in $(seq 1 24); do
        if timeout 30 docker run --rm --network smsly-net postgres:16-alpine \
                pg_isready -h "$_db_check_host" -p "$_db_check_port" < /dev/null ; then
            echo -e "${GREEN}  ✓ Database is ready (attempt $i).${NC}"
            DB_READY=true
            break
        fi
        printf "."
        sleep 5
    done
else
    for i in $(seq 1 24); do
        if timeout -k 5 10 docker compose -f "$COMPOSE_FILE" exec -T "$_db_exec_svc" pg_isready -U "$_db_exec_user" < /dev/null ; then
            echo -e "${GREEN}  ✓ Database is ready (attempt $i).${NC}"
            DB_READY=true
            break
        fi
        printf "."
        sleep 5
    done
fi
echo ""

if [ "$DB_READY" != "true" ]; then
    echo -e "${RED}  ✗ Database failed to become ready after 2 minutes.${NC}"
    if [ "$_db_mode" = "external" ]; then
        echo -e "${YELLOW}  Check: host ${_db_check_host}:${_db_check_port} reachable from Docker, security groups, and credentials.${NC}"
    else
        echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs ${_db_exec_svc}${NC}"
    fi
    exit 1
fi

# ─── Sync DB password to match .env (handles volume from previous install) ──
# The DB volume persists with the password from FIRST init.
# Always reset the password inside PostgreSQL to match the current .env.
set -a
source "$INSTALL_DIR/.env"  || true
set +a
# SQL-escape the password (single quotes doubled) — custom .env passwords
# may contain quotes that would otherwise break the ALTER USER statement.
_db_pw_escaped="${POSTGRES_PASSWORD//\'/\'\'}"
_db_super_escaped="${PATRONI_SUPERUSER_PASSWORD:-}"
_db_super_escaped="${_db_super_escaped//\'/\'\'}"
if [ "$_db_mode" = "external" ]; then
    echo -e "${BLUE}  → External mode: skipping password sync (roles are managed outside this host)...${NC}"
    PW_SYNCED=true
elif [ "$_db_mode" = "patroni" ]; then
    echo -e "${BLUE}  → Syncing database password via patroni superuser...${NC}"
    PW_SYNCED=false
    if [ -n "$_db_exec_pass" ] && timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T \
        -e PGPASSWORD="$_db_exec_pass" "$_db_exec_svc" \
        psql -U "$_db_exec_user" -d postgres \
        -c "ALTER USER ${POSTGRES_USER:-smsly_admin} WITH PASSWORD '${_db_pw_escaped}';" \
        < /dev/null ; then
        echo -e "${GREEN}  ✓ Database password synced via patroni superuser${NC}"
        PW_SYNCED=true
    else
        echo -e "${RED}  ✗ Could not sync password via patroni superuser. Check PATRONI_SUPERUSER_PASSWORD.${NC}"
    fi
else
    echo -e "${BLUE}  → Syncing database password...${NC}"

    # The DB volume persists with the password from FIRST init, and .env may have
    # been regenerated since. Local socket auth is TRUST in the official postgres
    # image, so ALTER USER over the socket works regardless of the current DB
    # password. Note: with POSTGRES_USER=smsly_admin the "postgres" role does NOT
    # exist — smsly_admin itself is the superuser.
    DB_SUPERUSER="${POSTGRES_USER:-smsly_admin}"
    DB_NAME="${POSTGRES_DB:-smsly_hosting}"
    PW_SYNCED=false
    if timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T "$_db_exec_svc" \
        psql -U "$DB_SUPERUSER" -d postgres \
        -c "ALTER USER ${DB_SUPERUSER} WITH PASSWORD '${_db_pw_escaped}';" \
        < /dev/null ; then
        echo -e "${GREEN}  ✓ Database password synced via superuser ${DB_SUPERUSER}${NC}"
        PW_SYNCED=true
    elif timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T "$_db_exec_svc" \
        psql -U postgres -d postgres \
        -c "ALTER USER ${DB_SUPERUSER} WITH PASSWORD '${_db_pw_escaped}';" \
        < /dev/null ; then
        echo -e "${GREEN}  ✓ Database password synced via postgres superuser${NC}"
        PW_SYNCED=true
    else
        echo -e "${RED}  ✗ Could not sync password over local socket. Check pg_hba.conf${NC}"
    fi
fi

# The socket check above bypasses auth (trust), so verify over TCP with the
# .env password — this is the only check that proves the password actually
# matches what the app will use. Uses the mode's check endpoint (local-ha:
# the db service hostname; patroni: HAProxy's write port, i.e. the exact
# path migrations take; external: the managed host directly).
if [ "$_db_mode" = "external" ]; then
    if timeout 60 docker run --rm --network smsly-net \
        -e PGPASSWORD="$_db_check_pass" postgres:16-alpine \
        psql -h "$_db_check_host" -p "$_db_check_port" -U "$_db_check_user" -d "$_db_name" -c "SELECT 1;" < /dev/null ; then
        echo -e "${GREEN}  ✓ Database password verified over TCP${NC}"
    else
        echo -e "${RED}  ✗ Password verification over TCP failed — migrations will fail. Check credentials and security groups.${NC}"
        exit 1
    fi
elif [ "$_db_mode" = "patroni" ]; then
    if timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T \
        -e PGPASSWORD="$_db_check_pass" "$_db_exec_svc" \
        psql -h "$_db_check_host" -p "$_db_check_port" -U "$_db_check_user" -d "$_db_name" -c "SELECT 1;" < /dev/null ; then
        echo -e "${GREEN}  ✓ Database password verified over TCP (via ${_db_check_host}:${_db_check_port})${NC}"
    else
        echo -e "${RED}  ✗ Password verification over TCP failed — migrations will fail. Check pg_hba.conf${NC}"
        exit 1
    fi
else
    if timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T \
        -e PGPASSWORD="$_db_check_pass" "$_db_exec_svc" \
        psql -h "$_db_check_host" -U "$_db_check_user" -d "$_db_name" -c "SELECT 1;" < /dev/null ; then
        echo -e "${GREEN}  ✓ Database password verified over TCP${NC}"
    else
        echo -e "${RED}  ✗ Password verification over TCP failed — migrations will fail. Check pg_hba.conf${NC}"
        exit 1
    fi
fi

# ─── Ensure PgCat is fresh and connected ──────────────────────────────────────
if [ -f "${COMPOSE_FILE:-docker-compose.prod.yml}" ] && grep -q "^  *pgcat:" "${COMPOSE_FILE:-docker-compose.prod.yml}"  && docker compose -f "$COMPOSE_FILE" ps pgcat ; then
    echo -e "${BLUE}  → Restarting PgCat balancer...${NC}"
    timeout -k 5 30 docker compose -f "$COMPOSE_FILE" restart pgcat || echo -e "${YELLOW}    ⚠ PgCat restart failed${NC}"
fi

# ─── Restart backend so it picks up the correct DB credentials ──────────────
echo -e "${BLUE}  → Restarting backend with synced credentials...${NC}"
timeout -k 5 30 docker compose -f "$COMPOSE_FILE" restart backend || echo -e "${YELLOW}    ⚠ Backend restart failed${NC}"
sleep 5

# ─── Wait for Redis replication (write-guard race) ─────────────────────────
# With REDIS_MIN_REPLICAS_TO_WRITE>=1 the primary REJECTS writes until a
# replica is connected. Starting backends/celery before that point turns
# first boot into auth/session failures that look like app bugs. Wait
# for at least one connected slave (5 min cap, then fail loud with the
# replica logs attached). Skipped when the operator allows writes
# without replicas (MIN_REPLICAS_TO_WRITE=0, e.g. single-node dev).
if [ "${REDIS_MIN_REPLICAS_TO_WRITE:-1}" != "0" ]; then
    echo -e "${BLUE}  → Waiting for Redis replica to attach (writes require 1 replica)...${NC}"
    _redis_synced=false
    _redis_slaves=""
    for i in $(seq 1 60); do
        # -k 5: bare `timeout` waits forever if the child ignores SIGTERM
        # (observed live: one hung `compose exec` wedged the whole install
        # at this gate with a healthy replica attached).
        _redis_slaves="$(timeout -k 5 10 docker compose -f "$COMPOSE_FILE" exec -T redis-primary \
            redis-cli -a "${REDIS_PASSWORD:-}" --no-auth-warning info replication 2>/dev/null \
            | grep -E '^connected_slaves:' | cut -d: -f2 | tr -d '\r[:space:]' || true)"
        if [ -n "$_redis_slaves" ] && [ "$_redis_slaves" -ge 1 ] 2>/dev/null; then
            echo -e "${GREEN}  ✓ Redis replica attached (${_redis_slaves} slave(s), attempt $i).${NC}"
            _redis_synced=true
            break
        fi
        sleep 5
    done
    if [ "$_redis_synced" != "true" ]; then
        echo -e "${RED}  ✗ Redis replica did not attach within 5 minutes (REDIS_MIN_REPLICAS_TO_WRITE=${REDIS_MIN_REPLICAS_TO_WRITE:-1}).${NC}"
        echo -e "${YELLOW}  Primary rejects writes until a replica connects — starting now would 500 every session/cache write.${NC}"
        docker compose -f "$COMPOSE_FILE" logs --tail=30 redis-primary redis-replica  || true
        echo -e "${YELLOW}  Fix the replica (or set REDIS_MIN_REPLICAS_TO_WRITE=0 in $INSTALL_DIR/.env for non-HA) and re-run with --resume.${NC}"
        exit 1
    fi
fi

    echo -e "${BLUE}  → Running Migrations...${NC}"

    # Stop all services that talk to the DB.  Any open connection — even
    # a SELECT — holds a shared lock that blocks the ACCESS EXCLUSIVE
    # lock an ALTER TABLE needs.  Celery, backend health checks, and
    # PgCat connection pools all compete with the migration.
    MIGRATION_STOPPED_SVCS="backend celery celery-deploy celery-fast celery-beat $(grep -q "^  *pgcat:" "${COMPOSE_FILE:-docker-compose.prod.yml}"  && echo "pgcat")"
    echo -e "${BLUE}    Stopping ${MIGRATION_STOPPED_SVCS} to prevent lock contention...${NC}"
    docker compose -f "$COMPOSE_FILE" stop --timeout 15 ${MIGRATION_STOPPED_SVCS} || echo -e "${YELLOW}    ⚠ Stop failed for some services${NC}"
    sleep 3

    # Kill every backend on the database so the migration owns it exclusively.
    # Uses the mode's exec endpoint (external mode has no local container).
    if [ "$_db_mode" = "external" ]; then
        echo -e "${YELLOW}    ⚠ External mode: cannot terminate server-side connections; relying on migration locks${NC}"
    elif timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T \
        -e PGPASSWORD="$_db_exec_pass" "$_db_exec_svc" \
        psql -U "$_db_exec_user" -d "$_db_name" \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'" \
        < /dev/null ; then
        true
    else
        echo -e "${YELLOW}    ⚠ Failed to terminate stale connections${NC}"
    fi
    sleep 2

    echo -e "${BLUE}    Running migrations (database: direct)...${NC}"
    # Note: Do NOT run makemigrations — migrations are committed in the repo.
    MIGRATE_OK=false
    # Migration runs via DIRECT_DATABASE_URL which goes straight to the
    # postgres backend, not through PgCat, so PgCat being stopped is safe.
    if run_backend_migrations ; then
        MIGRATE_OK=true
    else
        echo -e "${YELLOW}  ⚠ Migration attempt 1 failed — killing stale connections and retrying...${NC}"
        if [ "$_db_mode" = "external" ]; then
            echo -e "${YELLOW}    ⚠ External mode: cannot terminate server-side connections; retrying migration directly${NC}"
        elif timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T \
            -e PGPASSWORD="$_db_exec_pass" "$_db_exec_svc" \
            psql -U "$_db_exec_user" -d "$_db_name" \
            -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'" \
            < /dev/null ; then
            true
        else
            echo -e "${YELLOW}    ⚠ Failed to terminate stale connections${NC}"
        fi
        sleep 5
        if run_backend_migrations ; then
            MIGRATE_OK=true
        fi
    fi

    # Restart everything that was paused
    echo -e "${BLUE}    Restarting ${MIGRATION_STOPPED_SVCS}...${NC}"
    docker compose -f "$COMPOSE_FILE" start ${MIGRATION_STOPPED_SVCS} || echo -e "${YELLOW}    ⚠ Some services failed to restart${NC}"
    sleep 5

    if [ "$MIGRATE_OK" != "true" ]; then
        echo -e "${RED}  ✗ Migrations failed after 2 attempts.${NC}"
        echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs backend${NC}"
        echo -e "${YELLOW}  ↳ Tip: Re-run with --resume: sudo bash install.sh --resume${NC}"
        exit 1
    fi

echo -e "${BLUE}  → Collecting Static Files...${NC}"
    # Fix volume ownership — Docker creates named volumes as root.
    # NOTE: `docker compose exec --user root backend chown` cannot work here:
    # the backend container runs with CapDrop=[ALL], so even uid 0 cannot
    # chown (no CAP_CHOWN). Run chown host-side via a throwaway alpine
    # container instead.
    echo -e "${BLUE}    ↳ Fixing volume ownership...${NC}"
    _vol_json="$(docker compose -f "$COMPOSE_FILE" config --format json 2>/dev/null || true)"
    for _vkey in static_volume media_volume backups_data; do
        _vol_name="$(printf '%s' "$_vol_json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('volumes',{}).get('$_vkey',{}).get('name',''))" 2>/dev/null || true)"
        if [ -n "$_vol_name" ] && docker volume inspect "$_vol_name" >/dev/null 2>&1; then
            timeout 90 docker run --rm -v "$_vol_name":/data alpine chown -R 1000:1000 /data || echo -e "${YELLOW}    ⚠ Volume ownership fix failed for $_vol_name${NC}"
        fi
    done
    echo -e "${BLUE}    ↳ Running collectstatic...${NC}"
    timeout -k 5 120 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py collectstatic --noinput < /dev/null || echo -e "${YELLOW}    ⚠ collectstatic failed or timed out${NC}"

    # NOTE: heredoc (not `bash -c "..."`) on purpose: the bundle regen
    # pipeline inlines `source` lines, and a source line inside a
    # double-quoted string would break backend/install.sh syntax.
    export COMPOSE_FILE INSTALL_DIR
    timeout -k 5 120 bash <<'SMSLY_SYNC_EOF' || echo -e "${YELLOW}    ⚠ Domain state sync timed out (non-fatal)${NC}"
source "$INSTALL_DIR/lib/env.sh"
source "$INSTALL_DIR/lib/common.sh"
source "$INSTALL_DIR/lib/platform.sh"
sync_platform_domain_state "$INSTALL_DIR/.env"
SMSLY_SYNC_EOF
    set_checkpoint "database_initialized"
fi
fi
