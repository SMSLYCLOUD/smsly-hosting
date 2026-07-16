#!/bin/bash
# SMSLY Hosting Database Restore Script
# Wraps the manual psql restore procedure from DISASTER_RECOVERY.md.
#
# Usage:
#   ./scripts/restore_db.sh <dump_file> [options]
#
# Options:
#   --dry-run       Validate the dump file only, do not actually restore
#   --db-name NAME  Target database name (default: smsly_hosting)
#   --db-user USER  Database user (default: postgres)
#   --force         Proceed even if the target DB already has tables
#   --help          Show this help

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

DRY_RUN=false
DB_NAME="${PSQL_RESTORE_DB_NAME:-smsly_hosting}"
DB_USER="${PSQL_RESTORE_DB_USER:-postgres}"
FORCE=false
DUMP_FILE=""
LOG_FILE="/var/log/smsly-restore.log"

usage() {
    grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \?//'
    exit "${1:-0}"
}

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --db-name) DB_NAME="$2"; shift 2 ;;
        --db-user) DB_USER="$2"; shift 2 ;;
        --force) FORCE=true; shift ;;
        --help) usage 0 ;;
        -*) echo -e "${RED}Unknown option: $1${NC}"; usage 1 ;;
        *) DUMP_FILE="$1"; shift ;;
    esac
done

# Validate DB_NAME to prevent SQL injection
if [[ ! "$DB_NAME" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
    echo -e "${RED}Error: Invalid DB_NAME '$DB_NAME'. Must match ^[a-zA-Z_][a-zA-Z0-9_]*\$${NC}"
    exit 1
fi

if [[ -z "$DUMP_FILE" ]]; then
    echo -e "${RED}Error: No dump file specified.${NC}"
    usage 1
fi

if [[ ! -f "$DUMP_FILE" ]]; then
    echo -e "${RED}Error: Dump file not found: $DUMP_FILE${NC}"
    exit 1
fi

log() {
    local ts
    ts="$(date '+%Y-%m-%d %H:%M:%S')"
    echo -e "$*"
    echo "[$ts] $*" >> "$LOG_FILE"
}

log "${YELLOW}=== Database Restore Started ===${NC}"
log "Dump file: $DUMP_FILE"
log "Target DB: $DB_NAME"
log "User: $DB_USER"
log "Dry run: $DRY_RUN"
log "Force: $FORCE"

# ── 1. Validate the dump file ──────────────────────────────────────────
log "Validating dump file..."

DUMP_SIZE=$(stat -c%s "$DUMP_FILE"  || stat -f%z "$DUMP_FILE"  || echo 0)
if [[ "$DUMP_SIZE" -eq 0 ]]; then
    log "${RED}Error: Dump file is empty.${NC}"
    exit 1
fi

log "File size: ${DUMP_SIZE} bytes"

# Check if it's a valid SQL dump or custom-format dump
HEAD=$(head -c 200 "$DUMP_FILE"  || true)
if echo "$HEAD" | grep -q '^-- PostgreSQL database dump'; then
    log "Detected: plain SQL dump"
    DUMP_TYPE="sql"
elif echo "$HEAD" | grep -q '^PGDMP'; then
    log "Detected: PostgreSQL custom-format dump"
    DUMP_TYPE="custom"
else
    log "${YELLOW}Warning: Could not detect dump format from header. Proceeding anyway.${NC}"
    DUMP_TYPE="unknown"
fi

if $DRY_RUN; then
    log "${GREEN}Dry run complete — dump file validated.${NC}"
    exit 0
fi

# ── 2. Discover PostgreSQL container ──────────────────────────────────
log "Discovering PostgreSQL container..."

DB_CONTAINER=$(docker ps --format '{{.Names}}' | grep -iE '(smsly.*postgres|postgres.*smsly)' | grep -v pgcat | head -1 || true)

if [[ -z "$DB_CONTAINER" ]]; then
    DB_CONTAINER=$(docker ps --format '{{.Names}}' | grep -i postgres | grep -v pgcat | head -1 || true)
fi

if [[ -z "$DB_CONTAINER" ]]; then
    DB_CONTAINER=$(docker ps --format '{{.Names}} {{.Image}}' | grep -i postgres | grep -v pgcat | awk '{print $1}' | head -1 || true)
fi

if [[ -z "$DB_CONTAINER" ]]; then
    log "${RED}Error: No PostgreSQL container found.${NC}"
    exit 1
fi

log "Using container: $DB_CONTAINER"

# ── 3. Check if target DB has tables ──────────────────────────────────
EXISTING_TABLES=$(timeout 10 docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';"  || echo 0)

if [[ "$EXISTING_TABLES" -gt 0 ]]; then
    if ! $FORCE; then
        log "${RED}Error: Target database '$DB_NAME' already has $EXISTING_TABLES table(s). Use --force to overwrite.${NC}"
        exit 1
    fi
    log "${YELLOW}Warning: Database '$DB_NAME' has $EXISTING_TABLES table(s). Dropping...${NC}"
fi

# ── 4. Safety backup before destructive operation ────────────────────
SAFETY_BACKUP="/tmp/smsly_pre_restore_${DB_NAME}_$(date +%Y%m%d%H%M%S).sql.gz"
log "Creating safety backup of current database..."
if timeout 300 docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME"  | gzip > "$SAFETY_BACKUP"; then
    log "${GREEN}Safety backup saved to $SAFETY_BACKUP${NC}"
else
    log "${YELLOW}WARNING: Could not create safety backup (database may not exist)${NC}"
fi

# ── 5. Drop and recreate the database ─────────────────────────────────
log "Dropping existing database..."
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -c \
    "SELECT pg_terminate_backend(pg_stat_activity.pid) FROM pg_stat_activity WHERE pg_stat_activity.datname = '$DB_NAME' AND pid <> pg_backend_pid();"  || true
docker exec "$DB_CONTAINER" dropdb -U "$DB_USER" --if-exists "$DB_NAME"  || true

log "Creating fresh database '$DB_NAME'..."
docker exec "$DB_CONTAINER" createdb -U "$DB_USER" "$DB_NAME"

# ── 6. Copy dump into container and restore ───────────────────────────
DEST="/tmp/db_dump_$$.sql"
log "Copying dump file into container..."
docker cp "$DUMP_FILE" "${DB_CONTAINER}:${DEST}"

log "Restoring database (this may take a while)..."
RESTORE_START=$(date +%s)

if [[ "$DUMP_TYPE" == "custom" ]]; then
    timeout 3600 docker exec "$DB_CONTAINER" pg_restore -U "$DB_USER" -d "$DB_NAME" \
        --no-owner --role="$DB_USER" --exit-on-error "$DEST"
else
    timeout 3600 docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" \
        --set ON_ERROR_STOP=1 -f "$DEST"
fi

# Resynchronize sequences to prevent duplicate key IntegrityErrors on future inserts
log "Resynchronizing database sequences..."
docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -c "
DO \$\$
DECLARE
    seq_record RECORD;
    max_val BIGINT;
BEGIN
    FOR seq_record IN 
        SELECT
            c.relname AS seq_name,
            t.relname AS table_name,
            a.attname AS col_name
        FROM pg_class c
        JOIN pg_depend d ON d.objid = c.oid AND d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_class'::regclass
        JOIN pg_class t ON t.oid = d.refobjid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
        WHERE c.relkind = 'S'
    LOOP
        EXECUTE format('SELECT max(%I) FROM %I', seq_record.col_name, seq_record.table_name) INTO max_val;
        IF max_val IS NOT NULL THEN
            EXECUTE format('SELECT setval(%L, %s)', seq_record.seq_name, max_val);
        ELSE
            EXECUTE format('SELECT setval(%L, 1, false)', seq_record.seq_name);
        END IF;
    END LOOP;
END \$\$;
"  || log "${YELLOW}Warning: Could not resynchronize sequences automatically${NC}"

RESTORE_END=$(date +%s)
RESTORE_DURATION=$((RESTORE_END - RESTORE_START))

# Cleanup temp file
docker exec "$DB_CONTAINER" rm -f "$DEST"

# ── 7. Verify row counts ──────────────────────────────────────────────
log "Verifying restore..."
TOTAL_ROWS=$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT sum(n_live_tup) FROM pg_stat_user_tables;"  || echo 0)
TABLE_COUNT=$(docker exec "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT count(*) FROM pg_stat_user_tables;"  || echo 0)

log "${GREEN}Restore complete!${NC}"
log "Duration: ${RESTORE_DURATION}s"
log "Tables restored: $TABLE_COUNT"
log "Total rows: $TOTAL_ROWS"

log "${GREEN}=== Database Restore Finished ===${NC}"
exit 0
