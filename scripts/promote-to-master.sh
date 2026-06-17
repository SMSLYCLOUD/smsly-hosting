#!/bin/bash
# =============================================================================
# SMSLY Disaster Recovery: Promote Lite Agent to Master
# =============================================================================
# Usage:
#   sudo bash scripts/promote-to-master.sh /opt/smsly-hosting/backups/master-db/master_db_20260617_120000.sql.gz
#
# If no backup path is given, the latest backup in
# /opt/smsly-hosting/backups/master-db/ is used automatically.
#
# What this does:
#   1. Stops the lite agent stack (docker-compose.agent-lite.yml)
#   2. Recovers FIELD_ENCRYPTION_KEY and GATEWAY_SECRET from the agent's .env
#   3. Starts a PostgreSQL container and restores the master DB backup
#   4. Starts the master stack: Caddy, Registry, Backend, Celery, Frontend
#   5. Creates a new ManagedServer record for the promoted node
#   6. Outputs connection info for other agents to re-point
# =============================================================================

set -Eeuo pipefail
IFS=$'\n\t'

INSTALL_DIR="/opt/smsly-hosting"
BACKUP_DIR="$INSTALL_DIR/backups/master-db"
COMPOSE_FILE="docker-compose.prod.yml"
AGENT_COMPOSE="infrastructure/docker/docker-compose.agent-lite.yml"
LOG_FILE="/var/log/smsly-promote.log"
NEW_MASTER_IP=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $1" | tee -a "$LOG_FILE"; }
ok()   { echo -e "${GREEN}[✓]${NC} $1" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[!]${NC} $1" | tee -a "$LOG_FILE"; }
fail() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

check_root() { [ "$EUID" -eq 0 ] || fail "Must run as root. Use: sudo bash $0"; }

# ──────────────────────────────────────────────────────────────────────
# STEP 0: Pre-flight checks
# ──────────────────────────────────────────────────────────────────────
check_root()
cd "$INSTALL_DIR"

# Determine backup path
BACKUP_PATH="${1:-}"
if [ -z "$BACKUP_PATH" ]; then
    BACKUP_PATH=$(ls -t "$BACKUP_DIR"/master_db_*.sql.gz 2>/dev/null | head -1)
    [ -n "$BACKUP_PATH" ] || fail "No backup found in $BACKUP_DIR and no path was given."
fi
[ -f "$BACKUP_PATH" ] || fail "Backup file not found: $BACKUP_PATH"
BACKUP_SIZE=$(du -h "$BACKUP_PATH" | cut -f1)
log "Using backup: $BACKUP_PATH ($BACKUP_SIZE)"

# Verify .env exists
[ -f ".env" ] || fail ".env not found at $INSTALL_DIR/.env"

# ──────────────────────────────────────────────────────────────────────
# STEP 1: Recover critical secrets from the agent's .env
# ──────────────────────────────────────────────────────────────────────
log "Recovering secrets from .env..."

FIELD_ENCRYPTION_KEY=$(grep -m1 '^FIELD_ENCRYPTION_KEY=' .env 2>/dev/null | cut -d= -f2- || echo "")
GATEWAY_SECRET=$(grep -m1 '^GATEWAY_SECRET=' .env 2>/dev/null | cut -d= -f2- || echo "")
SECRET_KEY=$(grep -m1 '^SECRET_KEY=' .env 2>/dev/null | cut -d= -f2- || echo "")

# Fallback: master passes these as MASTER_* vars to the agent
[ -z "$FIELD_ENCRYPTION_KEY" ] && FIELD_ENCRYPTION_KEY=$(grep -m1 '^MASTER_FIELD_ENCRYPTION_KEY=' .env 2>/dev/null | cut -d= -f2- || echo "")
[ -z "$GATEWAY_SECRET" ] && GATEWAY_SECRET=$(grep -m1 '^MASTER_GATEWAY_SECRET=' .env 2>/dev/null | cut -d= -f2- || echo "")

[ -z "$FIELD_ENCRYPTION_KEY" ] && fail "FIELD_ENCRYPTION_KEY not found in .env (required for DB decryption)"
[ -z "$SECRET_KEY" ] && fail "SECRET_KEY not found in .env"

if [ -z "$GATEWAY_SECRET" ]; then
    warn "GATEWAY_SECRET not found — will generate a new one. Other agents will need re-provisioning."
    GATEWAY_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
fi

# Recover DB credentials from the backup metadata
DB_USER=$(grep -m1 '^MASTER_DB_USER=' .env 2>/dev/null | cut -d= -f2- || echo "smsly_admin")
DB_PASSWORD=$(grep -m1 '^MASTER_DB_PASSWORD=' .env 2>/dev/null | cut -d= -f2- || echo "smsly_password")
DB_NAME=$(grep -m1 '^POSTGRES_DB=' .env 2>/dev/null | cut -d= -f2- || echo "smsly_hosting")

ok "Secrets recovered"

# ──────────────────────────────────────────────────────────────────────
# STEP 2: Stop the lite agent stack
# ──────────────────────────────────────────────────────────────────────
log "Stopping lite agent stack..."
docker compose -f "$AGENT_COMPOSE" down --remove-orphans 2>/dev/null || true
ok "Agent stack stopped"

# ──────────────────────────────────────────────────────────────────────
# STEP 3: Install Postgres and restore the backup
# ──────────────────────────────────────────────────────────────────────
log "Starting PostgreSQL and restoring DB backup..."

# Remove any existing Postgres container from a previous run
docker rm -f smsly-db 2>/dev/null || true
docker volume rm -f postgres_data 2>/dev/null || true

# Run Postgres from the master compose, but don't start the rest yet
docker compose -f "$COMPOSE_FILE" up -d db
ok "PostgreSQL started"

# Wait for Postgres to be ready
log "Waiting for PostgreSQL to accept connections..."
for i in $(seq 1 30); do
    if docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U "$DB_USER" 2>/dev/null; then
        ok "PostgreSQL is ready"
        break
    fi
    [ "$i" -eq 30 ] && fail "PostgreSQL failed to start within 60s"
    sleep 2
done

# Restore the backup
log "Restoring master DB backup (this may take a while)..."
gunzip -c "$BACKUP_PATH" | docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$DB_USER" -d "$DB_NAME" 2>&1 | tail -5
ok "Database restored from backup"

# ──────────────────────────────────────────────────────────────────────
# STEP 4: Build the new master's .env
# ──────────────────────────────────────────────────────────────────────
log "Preparing master .env..."

# Resolve the new master's IP
NEW_MASTER_IP=$(curl -sS --max-time 5 http://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
NEW_MASTER_MESH_IP="${NEW_MASTER_IP}"  # Will be updated after WireGuard setup

# Update .env with master settings
env_set() {
    local key="$1" val="$2" file="$3"
    if grep -q "^${key}=" "$file" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$file"
    else
        echo "${key}=${val}" >> "$file"
    fi
}

env_set "DOMAIN" "" .env
env_set "SERVER_IP" "$NEW_MASTER_IP" .env
env_set "SECRET_KEY" "$SECRET_KEY" .env
env_set "FIELD_ENCRYPTION_KEY" "$FIELD_ENCRYPTION_KEY" .env
env_set "GATEWAY_SECRET" "$GATEWAY_SECRET" .env
env_set "USE_SSL" "false" .env
env_set "DISABLE_HTTPS_REDIRECT" "true" .env
env_set "SMSLY_DISABLE_TIER_GATES" "true" .env

# Database URL — local Postgres
env_set "DATABASE_URL" "postgresql://${DB_USER}:${DB_PASSWORD}@db:5432/${DB_NAME}" .env
env_set "POSTGRES_USER" "$DB_USER" .env
env_set "POSTGRES_PASSWORD" "$DB_PASSWORD" .env
env_set "POSTGRES_DB" "$DB_NAME" .env

ok "Master .env configured"

# ──────────────────────────────────────────────────────────────────────
# STEP 5: Start the master stack
# ──────────────────────────────────────────────────────────────────────
log "Starting master services..."

# Start registry first (needed by build pipeline)
docker compose -f "$COMPOSE_FILE" up -d registry
ok "Docker registry started"

# Start remaining core services (skip db — it's already running)
docker compose -f "$COMPOSE_FILE" up -d \
    socket-proxy redis rabbitmq traefik caddy backend celery celery-beat celery-fast celery-deploy frontend
ok "Master stack started"

# ──────────────────────────────────────────────────────────────────────
# STEP 6: Run migrations
# ──────────────────────────────────────────────────────────────────────
log "Running database migrations..."
docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py migrate --noinput 2>&1 | tail -3
ok "Migrations complete"

# ──────────────────────────────────────────────────────────────────────
# STEP 7: Verify the new master
# ──────────────────────────────────────────────────────────────────────
log "Verifying new master..."
sleep 10
HEALTH=$(curl -sS --max-time 10 http://localhost:8000/health/live 2>/dev/null || echo "unreachable")
if [ "$HEALTH" = "unreachable" ]; then
    warn "Health check failed — check backend logs: docker compose logs backend"
else
    ok "Backend API is healthy: $HEALTH"
fi

# ──────────────────────────────────────────────────────────────────────
# STEP 8: Output connection info for other agents
# ──────────────────────────────────────────────────────────────────────
echo ""
echo "============================================"
echo -e "${GREEN}  PROMOTION COMPLETE${NC}"
echo "============================================"
echo ""
echo "New Master IP:       $NEW_MASTER_IP"
echo "New Master Mesh IP:  $NEW_MASTER_MESH_IP"
echo "GATEWAY_SECRET:      $GATEWAY_SECRET"
echo "DB_USER:             $DB_USER"
echo "DB_NAME:             $DB_NAME"
echo ""
echo "To re-point other agents, run on the new master:"
echo ""
echo "  python manage.py repoint_agents \\"
echo "    --master-ip=$NEW_MASTER_IP \\"
echo "    --gateway-secret=$GATEWAY_SECRET"
echo ""

# Save connection info for the repoint command
cat > "$INSTALL_DIR/.promoted-master.json" << EOF
{
    "master_ip": "$NEW_MASTER_IP",
    "master_mesh_ip": "$NEW_MASTER_MESH_IP",
    "gateway_secret": "$GATEWAY_SECRET",
    "db_user": "$DB_USER",
    "db_password": "$DB_PASSWORD",
    "db_name": "$DB_NAME",
    "promoted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
ok "Promotion metadata saved to $INSTALL_DIR/.promoted-master.json"
