#!/bin/bash
# SMSLY Hosting Database Backup Script
# Run this daily via cron: 0 2 * * * /opt/smsly-hosting/scripts/backup.sh

set -e

# Configuration
BACKUP_DIR="${BACKUP_DIR:-/opt/smsly-hosting/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/smsly_hosting_${TIMESTAMP}.sql.gz"

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
    # Try docker-compose container name
    DB_CONTAINER=$(docker ps --format '{{.Names}}' | grep -E "(postgres|db)" | head -1)
    if [ -z "$DB_CONTAINER" ]; then
        echo -e "${RED}Error: No PostgreSQL container found${NC}"
        exit 1
    fi
fi

echo "Using database container: $DB_CONTAINER"

# Perform backup using pg_dump inside the container
docker exec "$DB_CONTAINER" pg_dump -U smsly_admin -d smsly_hosting 2>/dev/null | gzip > "$BACKUP_FILE" || \
docker exec "$DB_CONTAINER" pg_dump -U smsly -d smsly_hosting 2>/dev/null | gzip > "$BACKUP_FILE" || \
docker exec "$DB_CONTAINER" pg_dump -U postgres -d smsly_hosting 2>/dev/null | gzip > "$BACKUP_FILE"

# Check if backup was successful
if [ -s "$BACKUP_FILE" ]; then
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo -e "${GREEN}✓ Backup created: $BACKUP_FILE ($BACKUP_SIZE)${NC}"
else
    echo -e "${RED}✗ Backup failed or empty${NC}"
    rm -f "$BACKUP_FILE"
    exit 1
fi

# Cleanup old backups
echo -e "${YELLOW}Cleaning up backups older than ${RETENTION_DAYS} days...${NC}"
find "$BACKUP_DIR" -name "smsly_hosting_*.sql.gz" -mtime +${RETENTION_DAYS} -delete
REMAINING=$(ls -1 "$BACKUP_DIR"/*.sql.gz 2>/dev/null | wc -l)
echo -e "${GREEN}✓ Cleanup complete. ${REMAINING} backups retained.${NC}"

# Log backup info
echo "[$(date)] Backup completed: $BACKUP_FILE" >> "$BACKUP_DIR/backup.log"

echo -e "\n${GREEN}Backup completed successfully!${NC}"
echo -e "File: $BACKUP_FILE"
echo -e "Size: $BACKUP_SIZE"

# Optional: Upload to remote storage (uncomment and configure)
# aws s3 cp "$BACKUP_FILE" s3://your-bucket/smsly-backups/
# rclone copy "$BACKUP_FILE" remote:smsly-backups/
