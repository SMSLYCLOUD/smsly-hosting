#!/bin/bash
# =============================================================================
# Grid Agent (Lite) — Install & Update Script
# =============================================================================
# A lightweight agent node that connects to a master Grid instance.
# Runs: backend, celery-worker, socket-proxy, traefik (no Caddy, no DB)
#
# Usage:
#   Install:  sudo bash scripts/agent-lite.sh
#   Update:   sudo bash scripts/agent-lite.sh --update
#   Update half (no build): sudo bash scripts/agent-lite.sh --update-half
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'

INSTALL_DIR="/opt/smsly-hosting"
COMPOSE_FILE="infrastructure/docker/docker-compose.agent-lite.yml"

# ─── Parse args ──────────────────────────────────────────────────────────────
UPDATE_MODE=""
for arg in "$@"; do
    case "$arg" in
        --update)      UPDATE_MODE="full" ;;
        --update-half) UPDATE_MODE="half" ;;
        --help|-h)
            echo "Usage: sudo bash scripts/agent-lite.sh [--update|--update-half]"
            echo "  (no args)      Fresh install"
            echo "  --update       Full update (rebuild from cache)"
            echo "  --update-half  Quick update (restart only, no build)"
            exit 0 ;;
    esac
done

# ─── Root check ──────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ERROR: This script must be run as root.${NC}"
    exit 1
fi

# ─── Pre-flight ──────────────────────────────────────────────────────────────
check_internet() { ping -c1 -W3 8.8.8.8 &>/dev/null || ping -c1 -W3 1.1.1.1 &>/dev/null; }
check_docker() { command -v docker &>/dev/null && docker info &>/dev/null; }

echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${BLUE}  Grid Agent (Lite) — $([ -n "$UPDATE_MODE" ] && echo 'Update' || echo 'Install')${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"

# =============================================================================
# HELPERS
# =============================================================================
ensure_networks() {
    docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null 2>&1 || true
    docker network inspect socket-proxy >/dev/null 2>&1 || docker network create --driver bridge --internal socket-proxy >/dev/null 2>&1 || true
}

fix_permissions() {
    local env_file="$INSTALL_DIR/.env"
    [ ! -f "$env_file" ] && return 0
    chown root:1000 "$env_file" 2>/dev/null || true
    chmod 664 "$env_file" 2>/dev/null || true
}

run_migrations() {
    echo -e "${BLUE}  → Running migrations...${NC}"
    docker compose -f "$INSTALL_DIR/$COMPOSE_FILE" exec -T backend python manage.py migrate --noinput 2>/dev/null || {
        echo -e "${YELLOW}  ⚠ Migration failed, retrying in 10s...${NC}"
        sleep 10
        docker compose -f "$INSTALL_DIR/$COMPOSE_FILE" exec -T backend python manage.py migrate --noinput 2>/dev/null || true
    }
    docker compose -f "$INSTALL_DIR/$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput 2>/dev/null || true
}

# =============================================================================
# FRESH INSTALL
# =============================================================================
do_install() {
    echo -e "${YELLOW}[1/4] Checking prerequisites...${NC}"
    check_internet && echo -e "${GREEN}  ✓ Internet OK${NC}" || { echo -e "${RED}✗ No internet${NC}"; exit 1; }
    check_docker && echo -e "${GREEN}  ✓ Docker OK${NC}" || { echo -e "${RED}✗ Docker not running${NC}"; exit 1; }
    local ram total_ram_mb
    ram="$(free -m | awk '/^Mem:/{print $2}')"
    [ "${ram:-0}" -lt 512 ] && { echo -e "${RED}✗ Need >= 512MB RAM${NC}"; exit 1; }
    echo -e "${GREEN}  ✓ RAM: ${ram}MB${NC}"

    echo -e "${YELLOW}[2/4] Building agent image...${NC}"
    cd "$INSTALL_DIR"
    ensure_networks
    docker compose -f "$COMPOSE_FILE" build backend

    echo -e "${YELLOW}[3/4] Starting services...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d socket-proxy traefik
    docker compose -f "$COMPOSE_FILE" up -d backend
    echo -e "${BLUE}  → Waiting for backend health...${NC}"
    for i in $(seq 1 30); do
        if docker compose -f "$COMPOSE_FILE" exec -T backend curl -sf http://localhost:8000/health/live &>/dev/null; then
            echo -e "${GREEN}  ✓ Backend healthy${NC}"
            break
        fi
        [ "$i" -eq 30 ] && { echo -e "${YELLOW}  ⚠ Backend health timeout (non-fatal)${NC}"; break; }
        sleep 2
    done
    docker compose -f "$COMPOSE_FILE" up -d celery-worker

    echo -e "${YELLOW}[4/4] Finishing...${NC}"
    run_migrations
    fix_permissions
    echo -e "${GREEN}✅ Agent install complete${NC}"
    echo -e "  ${BLUE}Traefik:${NC} http://$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}'):80"
}

# =============================================================================
# UPDATE (full)
# =============================================================================
do_update_full() {
    echo -e "${BLUE}  → Pulling latest code...${NC}"
    cd "$INSTALL_DIR"
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    git stash 2>/dev/null || true
    git pull --force origin main 2>/dev/null || { echo -e "${YELLOW}  ⚠ Git pull failed, continuing with local code${NC}"; }
    git stash pop 2>/dev/null || true

    echo -e "${BLUE}  → Rebuilding agent image...${NC}"
    ensure_networks
    docker compose -f "$COMPOSE_FILE" build backend

    echo -e "${BLUE}  → Restarting services...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d --no-deps backend celery-worker
    sleep 5
    run_migrations
    fix_permissions
    echo -e "${GREEN}✅ Agent update complete${NC}"
}

# =============================================================================
# UPDATE (half — no build)
# =============================================================================
do_update_half() {
    echo -e "${BLUE}  → Pulling latest code...${NC}"
    cd "$INSTALL_DIR"
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    git stash 2>/dev/null || true
    git pull --force origin main 2>/dev/null || true
    git stash pop 2>/dev/null || true

    echo -e "${BLUE}  → Restarting backend (no build)...${NC}"
    docker compose -f "$COMPOSE_FILE" restart backend
    sleep 5
    run_migrations
    fix_permissions
    echo -e "${GREEN}✅ Agent half-update complete${NC}"
}

# =============================================================================
# MAIN DISPATCH
# =============================================================================
if [ ! -d "$INSTALL_DIR" ]; then
    echo -e "${RED}✗ $INSTALL_DIR not found. Run the master installer first.${NC}"
    exit 1
fi

case "$UPDATE_MODE" in
    full)  do_update_full ;;
    half)  do_update_half ;;
    "")    do_install ;;
esac
