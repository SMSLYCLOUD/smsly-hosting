#!/bin/bash
# SMSLY Hosting Database Backup Script
# Scheduled daily via smsly-backup.timer (installed by the fresh installer
# and refresh/update flows). Manual run:
#   sudo BACKUP_PASS=<secret> ./scripts/backup.sh
# When run from the timer, BACKUP_PASS falls back to BACKUP_ENCRYPTION_KEY
# from /opt/smsly-hosting/.env (see scripts/smsly-backup.service).

set -uo pipefail

umask 077

# Load deployment .env safely (line-by-line, no shell execution) so
# BACKUP_ENCRYPTION_KEY / POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD
# reflect the real config while preserving caller-supplied env vars.
if [ -f /opt/smsly-hosting/.env ]; then
    while IFS= read -r _env_line || [ -n "$_env_line" ]; do
        _env_line="${_env_line%$'\r'}"
        [[ -z "$_env_line" || "$_env_line" =~ ^[[:space:]]*# ]] && continue
        [[ "$_env_line" == export[[:space:]]* ]] && _env_line="${_env_line#export }"
        [[ "$_env_line" == *=* ]] || continue
        _env_key="${_env_line%%=*}"
        _env_key="${_env_key#"${_env_key%%[![:space:]]*}"}"
        _env_key="${_env_key%"${_env_key##*[![:space:]]}"}"
        [[ "$_env_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
        _env_val="${_env_line#*=}"
        if [[ "$_env_val" == \"*\" && "$_env_val" == *\" && ${#_env_val} -ge 2 ]]; then
            _env_val="${_env_val:1:${#_env_val}-2}"
        elif [[ "$_env_val" == \'*\' && "$_env_val" == *\' && ${#_env_val} -ge 2 ]]; then
            _env_val="${_env_val:1:${#_env_val}-2}"
        fi
        if [ -z "${!_env_key+x}" ]; then
            export "$_env_key=$_env_val"
        fi
    done < /opt/smsly-hosting/.env
    unset _env_line _env_key _env_val
fi

# Configuration
BACKUP_DIR="${BACKUP_DIR:-/opt/smsly-hosting/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/smsly_hosting_${TIMESTAMP}.sql.gz.enc"
BACKUP_PASS="${BACKUP_PASS:-${BACKUP_ENCRYPTION_KEY:-}}"
BACKUP_PASS="${BACKUP_PASS:?BACKUP_PASS environment variable must be set}"
export BACKUP_PASS
BACKUP_SUCCESS=0
BACKUP_SIZE="0B"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

echo -e "${YELLOW}[$(date)] Starting database backup...${NC}"

# Get database credentials from running container (primary first, then any
# postgres/patroni/db container — covers local-ha, patroni, and legacy names).
DB_CONTAINER="${DB_CONTAINER:-smsly-postgres-primary}"

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${DB_CONTAINER}$"; then
    DB_CONTAINER=$(docker ps --format '{{.Names}}' | grep -E "^(smsly-postgres-primary|smsly-patroni-[0-9]+|smsly-hosting-db-1)$" | head -1)
    if [ -z "$DB_CONTAINER" ]; then
        echo -e "${RED}Error: No PostgreSQL container found${NC}"
        exit 1
    fi
fi

echo "Using database container: $DB_CONTAINER"

# Perform backup trying each database user
DUMP_OK=false
if [ -n "${POSTGRES_PASSWORD:-}" ]; then
    export PGPASSWORD="${POSTGRES_PASSWORD}"
fi
for DB_USER in "${POSTGRES_USER:-smsly_admin}" smsly postgres; do
    echo "Attempting pg_dump with user '${DB_USER}'..."
    if timeout 300 bash -o pipefail -c 'docker exec ${PGPASSWORD:+-e PGPASSWORD} "$1" pg_dump -U "$2" -d "$3" | gzip | openssl enc -aes-256-cbc -salt -pbkdf2 -pass env:BACKUP_PASS -md sha256' _ "$DB_CONTAINER" "$DB_USER" "${POSTGRES_DB:-smsly_hosting}" > "$BACKUP_FILE"; then
        DUMP_OK=true
        break
    fi
done

# Check if backup was successful (size > 1KB to ensure it contains real schema/data not just gzip header)
if [ "$DUMP_OK" = "true" ] && [ -s "$BACKUP_FILE" ] && [ "$(stat -c%s "$BACKUP_FILE"  || stat -f%z "$BACKUP_FILE"  || echo 0)" -gt 500 ]; then
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo -e "${GREEN}✓ Backup created: $BACKUP_FILE ($BACKUP_SIZE)${NC}"
    BACKUP_SUCCESS=1
else
    echo -e "${RED}✗ Backup failed or empty${NC}"
    rm -f "$BACKUP_FILE"
fi

if [ "$BACKUP_SUCCESS" -eq 1 ]; then
    # Cleanup old backups
    echo -e "${YELLOW}Cleaning up backups older than ${RETENTION_DAYS} days...${NC}"
    find "$BACKUP_DIR" \( -name "smsly_hosting_*.sql.gz" -o -name "smsly_hosting_*.sql.gz.enc" \) -mtime +${RETENTION_DAYS} -delete
    REMAINING=$(find "$BACKUP_DIR" \( -name "smsly_hosting_*.sql.gz" -o -name "smsly_hosting_*.sql.gz.enc" \)  | wc -l)
    echo -e "${GREEN}✓ Cleanup complete. ${REMAINING} backups retained.${NC}"

    # Log backup info
    echo "[$(date)] Backup completed: $BACKUP_FILE ($BACKUP_SIZE)" >> "$BACKUP_DIR/backup.log"

    echo -e "\n${GREEN}Backup completed successfully!${NC}"
    echo -e "File: $BACKUP_FILE"
    echo -e "Size: $BACKUP_SIZE"

    # Optional: Upload to remote S3 storage when BACKUP_S3_BUCKET is configured
    if [ -n "${BACKUP_S3_BUCKET:-}" ] && command -v aws >/dev/null 2>&1; then
        S3_DEST="s3://${BACKUP_S3_BUCKET#s3://}"
        S3_DEST="${S3_DEST%/}/"
        aws s3 cp "$BACKUP_FILE" "${S3_DEST}$(basename "$BACKUP_FILE")" || echo -e "${RED}✗ S3 upload failed${NC}"
    fi
else
    echo "[$(date)] Backup FAILED: $BACKUP_FILE" >> "$BACKUP_DIR/backup.log"
fi
# rclone copy "$BACKUP_FILE" remote:smsly-backups/

# ── Healthcheck ping ─────────────────────────────────────────────────
if [ -n "${BACKUP_HEALTHCHECK_URL:-}" ]; then
    if [ "$BACKUP_SUCCESS" -eq 1 ]; then
        curl -fsS --retry 3 --max-time 10 "${BACKUP_HEALTHCHECK_URL}"  || true
    else
        curl -fsS --retry 3 --max-time 10 "${BACKUP_HEALTHCHECK_URL}/fail"  || true
    fi
fi

exit $((1 - BACKUP_SUCCESS))
