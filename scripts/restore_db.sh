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
umask 077

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

DRY_RUN=false
ENV_FILE="${ENV_FILE:-/opt/smsly-hosting/.env}"
INSTALL_DIR="$(dirname "$ENV_FILE")"
ENV_COMPOSE_FILE=""
ENV_POSTGRES_USER=""
ENV_POSTGRES_DB=""
ENV_POSTGRES_PASSWORD=""
ENV_BACKUP_ENCRYPTION_KEY=""

if [[ -f "$ENV_FILE" ]]; then
    while IFS= read -r _env_line || [[ -n "$_env_line" ]]; do
        _env_line="${_env_line%$'\r'}"
        [[ -z "$_env_line" || "$_env_line" =~ ^[[:space:]]*# ]] && continue
        [[ "$_env_line" == export[[:space:]]* ]] && _env_line="${_env_line#export }"
        [[ "$_env_line" == *=* ]] || continue
        _env_key="${_env_line%%=*}"
        _env_key="${_env_key#"${_env_key%%[![:space:]]*}"}"
        _env_key="${_env_key%"${_env_key##*[![:space:]]}"}"
        _env_val="${_env_line#*=}"
        if [[ "$_env_val" == \"*\" && "$_env_val" == *\" && ${#_env_val} -ge 2 ]]; then
            _env_val="${_env_val:1:${#_env_val}-2}"
        elif [[ "$_env_val" == \'*\' && "$_env_val" == *\' && ${#_env_val} -ge 2 ]]; then
            _env_val="${_env_val:1:${#_env_val}-2}"
        fi
        case "$_env_key" in
            COMPOSE_FILE) ENV_COMPOSE_FILE="$_env_val" ;;
            POSTGRES_USER) ENV_POSTGRES_USER="$_env_val" ;;
            POSTGRES_DB) ENV_POSTGRES_DB="$_env_val" ;;
            POSTGRES_PASSWORD) ENV_POSTGRES_PASSWORD="$_env_val" ;;
            BACKUP_ENCRYPTION_KEY) ENV_BACKUP_ENCRYPTION_KEY="$_env_val" ;;
        esac
    done < "$ENV_FILE"
    unset _env_line _env_key _env_val
fi

COMPOSE_FILE="${COMPOSE_FILE:-${ENV_COMPOSE_FILE:-$INSTALL_DIR/docker-compose.prod.yml}}"
case "$COMPOSE_FILE" in
    /*) ;;
    *) COMPOSE_FILE="$INSTALL_DIR/$COMPOSE_FILE" ;;
esac
COMPOSE_ENV_ARGS=()
[[ -f "$ENV_FILE" ]] && COMPOSE_ENV_ARGS=(--env-file "$ENV_FILE")
STOPPED_APP_SERVICES=()
SERVICES_STOPPED=false

DB_NAME="${PSQL_RESTORE_DB_NAME:-${ENV_POSTGRES_DB:-smsly_hosting}}"
if [[ -f "$ENV_FILE" ]]; then
    DB_USER="${PSQL_RESTORE_DB_USER:-${ENV_POSTGRES_USER:-smsly_admin}}"
else
    DB_USER="${PSQL_RESTORE_DB_USER:-postgres}"
fi
BACKUP_PASS="${BACKUP_PASS:-${ENV_BACKUP_ENCRYPTION_KEY:-}}"
DB_PASSWORD="${POSTGRES_PASSWORD:-${ENV_POSTGRES_PASSWORD:-}}"
PG_ENV_ARGS=()
[[ -n "${DB_PASSWORD:-}" ]] && PG_ENV_ARGS=(-e "PGPASSWORD=$DB_PASSWORD")
FORCE=false
DUMP_FILE=""
WORK_DUMP=""
LOG_FILE="/var/log/smsly-restore.log"

restart_app_services() {
    if [[ "$SERVICES_STOPPED" == "true" && ${#STOPPED_APP_SERVICES[@]} -gt 0 && -f "$COMPOSE_FILE" ]]; then
        log "Restarting stopped platform services: ${STOPPED_APP_SERVICES[*]}..."
        timeout 120 docker compose "${COMPOSE_ENV_ARGS[@]}" -f "$COMPOSE_FILE" start "${STOPPED_APP_SERVICES[@]}" || \
            log "${YELLOW}Warning: Failed to restart some services (${STOPPED_APP_SERVICES[*]}). Run docker compose up -d manually.${NC}"
        SERVICES_STOPPED=false
    fi
}

cleanup_temp() {
    restart_app_services
    if [[ -n "${WORK_DUMP:-}" && -f "$WORK_DUMP" ]]; then
        rm -f "$WORK_DUMP"
    fi
}
trap cleanup_temp EXIT

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

# Validate DB_NAME and DB_USER to prevent SQL injection
if [[ ! "$DB_NAME" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
    echo -e "${RED}Error: Invalid DB_NAME '$DB_NAME'. Must match ^[a-zA-Z_][a-zA-Z0-9_]*\$${NC}"
    exit 1
fi
if [[ ! "$DB_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
    echo -e "${RED}Error: Invalid DB_USER '$DB_USER'. Must match ^[a-zA-Z_][a-zA-Z0-9_]*\$${NC}"
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

RESTORE_SOURCE="$DUMP_FILE"

# Decrypt (.enc) and/or decompress (.gz) into a 0600 temp file BEFORE
# validating headers or touching the live database.
if [[ "$DUMP_FILE" == *.enc ]]; then
    if [[ -z "${BACKUP_PASS:-}" ]]; then
        log "${RED}Error: Encrypted dump requires BACKUP_PASS or BACKUP_ENCRYPTION_KEY in $ENV_FILE.${NC}"
        exit 1
    fi
    export BACKUP_PASS
    WORK_DUMP="$(mktemp /tmp/smsly_restore_XXXXXX)"
    chmod 600 "$WORK_DUMP"
    log "Decrypting dump file..."
    if ! openssl enc -d -aes-256-cbc -pbkdf2 -md sha256 -pass env:BACKUP_PASS -in "$DUMP_FILE" -out "$WORK_DUMP" 2>/dev/null; then
        log "${RED}Error: Decryption failed for $DUMP_FILE. Aborting before touching live database.${NC}"
        exit 1
    fi
    if [[ "$DUMP_FILE" == *.gz.enc ]] || gzip -t "$WORK_DUMP" 2>/dev/null; then
        DECOMPRESSED_TMP="$(mktemp /tmp/smsly_restore_dec_XXXXXX)"
        chmod 600 "$DECOMPRESSED_TMP"
        log "Decompressing decrypted dump file..."
        if ! gunzip -c "$WORK_DUMP" > "$DECOMPRESSED_TMP" 2>/dev/null; then
            rm -f "$DECOMPRESSED_TMP"
            log "${RED}Error: Decompression failed for $DUMP_FILE. Aborting before touching live database.${NC}"
            exit 1
        fi
        mv -f "$DECOMPRESSED_TMP" "$WORK_DUMP"
        chmod 600 "$WORK_DUMP"
    fi
    RESTORE_SOURCE="$WORK_DUMP"
elif [[ "$DUMP_FILE" == *.gz ]]; then
    WORK_DUMP="$(mktemp /tmp/smsly_restore_XXXXXX)"
    chmod 600 "$WORK_DUMP"
    log "Decompressing dump file..."
    if ! gunzip -c "$DUMP_FILE" > "$WORK_DUMP" 2>/dev/null; then
        log "${RED}Error: Decompression failed for $DUMP_FILE. Aborting before touching live database.${NC}"
        exit 1
    fi
    RESTORE_SOURCE="$WORK_DUMP"
fi

if [[ ! -s "$RESTORE_SOURCE" ]]; then
    log "${RED}Error: Decrypted/decompressed dump is empty. Aborting before touching live database.${NC}"
    exit 1
fi

# Check if it's a valid SQL dump or custom-format dump
HEAD=$(head -c 200 "$RESTORE_SOURCE" || true)
if echo "$HEAD" | grep -q '^-- PostgreSQL database dump'; then
    log "Detected: plain SQL dump"
    DUMP_TYPE="sql"
elif echo "$HEAD" | grep -q '^PGDMP'; then
    log "Detected: PostgreSQL custom-format dump"
    DUMP_TYPE="custom"
else
    log "${RED}Error: Unknown or invalid dump format header. Aborting before touching live database.${NC}"
    exit 1
fi

if $DRY_RUN; then
    log "${GREEN}Dry run complete — dump file validated.${NC}"
    exit 0
fi

# ── 2. Discover PostgreSQL container ──────────────────────────────────
log "Discovering PostgreSQL container..."

DB_CONTAINER=$(docker ps --format '{{.Names}}' | grep -iE '(smsly.*postgres|postgres.*smsly)' | grep -vE 'pgcat|replica' | head -1 || true)

if [[ -z "$DB_CONTAINER" ]]; then
    DB_CONTAINER=$(docker ps --format '{{.Names}}' | grep -i postgres | grep -vE 'pgcat|replica' | head -1 || true)
fi

if [[ -z "$DB_CONTAINER" ]]; then
    DB_CONTAINER=$(docker ps --format '{{.Names}} {{.Image}}' | grep -i postgres | grep -vE 'pgcat|replica' | awk '{print $1}' | head -1 || true)
fi

if [[ -z "$DB_CONTAINER" ]]; then
    log "${RED}Error: No PostgreSQL container found.${NC}"
    exit 1
fi

log "Using container: $DB_CONTAINER"

# Verify target container is primary (not a read replica)
IS_RECOVERY=$(timeout 10 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -tAc "SELECT pg_is_in_recovery();" 2>/dev/null | tr -d '[:space:]' || echo "unknown")
if [[ "$IS_RECOVERY" != "f" ]]; then
    log "${RED}Error: Container '$DB_CONTAINER' is not a writable primary (pg_is_in_recovery='$IS_RECOVERY'). Aborting restore.${NC}"
    exit 1
fi

# ── 3. Check if target DB has tables ──────────────────────────────────
EXISTING_TABLES=$(timeout 10 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public';"  || echo 0)

if [[ "$EXISTING_TABLES" -gt 0 ]]; then
    if ! $FORCE; then
        log "${RED}Error: Target database '$DB_NAME' already has $EXISTING_TABLES table(s). Use --force to overwrite.${NC}"
        exit 1
    fi
    log "${YELLOW}Warning: Database '$DB_NAME' has $EXISTING_TABLES table(s). Dropping...${NC}"
fi

# ── 4. Safety backup before destructive operation ────────────────────
mkdir -p "$INSTALL_DIR/backups"
chmod 700 "$INSTALL_DIR/backups" 2>/dev/null || true
SAFETY_BACKUP="$INSTALL_DIR/backups/smsly_pre_restore_${DB_NAME}_$(date +%Y%m%d%H%M%S).sql.gz"
log "Creating safety backup of current database..."
if timeout 300 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" pg_dump -U "$DB_USER" "$DB_NAME"  | gzip > "$SAFETY_BACKUP"; then
    chmod 600 "$SAFETY_BACKUP"
    log "${GREEN}Safety backup saved to $SAFETY_BACKUP${NC}"
else
    rm -f "$SAFETY_BACKUP"
    log "${YELLOW}WARNING: Could not create safety backup (database may not exist)${NC}"
fi

# ── 5. Stop writers/poolers, restore into TMP_DB, then atomically swap ──
if [[ -f "$COMPOSE_FILE" ]]; then
    _running_svcs="$(timeout 30 docker compose "${COMPOSE_ENV_ARGS[@]}" -f "$COMPOSE_FILE" ps --status running --services 2>/dev/null || true)"
    for _svc in pgcat backend celery celery-deploy celery-fast celery-beat celery-worker; do
        if printf '%s\n' "$_running_svcs" | grep -qx "$_svc"; then
            STOPPED_APP_SERVICES+=("$_svc")
        fi
    done
    if [[ ${#STOPPED_APP_SERVICES[@]} -gt 0 ]]; then
        log "Stopping active writers/poolers before restore: ${STOPPED_APP_SERVICES[*]}..."
        timeout 60 docker compose "${COMPOSE_ENV_ARGS[@]}" -f "$COMPOSE_FILE" stop -t 15 "${STOPPED_APP_SERVICES[@]}" || true
        SERVICES_STOPPED=true
    fi
fi

TMP_DB="${DB_NAME}_restore_tmp"
OLD_DB="${DB_NAME}_pre_restore_old"

log "Preparing temporary database '$TMP_DB'..."
timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$TMP_DB' AND pid <> pg_backend_pid();" || true
timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" dropdb -U "$DB_USER" --if-exists "$TMP_DB" || true
timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" createdb -U "$DB_USER" "$TMP_DB"

# ── 6. Copy dump into container and restore into TMP_DB ───────────────
DEST="/tmp/db_dump_$$.sql"
log "Copying dump file into container..."
timeout 120 docker cp "$RESTORE_SOURCE" "${DB_CONTAINER}:${DEST}"

log "Restoring database into '$TMP_DB' (this may take a while)..."
RESTORE_START=$(date +%s)

RESTORE_RC=0
if [[ "$DUMP_TYPE" == "custom" ]]; then
    timeout 3600 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" pg_restore -U "$DB_USER" -d "$TMP_DB" \
        --no-owner --role="$DB_USER" --exit-on-error "$DEST" || RESTORE_RC=$?
else
    timeout 3600 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d "$TMP_DB" \
        --set ON_ERROR_STOP=1 -f "$DEST" || RESTORE_RC=$?
fi

timeout 30 docker exec "$DB_CONTAINER" rm -f "$DEST" || true

if [[ "$RESTORE_RC" -ne 0 ]]; then
    log "${RED}Error: Restore into '$TMP_DB' failed (exit $RESTORE_RC). Live database '$DB_NAME' untouched.${NC}"
    timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" dropdb -U "$DB_USER" --if-exists "$TMP_DB" || true
    exit 1
fi

# Resynchronize sequences to prevent duplicate key IntegrityErrors on future inserts
log "Resynchronizing database sequences..."
timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d "$TMP_DB" -c "
DO \$\$
DECLARE
    seq_record RECORD;
    max_val BIGINT;
BEGIN
    FOR seq_record IN
        SELECT
            sn.nspname AS seq_schema,
            c.relname AS seq_name,
            tn.nspname AS table_schema,
            t.relname AS table_name,
            a.attname AS col_name
        FROM pg_class c
        JOIN pg_namespace sn ON sn.oid = c.relnamespace
        JOIN pg_depend d ON d.objid = c.oid AND d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_class'::regclass
        JOIN pg_class t ON t.oid = d.refobjid
        JOIN pg_namespace tn ON tn.oid = t.relnamespace
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
        WHERE c.relkind = 'S'
    LOOP
        EXECUTE format('SELECT max(%I) FROM %I.%I', seq_record.col_name, seq_record.table_schema, seq_record.table_name) INTO max_val;
        IF max_val IS NOT NULL THEN
            EXECUTE format('SELECT setval(%L, %s)', format('%I.%I', seq_record.seq_schema, seq_record.seq_name), max_val);
        ELSE
            EXECUTE format('SELECT setval(%L, 1, false)', format('%I.%I', seq_record.seq_schema, seq_record.seq_name));
        END IF;
    END LOOP;
END \$\$;
"  || log "${YELLOW}Warning: Could not resynchronize sequences automatically${NC}"

log "Atomically swapping '$TMP_DB' -> '$DB_NAME'..."
DB_EXISTS=$(timeout 10 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME';" || echo 0)
if [[ "$DB_EXISTS" == "1" ]]; then
    timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -c \
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('$DB_NAME', '$OLD_DB', '$TMP_DB') AND pid <> pg_backend_pid();" || true
    timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" dropdb -U "$DB_USER" --if-exists "$OLD_DB" || true
    timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 -c \
        "ALTER DATABASE \"$DB_NAME\" RENAME TO \"$OLD_DB\";"
    if ! timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 -c \
        "ALTER DATABASE \"$TMP_DB\" RENAME TO \"$DB_NAME\";"; then
        log "${RED}Error: Failed to rename '$TMP_DB' to '$DB_NAME' — restoring '$OLD_DB'${NC}"
        timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -c \
            "ALTER DATABASE \"$OLD_DB\" RENAME TO \"$DB_NAME\";" || true
        exit 1
    fi
    timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" dropdb -U "$DB_USER" --if-exists "$OLD_DB" || true
else
    timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -c \
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$TMP_DB' AND pid <> pg_backend_pid();" || true
    timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 -c \
        "ALTER DATABASE \"$TMP_DB\" RENAME TO \"$DB_NAME\";"
fi

RESTORE_END=$(date +%s)
RESTORE_DURATION=$((RESTORE_END - RESTORE_START))

restart_app_services

# ── 7. Verify row counts ──────────────────────────────────────────────
log "Verifying restore..."
TOTAL_ROWS=$(timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT sum(n_live_tup) FROM pg_stat_user_tables;"  || echo 0)
TABLE_COUNT=$(timeout 120 docker exec ${PG_ENV_ARGS[@]+"${PG_ENV_ARGS[@]}"} "$DB_CONTAINER" psql -U "$DB_USER" -d "$DB_NAME" -tAc \
    "SELECT count(*) FROM pg_stat_user_tables;"  || echo 0)

log "${GREEN}Restore complete!${NC}"
log "Duration: ${RESTORE_DURATION}s"
log "Tables restored: $TABLE_COUNT"
log "Total rows: $TOTAL_ROWS"

log "${GREEN}=== Database Restore Finished ===${NC}"
exit 0
