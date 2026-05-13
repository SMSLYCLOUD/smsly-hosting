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
LOG_FILE="/var/log/smsly-agent-lite.log"

INSTALL_DIR="/opt/smsly-hosting"
COMPOSE_FILE="infrastructure/docker/docker-compose.agent-lite.yml"
COMPOSE_PATH="$INSTALL_DIR/$COMPOSE_FILE"
LOCK_FILE="/tmp/smsly-agent-lite.lock"

# ─── Parse args ──────────────────────────────────────────────────────────────
UPDATE_MODE=""
for arg in "$@"; do
    case "$arg" in
        --update)      UPDATE_MODE="full" ;;
        --update-half) UPDATE_MODE="half" ;;
        --skip-git)    SKIP_GIT="true" ;;
        --help|-h)
            echo "Usage: sudo bash scripts/agent-lite.sh [--update|--update-half] [--skip-git]"
            echo "  (no args)      Fresh install"
            echo "  --update       Full update (rebuild from cache)"
            echo "  --update-half  Quick update (restart only, no build)"
            echo "  --skip-git     Skip git pull (use local code as-is)"
            exit 0 ;;
    esac
done

# ─── Root check ──────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ERROR: This script must be run as root.${NC}"
    exit 1
fi

# ─── Lock file (prevent concurrent runs) ─────────────────────────────────────
if ! mkdir "$LOCK_FILE" 2>/dev/null; then
    echo -e "${RED}✗ Another instance is already running (lock: $LOCK_FILE)${NC}"
    exit 1
fi
cleanup() {
    rm -rf "$LOCK_FILE" 2>/dev/null || true
    echo -e "\n${BLUE}  → Lock released.${NC}"
}
trap cleanup EXIT

# ─── Log setup ───────────────────────────────────────────────────────────────
exec > >(tee -a "$LOG_FILE") 2>&1

# ─── Directory guard ─────────────────────────────────────────────────────────
if [ ! -d "$INSTALL_DIR" ]; then
    echo -e "${RED}✗ $INSTALL_DIR not found. Run the master installer first.${NC}"
    exit 1
fi

# ─── .env guard ──────────────────────────────────────────────────────────────
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo -e "${RED}✗ .env not found at $INSTALL_DIR/.env${NC}"
    echo -e "${YELLOW}  The master installer must populate .env before running this script.${NC}"
    exit 1
fi
for _var in DATABASE_URL CELERY_BROKER_URL REDIS_URL; do
    if ! grep -q "^${_var}=" "$INSTALL_DIR/.env" 2>/dev/null; then
        echo -e "${RED}✗ Missing $_var in .env — run the master provisioning flow first.${NC}"
        exit 1
    fi
done

# ─── Compose file guard ──────────────────────────────────────────────────────
if [ ! -f "$COMPOSE_PATH" ]; then
    echo -e "${RED}✗ Compose file not found: $COMPOSE_PATH${NC}"
    exit 1
fi

echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${BLUE}  Grid Agent (Lite) — $([ -n "$UPDATE_MODE" ] && echo 'Update' || echo 'Install')${NC}"
echo -e "${BLUE}  Log: $LOG_FILE${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"

# =============================================================================
# HELPERS
# =============================================================================
ensure_networks() {
    docker network inspect smsly-net >/dev/null 2>&1 || {
        echo -e "${BLUE}  → Creating smsly-net...${NC}"
        docker network create smsly-net >/dev/null 2>&1 || true
    }
    docker network inspect socket-proxy >/dev/null 2>&1 || {
        echo -e "${BLUE}  → Creating socket-proxy...${NC}"
        docker network create --driver bridge --internal socket-proxy >/dev/null 2>&1 || true
    }
}

fix_permissions() {
    local env_file="$INSTALL_DIR/.env"
    [ ! -f "$env_file" ] && return 0
    chown root:1000 "$env_file" 2>/dev/null || true
    chmod 664 "$env_file" 2>/dev/null || true
    # Also fix caddy-config if it exists (for shared volumes with master)
    [ -d "$INSTALL_DIR/caddy-config" ] && chown -R 1000:1000 "$INSTALL_DIR/caddy-config" 2>/dev/null || true
}

pull_latest_code() {
    [ "${SKIP_GIT:-false}" = "true" ] && { echo -e "${BLUE}  → Skipping git pull (--skip-git)${NC}"; return 0; }
    echo -e "${BLUE}  → Pulling latest code...${NC}"
    cd "$INSTALL_DIR"
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    # Stash any local changes to avoid pull conflicts
    git stash --include-untracked 2>/dev/null || true
    if git pull --force origin main 2>/dev/null; then
        echo -e "${GREEN}  ✓ Code updated${NC}"
    else
        echo -e "${YELLOW}  ⚠ Git pull failed, continuing with local code${NC}"
    fi
    # Pop stash — if it fails (conflicts), leave stashed and warn
    git stash pop 2>/dev/null || echo -e "${YELLOW}  ⚠ Local changes stashed (git stash list)${NC}"
}

run_migrations() {
    # Check if backend is running before attempting migrations
    if ! docker compose -f "$COMPOSE_PATH" ps --status running backend 2>/dev/null | grep -q "Up"; then
        echo -e "${YELLOW}  ⚠ Backend not running, skipping migrations${NC}"
        return 0
    fi
    echo -e "${BLUE}  → Running migrations...${NC}"
    if docker compose -f "$COMPOSE_PATH" exec -T backend python manage.py migrate --noinput 2>/dev/null; then
        echo -e "${GREEN}  ✓ Migrations complete${NC}"
    else
        echo -e "${YELLOW}  ⚠ Migration failed, retrying in 10s...${NC}"
        sleep 10
        docker compose -f "$COMPOSE_PATH" exec -T backend python manage.py migrate --noinput 2>/dev/null && \
            echo -e "${GREEN}  ✓ Migrations complete on retry${NC}" || \
            echo -e "${YELLOW}  ⚠ Migrations still failing (non-fatal, will retry on next update)${NC}"
    fi
    docker compose -f "$COMPOSE_PATH" exec -T --user root backend python manage.py collectstatic --noinput 2>/dev/null || true
}

wait_for_backend() {
    local timeout="${1:-60}"
    local interval="${2:-3}"
    echo -e "${BLUE}  → Waiting for backend health (up to ${timeout}s)...${NC}"
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        if docker compose -f "$COMPOSE_PATH" exec -T backend curl -sf http://localhost:8000/health/live &>/dev/null; then
            echo -e "${GREEN}  ✓ Backend healthy${NC}"
            return 0
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
        # Print a progress dot every ~12s
        [ $((elapsed % 12)) -eq 0 ] && echo -e "${BLUE}    ... still waiting (${elapsed}s)${NC}"
    done
    echo -e "${YELLOW}  ⚠ Backend health timeout after ${timeout}s (non-fatal)${NC}"
    return 1
}

# =============================================================================
# FRESH INSTALL
# =============================================================================
do_install() {
    local step=0

    # ── Step 1: Prerequisites ──
    step=$((step + 1))
    echo -e "\n${YELLOW}[$step/5] Checking prerequisites...${NC}"
    check_internet() { ping -c1 -W3 8.8.8.8 &>/dev/null || ping -c1 -W3 1.1.1.1 &>/dev/null; }
    check_docker() { command -v docker &>/dev/null && docker info &>/dev/null; }
    check_internet && echo -e "${GREEN}  ✓ Internet OK${NC}" || { echo -e "${RED}✗ No internet${NC}"; exit 1; }
    check_docker && echo -e "${GREEN}  ✓ Docker OK${NC}" || { echo -e "${RED}✗ Docker not running${NC}"; exit 1; }
    local ram disk_free
    ram="$(free -m | awk '/^Mem:/{print $2}')"
    [ "${ram:-0}" -lt 512 ] && { echo -e "${RED}✗ Need >= 512MB RAM (have ${ram:-0}MB)${NC}"; exit 1; }
    echo -e "${GREEN}  ✓ RAM: ${ram}MB${NC}"
    disk_free="$(df -m "$INSTALL_DIR" | awk 'NR==2{print $4}')"
    [ "${disk_free:-0}" -lt 2048 ] && { echo -e "${RED}✗ Need >= 2GB free disk (have ${disk_free:-0}MB)${NC}"; exit 1; }
    echo -e "${GREEN}  ✓ Disk: ${disk_free}MB free${NC}"

    # ── Step 2: Network + images ──
    step=$((step + 1))
    echo -e "\n${YELLOW}[$step/5] Preparing infrastructure...${NC}"
    cd "$INSTALL_DIR"
    ensure_networks
    for _img in tecnativa/docker-socket-proxy:latest traefik:v3.6; do
        if docker image inspect "$_img" &>/dev/null; then
            echo -e "${GREEN}  ✓ $_img cached${NC}"
        elif docker pull "$_img" 2>/dev/null; then
            echo -e "${GREEN}  ✓ $_img pulled${NC}"
        else
            echo -e "${YELLOW}  ⚠ Could not pull $_img (build may fail if uncached)${NC}"
        fi
    done

    # ── Step 3: Build backend ──
    step=$((step + 1))
    echo -e "\n${YELLOW}[$step/5] Building agent image...${NC}"
    if docker compose -f "$COMPOSE_FILE" build backend; then
        echo -e "${GREEN}  ✓ Backend image built${NC}"
    else
        echo -e "${RED}✗ Backend build failed${NC}"
        exit 1
    fi

    # ── Step 4: Start services ──
    step=$((step + 1))
    echo -e "\n${YELLOW}[$step/5] Starting services...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d socket-proxy traefik 2>/dev/null || \
        echo -e "${YELLOW}  ⚠ Some infra services may have failed (check: docker compose ps)${NC}"
    docker compose -f "$COMPOSE_FILE" up -d backend
    wait_for_backend 90 3
    docker compose -f "$COMPOSE_FILE" up -d celery-worker

    # Verify all expected services
    echo -e "${BLUE}  → Verifying services...${NC}"
    docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || true

    # ── Step 5: Finish ──
    step=$((step + 1))
    echo -e "\n${YELLOW}[$step/5] Finalizing...${NC}"
    run_migrations
    fix_permissions
    local ip
    ip="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo 'unknown')"
    echo -e "\n${GREEN}═══════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✅ Agent Install Complete${NC}"
    echo -e "${GREEN}  Traefik: http://${ip}:80${NC}"
    echo -e "${GREEN}═══════════════════════════════════════${NC}"
}

# =============================================================================
# UPDATE (full)
# =============================================================================
do_update_full() {
    echo -e "\n${BLUE}  → Full update (rebuild + restart)${NC}"
    pull_latest_code
    cd "$INSTALL_DIR"
    ensure_networks
    echo -e "${BLUE}  → Rebuilding agent image...${NC}"
    docker compose -f "$COMPOSE_PATH" build backend || {
        echo -e "${RED}✗ Build failed${NC}"; exit 1;
    }
    echo -e "${BLUE}  → Restarting services...${NC}"
    docker compose -f "$COMPOSE_PATH" up -d --no-deps backend celery-worker 2>/dev/null || true
    wait_for_backend 60 3
    run_migrations
    fix_permissions
    echo -e "\n${GREEN}✅ Agent update complete${NC}"
}

# =============================================================================
# UPDATE (half — no build)
# =============================================================================
do_update_half() {
    echo -e "\n${BLUE}  → Half update (restart only, no build)${NC}"
    pull_latest_code
    cd "$INSTALL_DIR"
    echo -e "${BLUE}  → Restarting backend...${NC}"
    docker compose -f "$COMPOSE_PATH" restart backend 2>/dev/null || true
    wait_for_backend 60 3
    run_migrations
    fix_permissions
    echo -e "\n${GREEN}✅ Agent half-update complete${NC}"
}

# =============================================================================
# MAIN DISPATCH
# =============================================================================
case "$UPDATE_MODE" in
    full)  do_update_full ;;
    half)  do_update_half ;;
    "")    do_install ;;
    *)     echo -e "${RED}Unknown mode: $UPDATE_MODE${NC}"; exit 1 ;;
esac
