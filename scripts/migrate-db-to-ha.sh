#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# migrate-db-to-ha.sh
#
# Migrates data from the old standalone postgres container (smsly-hosting-db-1)
# to the new HA primary (smsly-postgres-primary). After migration, pgcat
# (with POSTGRES_HOST=postgres-primary) will serve all database traffic.
#
# Usage:
#   sudo bash scripts/migrate-db-to-ha.sh [--cleanup]
#
#   --cleanup   Stop and remove old db/redis containers after successful migration.
#
# The script is idempotent: safe to re-run. It skips migration if postgres-primary
# already has tables.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

COMPOSE_FILE="docker-compose.prod.yml"
OLD_DB_CONTAINER="smsly-hosting-db-1"
NEW_DB_CONTAINER="smsly-postgres-primary"
OLD_REDIS_CONTAINER="smsly-hosting-redis-1"
DB_NAME="${POSTGRES_DB:-smsly_hosting}"
DB_USER="${POSTGRES_USER:-smsly_admin}"
DUMP_FILE="/tmp/smsly_db_migration_$(date +%Y%m%d_%H%M%S).sql"

DO_CLEANUP=false
for arg in "$@"; do
    case "$arg" in
        --cleanup) DO_CLEANUP=true ;;
        --help|-h)
            echo "Usage: sudo bash scripts/migrate-db-to-ha.sh [--cleanup]"
            echo "  --cleanup   Remove old db/redis containers after migration"
            exit 0
            ;;
    esac
done

# ── Pre-flight checks ────────────────────────────────────────────────────────
echo -e "${BLUE}═══ SMSLY Database Migration: old db → postgres-primary ═══${NC}"

# Read POSTGRES_PASSWORD from .env
ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}✗ $ENV_FILE not found. Run from /opt/smsly-hosting${NC}"
    exit 1
fi
POSTGRES_PASSWORD="$(grep -E '^POSTGRES_PASSWORD=' "$ENV_FILE" | cut -d'=' -f2-)"
if [ -z "$POSTGRES_PASSWORD" ]; then
    echo -e "${RED}✗ POSTGRES_PASSWORD not set in $ENV_FILE${NC}"
    exit 1
fi

# Check old container exists
if ! docker ps -a --format '{{.Names}}' | grep -qx "$OLD_DB_CONTAINER"; then
    echo -e "${YELLOW}⚠ Old container $OLD_DB_CONTAINER not found — nothing to migrate${NC}"
    exit 0
fi

# Check new container exists — if not, start the HA stack and wait for it
if ! docker ps --format '{{.Names}}' | grep -qx "$NEW_DB_CONTAINER"; then
    echo -e "${YELLOW}⚠ $NEW_DB_CONTAINER is not running — starting HA stack...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d db postgres-replica 
    echo -e "  → Waiting for postgres-primary to become healthy..."
    for _i in $(seq 1 30); do
        if docker ps --format '{{.Names}}' | grep -qx "$NEW_DB_CONTAINER" && \
           timeout 30 docker exec "$NEW_DB_CONTAINER" pg_isready -U "$DB_USER" ; then
            echo -e "${GREEN}  ✓ postgres-primary is ready${NC}"
            break
        fi
        sleep 2
    done
    if ! docker ps --format '{{.Names}}' | grep -qx "$NEW_DB_CONTAINER"; then
        echo -e "${RED}✗ $NEW_DB_CONTAINER failed to start${NC}"
        exit 1
    fi
fi

# ── Check if postgres-primary already has data ────────────────────────────────
TABLE_COUNT=$(timeout 120 docker exec "$NEW_DB_CONTAINER" \
    psql -U "$DB_USER" -d "$DB_NAME" -t -A \
    -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" \
     || echo "0")

if [ "$TABLE_COUNT" -gt 0 ]; then
    echo -e "${YELLOW}⚠ postgres-primary already has $TABLE_COUNT tables — checking if migration needed...${NC}"

    # Compare row counts between old and new
    OLD_ROWS=$(timeout 120 docker exec "$OLD_DB_CONTAINER" \
        psql -U "$DB_USER" -d "$DB_NAME" -t -A \
        -c "SELECT sum(n_live_tup) FROM pg_stat_user_tables;" \
         || echo "0")
    NEW_ROWS=$(timeout 120 docker exec "$NEW_DB_CONTAINER" \
        psql -U "$DB_USER" -d "$DB_NAME" -t -A \
        -c "SELECT sum(n_live_tup) FROM pg_stat_user_tables;" \
         || echo "0")

    echo -e "  Old db rows:      ${OLD_ROWS:-0}"
    echo -e "  postgres-primary: ${NEW_ROWS:-0}"

    if [ "${NEW_ROWS:-0}" -ge "${OLD_ROWS:-0}" ] ; then
        echo -e "${GREEN}✓ postgres-primary already has equal or more data — skipping migration${NC}"
        if [ "$DO_CLEANUP" = true ]; then
            echo ""
            echo -e "${YELLOW}── Cleanup mode ──${NC}"
            echo -e "${YELLOW}  → Would stop: $OLD_DB_CONTAINER, $OLD_REDIS_CONTAINER${NC}"
            docker stop "$OLD_DB_CONTAINER" "$OLD_REDIS_CONTAINER" || echo -e "${YELLOW}    ⚠ Could not stop old containers${NC}"
            docker rm "$OLD_DB_CONTAINER" "$OLD_REDIS_CONTAINER" || echo -e "${YELLOW}    ⚠ Could not remove old containers${NC}"
            echo -e "${GREEN}  ✓ Old containers removed${NC}"
        fi
        exit 0
    fi
    echo -e "${YELLOW}  → Old db has more data — proceeding with migration${NC}"
fi

# ── Step 1: Dump from old db ─────────────────────────────────────────────────
echo ""
echo -e "${BLUE}→ Step 1: Dumping database from $OLD_DB_CONTAINER...${NC}"

timeout 600 docker exec "$OLD_DB_CONTAINER" \
    pg_dump -U "$DB_USER" -d "$DB_NAME" \
    --no-owner --no-acl --clean --if-exists \
    -F p  > "$DUMP_FILE"

DUMP_SIZE=$(wc -c < "$DUMP_FILE")
echo -e "${GREEN}  ✓ Dump created: $DUMP_FILE ($(( DUMP_SIZE / 1024 )) KB)${NC}"

if [ "$DUMP_SIZE" -lt 100 ]; then
    echo -e "${RED}✗ Dump file too small — aborting${NC}"
    rm -f "$DUMP_FILE"
    exit 1
fi

# ── Step 2: Stop backend/celery to prevent writes during restore ─────────────
echo ""
echo -e "${BLUE}→ Step 2: Pausing backend services to prevent writes...${NC}"

SVCS_TO_STOP="backend celery celery-deploy celery-fast celery-beat"
for svc in $SVCS_TO_STOP; do
    docker compose -f "$COMPOSE_FILE" stop "$svc" || echo -e "${YELLOW}    ⚠ Failed to stop $svc${NC}"
done
echo -e "${GREEN}  ✓ Backend services paused${NC}"

# ── Step 3: Restore to postgres-primary ──────────────────────────────────────
echo ""
echo -e "${BLUE}→ Step 3: Restoring to $NEW_DB_CONTAINER...${NC}"

# Connect via a superuser to drop/recreate the database.  The postgres
# image may not have a 'postgres' role if POSTGRES_USER was customized,
# so fall back to $DB_USER (which is typically smsly_admin, a superuser).
# Connect to template1 so we're not blocking the target database.
for _try_user in "postgres" "$DB_USER"; do
    if timeout 30 docker exec "$NEW_DB_CONTAINER" psql -U "$_try_user" -d template1 -c "SELECT 1;" ; then
        _admin_user="$_try_user"
        break
    fi
done
_admin_user="${_admin_user:-$DB_USER}"
echo "  → Using admin role: $_admin_user"

echo "  → Killing active connections to $DB_NAME..."
set +e
timeout 120 docker exec -i "$NEW_DB_CONTAINER" psql -U "$_admin_user" -d template1 <<EOSQL 
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = '$DB_NAME'
  AND pid <> pg_backend_pid();
EOSQL
set -e

echo "  → Dropping $DB_NAME if it exists..."
set +e
_drop_output=$(timeout 120 docker exec -i "$NEW_DB_CONTAINER" psql -U "$_admin_user" -d template1 -c "DROP DATABASE IF EXISTS $DB_NAME;" )
_drop_rc=$?
set -e
echo "    $_drop_output" | tail -1

echo "  → Creating $DB_NAME..."
timeout 120 docker exec -i "$NEW_DB_CONTAINER" psql -U "$_admin_user" -d template1 -c "CREATE DATABASE $DB_NAME OWNER $DB_USER;"  || {
    echo -e "${RED}  ✗ Failed to create database${NC}"
    rm -f "$DUMP_FILE"
    exit 1
}

# Pipe the dump into the new container
echo "  → Restoring SQL dump (117 MB)..."
set +e
_restore_output=$(cat "$DUMP_FILE" | timeout 3600 docker exec -i "$NEW_DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" )
_restore_rc=$?
set -e
if [ $_restore_rc -ne 0 ]; then
    echo -e "${RED}  ✗ Failed to restore SQL dump (exit code $_restore_rc)${NC}"
    echo -e "${YELLOW}  → Last 20 lines of error output:${NC}"
    echo "$_restore_output" | tail -20
    rm -f "$DUMP_FILE"
    exit 1
fi

echo -e "${GREEN}  ✓ Database restored to postgres-primary${NC}"

# ── Step 4: Verify migration ────────────────────────────────────────────────
echo ""
echo -e "${BLUE}→ Step 4: Verifying migration...${NC}"

NEW_TABLE_COUNT=$(timeout 120 docker exec "$NEW_DB_CONTAINER" \
    psql -U "$DB_USER" -d "$DB_NAME" -t -A \
    -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")

NEW_ROWS_VERIFY=$(timeout 120 docker exec "$NEW_DB_CONTAINER" \
    psql -U "$DB_USER" -d "$DB_NAME" -t -A \
    -c "SELECT sum(n_live_tup) FROM pg_stat_user_tables;" || echo "0")

echo -e "  Tables in postgres-primary: $NEW_TABLE_COUNT"
echo -e "  Total rows:                ${NEW_ROWS_VERIFY:-0}"

# Check critical tables exist
for tbl in auth_user accounts_organization deployments_project deployments_service; do
    EXISTS=$(timeout 120 docker exec "$NEW_DB_CONTAINER" \
        psql -U "$DB_USER" -d "$DB_NAME" -t -A \
        -c "SELECT 1 FROM information_schema.tables WHERE table_name='$tbl' LIMIT 1;"  || echo "")
    if [ "$EXISTS" = "1" ]; then
        echo -e "  ${GREEN}✓ $tbl exists${NC}"
    else
        echo -e "  ${RED}✗ $tbl MISSING${NC}"
    fi
done

# Check user count
USER_COUNT=$(timeout 120 docker exec "$NEW_DB_CONTAINER" \
    psql -U "$DB_USER" -d "$DB_NAME" -t -A \
    -c "SELECT count(*) FROM auth_user;"  || echo "0")
echo -e "  Users in database: $USER_COUNT"

if [ "${USER_COUNT:-0}" -eq 0 ]; then
    echo -e "${RED}✗ No users found — migration may have failed${NC}"
    echo -e "${YELLOW}  → Restarting backend services and aborting${NC}"
    for svc in $SVCS_TO_STOP; do
        docker compose -f "$COMPOSE_FILE" start "$svc" || echo -e "${YELLOW}    ⚠ Failed to start $svc${NC}"
    done
    exit 1
fi

echo -e "${GREEN}✓ Migration verified — $USER_COUNT users, $NEW_TABLE_COUNT tables${NC}"

# ── Step 5: Restart backend services ────────────────────────────────────────
echo ""
echo -e "${BLUE}→ Step 5: Restarting backend services...${NC}"

for svc in $SVCS_TO_STOP; do
    docker compose -f "$COMPOSE_FILE" start "$svc" || echo -e "${YELLOW}    ⚠ Failed to start $svc${NC}"
done
echo -e "${GREEN}  ✓ Backend services restarted${NC}"

# ── Step 6: Cleanup (optional) ──────────────────────────────────────────────
if [ "$DO_CLEANUP" = true ]; then
    echo ""
    echo -e "${BLUE}→ Step 6: Cleaning up old containers...${NC}"
    docker stop "$OLD_DB_CONTAINER"  && docker rm "$OLD_DB_CONTAINER"  && \
        echo -e "${GREEN}  ✓ Removed $OLD_DB_CONTAINER${NC}" || \
        echo -e "${YELLOW}  ⚠ Could not remove $OLD_DB_CONTAINER${NC}"

    docker stop "$OLD_REDIS_CONTAINER"  && docker rm "$OLD_REDIS_CONTAINER"  && \
        echo -e "${GREEN}  ✓ Removed $OLD_REDIS_CONTAINER${NC}" || \
        echo -e "${YELLOW}  ⚠ Could not remove $OLD_REDIS_CONTAINER${NC}"
else
    echo ""
    echo -e "${YELLOW}→ Old containers still running:${NC}"
    echo -e "  $OLD_DB_CONTAINER  (stop with: docker stop $OLD_DB_CONTAINER)"
    echo -e "  $OLD_REDIS_CONTAINER  (stop with: docker stop $OLD_REDIS_CONTAINER)"
    echo ""
    echo -e "${YELLOW}→ Re-run with --cleanup to remove them:${NC}"
    echo -e "  sudo bash scripts/migrate-db-to-ha.sh --cleanup"
fi

# ── Cleanup dump file ────────────────────────────────────────────────────────
rm -f "$DUMP_FILE"
echo ""
echo -e "${GREEN}═══ Migration complete ═══${NC}"
echo -e "  Dump file cleaned up."
echo -e "  pgcat will now route to postgres-primary."
echo -e "  Verify with: docker exec smsly-hosting-pgcat-1 printenv DB_HOST"
