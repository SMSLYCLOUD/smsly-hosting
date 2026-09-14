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
DB_READY=false
for i in $(seq 1 24); do
    if timeout 10 docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U smsly_admin < /dev/null ; then
        echo -e "${GREEN}  ✓ Database is ready (attempt $i).${NC}"
        DB_READY=true
        break
    fi
    printf "."
    sleep 5
done
echo ""

if [ "$DB_READY" != "true" ]; then
    echo -e "${RED}  ✗ Database failed to become ready after 2 minutes.${NC}"
    echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs db${NC}"
    exit 1
fi

# ─── Sync DB password to match .env (handles volume from previous install) ──
# The DB volume persists with the password from FIRST init.
# Always reset the password inside PostgreSQL to match the current .env.
set -a
source "$INSTALL_DIR/.env"  || true
set +a
echo -e "${BLUE}  → Syncing database password...${NC}"

# The DB volume persists with the password from FIRST init, and .env may have
# been regenerated since. Local socket auth is TRUST in the official postgres
# image, so ALTER USER over the socket works regardless of the current DB
# password. Note: with POSTGRES_USER=smsly_admin the "postgres" role does NOT
# exist — smsly_admin itself is the superuser.
DB_SUPERUSER="${POSTGRES_USER:-smsly_admin}"
DB_NAME="${POSTGRES_DB:-smsly_hosting}"
PW_SYNCED=false
if timeout 30 docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U "$DB_SUPERUSER" -d postgres \
    -c "ALTER USER ${DB_SUPERUSER} WITH PASSWORD '${POSTGRES_PASSWORD}';" \
    < /dev/null ; then
    echo -e "${GREEN}  ✓ Database password synced via superuser ${DB_SUPERUSER}${NC}"
    PW_SYNCED=true
elif timeout 30 docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U postgres -d postgres \
    -c "ALTER USER ${DB_SUPERUSER} WITH PASSWORD '${POSTGRES_PASSWORD}';" \
    < /dev/null ; then
    echo -e "${GREEN}  ✓ Database password synced via postgres superuser${NC}"
    PW_SYNCED=true
else
    echo -e "${RED}  ✗ Could not sync password over local socket. Check pg_hba.conf${NC}"
fi

# The socket check above bypasses auth (trust), so verify over TCP with the
# .env password — this is the only check that proves the password actually
# matches what the app will use. Must use the network hostname (eth0), not
# 127.0.0.1: the official postgres image trusts loopback too.
if timeout 30 docker compose -f "$COMPOSE_FILE" exec -T \
    -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
    psql -h db -U "$DB_SUPERUSER" -d "$DB_NAME" -c "SELECT 1;" < /dev/null ; then
    echo -e "${GREEN}  ✓ Database password verified over TCP${NC}"
else
    echo -e "${RED}  ✗ Password verification over TCP failed — migrations will fail. Check pg_hba.conf${NC}"
    exit 1
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

    echo -e "${BLUE}  → Running Migrations...${NC}"

    # Stop all services that talk to the DB.  Any open connection — even
    # a SELECT — holds a shared lock that blocks the ACCESS EXCLUSIVE
    # lock an ALTER TABLE needs.  Celery, backend health checks, and
    # PgCat connection pools all compete with the migration.
    MIGRATION_STOPPED_SVCS="backend celery celery-deploy celery-fast celery-beat $(grep -q "^  *pgcat:" "${COMPOSE_FILE:-docker-compose.prod.yml}"  && echo "pgcat")"
    echo -e "${BLUE}    Stopping ${MIGRATION_STOPPED_SVCS} to prevent lock contention...${NC}"
    docker compose -f "$COMPOSE_FILE" stop --timeout 15 ${MIGRATION_STOPPED_SVCS} || echo -e "${YELLOW}    ⚠ Stop failed for some services${NC}"
    sleep 3

    # Kill every backend on the database so the migration owns it exclusively
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U smsly_admin -d smsly_hosting \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'" \
        < /dev/null \
         || echo -e "${YELLOW}    ⚠ Failed to terminate stale connections${NC}"
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
        timeout 30 docker compose -f "$COMPOSE_FILE" exec -T db \
            psql -U smsly_admin -d smsly_hosting \
            -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'" \
            < /dev/null \
             || echo -e "${YELLOW}    ⚠ Failed to terminate stale connections${NC}"
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
    timeout 120 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py collectstatic --noinput < /dev/null || echo -e "${YELLOW}    ⚠ collectstatic failed or timed out${NC}"

    sync_platform_domain_state "$INSTALL_DIR/.env"
    set_checkpoint "database_initialized"
fi
fi
