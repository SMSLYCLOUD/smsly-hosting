#!/bin/bash
# =============================================================================
# SMSLY Disaster Recovery: Promote Lite Agent to Master
# =============================================================================
# Usage:
#   sudo bash scripts/promote-to-master.sh
#   sudo bash scripts/promote-to-master.sh --db-url=postgresql://user:pass@db-host:5432/smsly_hosting
#
# What this does:
#   1. Stops the lite agent stack (docker-compose.agent-lite.yml)
#   2. Recovers FIELD_ENCRYPTION_KEY and GATEWAY_SECRET from the agent's .env
#   3. Connects to the existing database (main/replica) OR starts local
#      Postgres and restores the latest agent-stored backup (fallback)
#   4. Starts the master stack: Caddy, Registry, Backend, Celery, Frontend
#   5. Runs migrations on the (potentially stale) database
#   6. Saves promotion metadata for re-pointing other agents
#
# Database priority:
#   1. --db-url parameter (explicit — use your main DB or promoted replica)
#   2. DATABASE_URL from the agent's .env (if master DB is still reachable)
#   3. Local Postgres + latest backup from /opt/smsly-hosting/backups/master-db/
#      (last resort — only if both master AND DB are lost)
# =============================================================================

set -Eeuo pipefail
IFS=$'\n\t'

INSTALL_DIR="/opt/smsly-hosting"
BACKUP_DIR="$INSTALL_DIR/backups/master-db"
COMPOSE_FILE="docker-compose.prod.yml"
AGENT_COMPOSE="infrastructure/docker/docker-compose.agent-lite.yml"
LOG_FILE="/var/log/smsly-promote.log"

# Colors
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $1" | tee -a "$LOG_FILE"; }
ok()   { echo -e "${GREEN}[✓]${NC} $1" | tee -a "$LOG_FILE"; }
warn() { echo -e "${YELLOW}[!]${NC} $1" | tee -a "$LOG_FILE"; }
fail() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# Parse args
DB_URL=""
REPLICA_URL=""
for arg in "$@"; do
    case "$arg" in
        --db-url=*)     DB_URL="${arg#*=}" ;;
        --replica-url=*) REPLICA_URL="${arg#*=}" ;;
        --help|-h)
            echo "Usage: $0 [--db-url=URL] [--replica-url=URL]"
            echo ""
            echo "  --db-url         Primary database URL (main DB or promoted replica)"
            echo "  --replica-url    Read replica URL (tried if --db-url is unreachable)"
            echo ""
            echo "DB resolution order:"
            echo "  1. --db-url (explicit primary)"
            echo "  2. --replica-url (explicit replica — promote manually first if needed)"
            echo "  3. DATABASE_URL from agent .env (original master DB if still reachable)"
            echo "  4. Local Postgres + restore from agent backup (last resort)"
            exit 0 ;;
    esac
done

[ "$EUID" -eq 0 ] || fail "Must run as root. Use: sudo bash $0"
cd "$INSTALL_DIR"
[ -f ".env" ] || fail ".env not found at $INSTALL_DIR/.env"

# ──────────────────────────────────────────────────────────────────────
# STEP 1: Recover critical secrets from the agent's .env
# ──────────────────────────────────────────────────────────────────────
log "Recovering secrets from .env..."

FIELD_ENCRYPTION_KEY=$(grep -m1 '^FIELD_ENCRYPTION_KEY=' .env 2>/dev/null | cut -d= -f2- || echo "")
GATEWAY_SECRET=$(grep -m1 '^GATEWAY_SECRET=' .env 2>/dev/null | cut -d= -f2- || echo "")
SECRET_KEY=$(grep -m1 '^SECRET_KEY=' .env 2>/dev/null | cut -d= -f2- || echo "")
DOMAIN=$(grep -m1 '^DOMAIN=' .env 2>/dev/null | cut -d= -f2- || echo "")

# Fallback: master passes these as MASTER_* vars to agents
[ -z "$FIELD_ENCRYPTION_KEY" ] && FIELD_ENCRYPTION_KEY=$(grep -m1 '^MASTER_FIELD_ENCRYPTION_KEY=' .env 2>/dev/null | cut -d= -f2- || echo "")
[ -z "$GATEWAY_SECRET" ] && GATEWAY_SECRET=$(grep -m1 '^MASTER_GATEWAY_SECRET=' .env 2>/dev/null | cut -d= -f2- || echo "")

[ -z "$FIELD_ENCRYPTION_KEY" ] && fail "FIELD_ENCRYPTION_KEY not found in .env"
[ -z "$SECRET_KEY" ] && fail "SECRET_KEY not found in .env"

if [ -z "$GATEWAY_SECRET" ]; then
    warn "GATEWAY_SECRET not found - generating new one. Other agents need re-provisioning."
    GATEWAY_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
fi

DB_USER=$(grep -m1 '^MASTER_DB_USER=' .env 2>/dev/null | cut -d= -f2- || echo "smsly_admin")
DB_PASSWORD=$(grep -m1 '^MASTER_DB_PASSWORD=' .env 2>/dev/null | cut -d= -f2- || echo "")
DB_NAME=$(grep -m1 '^POSTGRES_DB=' .env 2>/dev/null || grep -m1 '^POSTGRES_DB=' "$INSTALL_DIR/.env" 2>/dev/null || echo "smsly_hosting")
ok "Secrets recovered"

# ──────────────────────────────────────────────────────────────────────
# STEP 2: Stop the lite agent stack
# ──────────────────────────────────────────────────────────────────────
log "Stopping lite agent stack..."
docker compose -f "$AGENT_COMPOSE" down --remove-orphans || echo -e "${YELLOW}    ⚠ Agent stack stop failed${NC}"
ok "Agent stack stopped"

# ──────────────────────────────────────────────────────────────────────
# STEP 3: Resolve database connection
# ──────────────────────────────────────────────────────────────────────
RESOLVED_DB_URL=""

resolve_db() {
    local url="$1"
    log "Testing DB connection: $url"
    local tmpfile
    tmpfile=$(mktemp)
    # Write a small test query to a temp file and run via psql
    echo "SELECT 1 AS ok;" > "$tmpfile"
    if psql "$url" -f "$tmpfile" -t -A 2>/dev/null | grep -q "1"; then
        rm -f "$tmpfile"
        return 0
    fi
    rm -f "$tmpfile"
    return 1
}

# Priority 1: --db-url parameter (explicit primary — main DB or promoted replica)
if [ -n "$DB_URL" ]; then
    log "Trying --db-url (primary database)..."
    if resolve_db "$DB_URL"; then
        RESOLVED_DB_URL="$DB_URL"
        ok "Connected to primary database"
    else
        warn "Primary database not reachable at $DB_URL"
    fi
fi

# Priority 2: --replica-url parameter — auto-promote if in recovery mode
promote_replica() {
    local url="$1"
    log "Checking if replica is in recovery mode..."
    local in_recovery
    in_recovery=$(psql "$url" -t -A -c "SELECT pg_is_in_recovery();" 2>/dev/null || echo "unknown")
    if [ "$in_recovery" = "t" ]; then
        log "Replica is in recovery — promoting to primary..."
        local promoted
        promoted=$(psql "$url" -t -A -c "SELECT pg_promote();" 2>/dev/null || echo "f")
        if [ "$promoted" = "t" ]; then
            log "Promotion initiated — waiting for replica to become writable..."
            for i in $(seq 1 30); do
                sleep 2
                local check
                check=$(psql "$url" -t -A -c "SELECT pg_is_in_recovery();" 2>/dev/null || echo "t")
                if [ "$check" = "f" ]; then
                    ok "Replica promoted to primary successfully"
                    return 0
                fi
            done
            warn "Replica promotion timed out after 60s"
            return 1
        else
            warn "pg_promote() returned false — check replica configuration"
            return 1
        fi
    elif [ "$in_recovery" = "f" ]; then
        ok "Replica is already writable (not in recovery)"
        return 0
    else
        warn "Cannot determine replica state at $url"
        return 1
    fi
}

if [ -z "$RESOLVED_DB_URL" ] && [ -n "$REPLICA_URL" ]; then
    log "Trying --replica-url..."
    if resolve_db "$REPLICA_URL"; then
        if promote_replica "$REPLICA_URL"; then
            RESOLVED_DB_URL="$REPLICA_URL"
            ok "Connected to promoted replica as new primary"
        else
            warn "Replica reachable but promotion failed — using as read-only fallback"
            RESOLVED_DB_URL="$REPLICA_URL"
        fi
    else
        warn "Replica not reachable at $REPLICA_URL"
    fi
fi

# Priority 3: DATABASE_URL from agent's .env (original master DB if still reachable)
if [ -z "$RESOLVED_DB_URL" ]; then
    AGENT_DB_URL=$(grep -m1 '^DATABASE_URL=' .env 2>/dev/null | cut -d= -f2- || echo "")
    if [ -n "$AGENT_DB_URL" ]; then
        log "Trying master's DATABASE_URL from .env..."
        if resolve_db "$AGENT_DB_URL"; then
            RESOLVED_DB_URL="$AGENT_DB_URL"
            ok "Connected to existing master database"
        else
            warn "Master database not reachable at $AGENT_DB_URL"
        fi
    fi
fi

# Priority 3: Local Postgres + restore from agent-stored backup
if [ -z "$RESOLVED_DB_URL" ]; then
    BACKUP_PATH=$(ls -t "$BACKUP_DIR"/master_db_*.sql.gz 2>/dev/null | head -1)
    if [ -z "$BACKUP_PATH" ]; then
        fail "No database reachable and no backup found in $BACKUP_DIR"
    fi

    log "Starting local PostgreSQL for backup restore..."
    docker rm -f smsly-db || echo -e "${YELLOW}    ⚠ Failed to remove old smsly-db${NC}"
    docker volume rm -f postgres_data || echo -e "${YELLOW}    ⚠ Failed to remove postgres_data volume${NC}"
    docker compose -f "$COMPOSE_FILE" up -d db

    log "Waiting for PostgreSQL..."
    for i in $(seq 1 30); do
        if docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U "$DB_USER" 2>/dev/null; then
            ok "PostgreSQL ready"
            break
        fi
        [ "$i" -eq 30 ] && fail "PostgreSQL failed to start"
        sleep 2
    done

    BACKUP_SIZE=$(du -h "$BACKUP_PATH" | cut -f1)
    log "Restoring backup: $(basename "$BACKUP_PATH") ($BACKUP_SIZE)..."
    gunzip -c "$BACKUP_PATH" | docker compose -f "$COMPOSE_FILE" exec -T db psql -U "$DB_USER" -d "$DB_NAME" 2>&1 | tail -3
    RESOLVED_DB_URL="postgresql://${DB_USER}:${DB_PASSWORD}@db:5432/${DB_NAME}"
    ok "Database restored from agent backup"
fi

# ──────────────────────────────────────────────────────────────────────
# STEP 4: Build the new master's .env
# ──────────────────────────────────────────────────────────────────────
log "Preparing master .env..."

NEW_MASTER_IP=$(curl -sS --max-time 5 http://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

# Extract host from resolved DB URL for the docker compose service name
# If the DB URL points to 'db' (local container), keep it as-is
DB_HOST=$(echo "$RESOLVED_DB_URL" | sed -E 's|.*@([^:/]+).*|\1|')
if [ "$DB_HOST" = "db" ]; then
    DB_LINK="db"  # local container — compose handles it
else
    DB_LINK=""     # external DB — don't start compose 'db' service
fi

env_set() {
    local key="$1" val="$2"
    if grep -q "^${key}=" .env 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" .env
    else
        echo "${key}=${val}" >> .env
    fi
}

env_set "DOMAIN" "$DOMAIN"
env_set "SERVER_IP" "$NEW_MASTER_IP"
env_set "SECRET_KEY" "$SECRET_KEY"
env_set "FIELD_ENCRYPTION_KEY" "$FIELD_ENCRYPTION_KEY"
env_set "GATEWAY_SECRET" "$GATEWAY_SECRET"
env_set "DATABASE_URL" "$RESOLVED_DB_URL"
env_set "USE_SSL" "false"
env_set "DISABLE_HTTPS_REDIRECT" "true"
env_set "SMSLY_DISABLE_TIER_GATES" "true"
env_set "POSTGRES_USER" "$DB_USER"
env_set "POSTGRES_PASSWORD" "$DB_PASSWORD"
env_set "POSTGRES_DB" "$DB_NAME"
ok "Master .env configured"

# ──────────────────────────────────────────────────────────────────────
# STEP 5: Start the master stack
# ──────────────────────────────────────────────────────────────────────
log "Starting master services..."

docker compose -f "$COMPOSE_FILE" up -d registry
ok "Registry started"

if [ -n "$DB_LINK" ]; then
    docker compose -f "$COMPOSE_FILE" up -d socket-proxy redis rabbitmq traefik caddy backend celery celery-beat celery-fast celery-deploy frontend
else
    # External DB — start without compose 'db' service
    docker compose -f "$COMPOSE_FILE" up -d --no-deps socket-proxy redis rabbitmq traefik caddy
    docker compose -f "$COMPOSE_FILE" up -d --no-deps backend celery celery-beat celery-fast celery-deploy frontend
fi
ok "Master stack started"

# ──────────────────────────────────────────────────────────────────────
# STEP 6: Run migrations
# ──────────────────────────────────────────────────────────────────────
log "Running migrations..."
sleep 10  # give backend time to connect to DB
docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py migrate --noinput 2>&1 | tail -3
ok "Migrations complete"

# ──────────────────────────────────────────────────────────────────────
# STEP 7: Verify
# ──────────────────────────────────────────────────────────────────────
log "Verifying new master..."
sleep 5
HEALTH=$(curl -sS --max-time 10 http://localhost:8000/health/live 2>/dev/null || echo "unreachable")
if [ "$HEALTH" = "unreachable" ]; then
    warn "Health check failed — check: docker compose logs backend"
else
    ok "Backend API healthy: $HEALTH"
fi

# ──────────────────────────────────────────────────────────────────────
# STEP 8: Output + save metadata for repoint_agents
# ──────────────────────────────────────────────────────────────────────
echo ""
echo "===================================="
echo -e "${GREEN}  PROMOTION COMPLETE${NC}"
echo "===================================="
echo ""
echo "New Master IP:       $NEW_MASTER_IP"
echo "GATEWAY_SECRET:      (written to .promoted-master.json)"
echo "Database:            (written to .promoted-master.json)"
echo ""
echo "Re-point other agents:"
echo ""
echo "  python manage.py repoint_agents \\"
echo "    --master-ip=$NEW_MASTER_IP \\"
echo "    --gateway-secret=$GATEWAY_SECRET"
echo ""

cat > "$INSTALL_DIR/.promoted-master.json" << EOF
{
    "master_ip": "$NEW_MASTER_IP",
    "gateway_secret": "$GATEWAY_SECRET",
    "db_url": "$RESOLVED_DB_URL",
    "db_user": "$DB_USER",
    "db_name": "$DB_NAME",
    "promoted_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
EOF
chmod 600 "$INSTALL_DIR/.promoted-master.json"
ok "Metadata saved to .promoted-master.json (permissions: 600)"
