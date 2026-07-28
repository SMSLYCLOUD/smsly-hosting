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
    if timeout 10 docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U smsly_admin ; then
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

# Try local trust auth first (Docker default), then try with PGPASSWORD
if timeout 30 docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U postgres -c "ALTER USER smsly_admin WITH PASSWORD '${POSTGRES_PASSWORD}';" \
    ; then
    echo -e "${GREEN}  ✓ Database password synced${NC}"
elif timeout 30 docker compose -f "$COMPOSE_FILE" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
    psql -U smsly_admin -d smsly_hosting -c "SELECT 1;" ; then
    echo -e "${GREEN}  ✓ Database password already matches${NC}"
else
    echo -e "${YELLOW}  ⚠ Password mismatch — resetting via postgres superuser...${NC}"
    # Last resort: the Docker postgres container always accepts local postgres user
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U postgres -c "ALTER USER smsly_admin WITH PASSWORD '${POSTGRES_PASSWORD}';" \
         || echo -e "${RED}  ✗ Could not sync password. Check pg_hba.conf${NC}"
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
    # Fix volume ownership — Docker creates named volumes as root
    echo -e "${BLUE}    ↳ Fixing volume ownership...${NC}"
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T --user root backend chown -R 1000:1000 /app/staticfiles /app/media /app/backups || echo -e "${YELLOW}    ⚠ Volume ownership fix failed${NC}"
    echo -e "${BLUE}    ↳ Running collectstatic...${NC}"
    timeout 120 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py collectstatic --noinput || echo -e "${YELLOW}    ⚠ collectstatic failed or timed out${NC}"

    sync_platform_domain_state "$INSTALL_DIR/.env"
    set_checkpoint "database_initialized"
fi
fi
