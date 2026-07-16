#!/bin/bash
# SMSLY Hosting Database Backup Script
# Run this daily via cron: 0 2 * * * /opt/smsly-hosting/scripts/backup.sh

set -uo pipefail

# Configuration
BACKUP_DIR="${BACKUP_DIR:-/opt/smsly-hosting/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/smsly_hosting_${TIMESTAMP}.sql.gz.enc"
BACKUP_PASS="${BACKUP_PASS:?BACKUP_PASS environment variable must be set}"
export BACKUP_PASS
BACKUP_SUCCESS=0

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

echo -e "${YELLOW}[$(date)] Starting database backup...${NC}"

# Get database credentials from running container
DB_CONTAINER="smsly-postgres"

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${DB_CONTAINER}$"; then
    DB_CONTAINER=$(docker ps --format '{{.Names}}' | grep -E "(postgres|db)" | head -1)
    if [ -z "$DB_CONTAINER" ]; then
        echo -e "${RED}Error: No PostgreSQL container found${NC}"
        exit 1
    fi
fi

echo "Using database container: $DB_CONTAINER"

# Perform backup trying each database user
DUMP_OK=false
for DB_USER in smsly_admin smsly postgres; do
    echo "Attempting pg_dump with user '${DB_USER}'..."
    if timeout 300 sh -c 'docker exec "$1" pg_dump -U "$2" -d smsly_hosting | gzip | openssl enc -aes-256-cbc -salt -pbkdf2 -pass env:BACKUP_PASS -md sha256' _ "$DB_CONTAINER" "$DB_USER" > "$BACKUP_FILE"; then
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

# Cleanup old backups
echo -e "${YELLOW}Cleaning up backups older than ${RETENTION_DAYS} days...${NC}"
find "$BACKUP_DIR" \( -name "smsly_hosting_*.sql.gz" -o -name "smsly_hosting_*.sql.gz.enc" \) -mtime +${RETENTION_DAYS} -delete
REMAINING=$(find "$BACKUP_DIR" \( -name "smsly_hosting_*.sql.gz" -o -name "smsly_hosting_*.sql.gz.enc" \)  | wc -l)
echo -e "${GREEN}✓ Cleanup complete. ${REMAINING} backups retained.${NC}"

# Log backup info
echo "[$(date)] Backup completed: $BACKUP_FILE" >> "$BACKUP_DIR/backup.log"

echo -e "\n${GREEN}Backup completed successfully!${NC}"
echo -e "File: $BACKUP_FILE"
echo -e "Size: $BACKUP_SIZE"

# Optional: Upload to remote storage (uncomment and configure)
if command -v aws ; then
    aws s3 cp "$BACKUP_FILE" s3://your-bucket/smsly-backups/ || echo -e "${RED}✗ S3 upload failed${NC}"
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
