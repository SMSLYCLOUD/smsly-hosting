#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# redis-failover-recovery.sh
#
# Detects and recovers from a Redis Sentinel failover where the old
# redis-primary container is orphaned (still running as standalone with
# diverged data).
#
# What it does:
#   1. Queries Sentinel for the current authoritative master.
#   2. Checks if the ``redis-primary`` container is still that master.
#   3. If NOT (failover happened):
#      a. Stops and removes the orphaned ``redis-primary`` container.
#      b. Optionally re-initializes it as a replica of the new master.
#
# Usage:
#   sudo bash scripts/redis-failover-recovery.sh [--dry-run]
#
# Dependencies:
#   - docker compose (with redis-primary service defined)
#   - redis-cli (from host or via docker exec)
#
# ⚠  This script must run on the host where the Redis containers live.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

COMPOSE_FILE="docker-compose.prod.yml"
PRIMARY_CONTAINER="smsly-redis-primary"
REPLICA_CONTAINER="smsly-redis-replica"
SENTINEL_1="smsly-redis-sentinel-1"
SENTINEL_PORT=26379
SENTINEL_MASTER="mymaster"

DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --help|-h)
            echo "Usage: sudo bash scripts/redis-failover-recovery.sh [--dry-run]"
            echo ""
            echo "  --dry-run    Log actions without making changes."
            exit 0
            ;;
    esac
done

echo -e "${BLUE}══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  SMSLY Redis Failover Recovery${NC}"
echo -e "${BLUE}══════════════════════════════════════════════════════════════${NC}"

# ── Helper: run redis-cli inside a sentinel container ────────────────────────
redis_cli_sentinel() {
    docker exec "$SENTINEL_1" redis-cli -p "$SENTINEL_PORT" "$@"
}

# ── Helper: run redis-cli inside the primary container ───────────────────────
redis_cli_primary() {
    # Pass REDIS_PASSWORD from host env or fall back to sentinel config
    local pass="${REDIS_PASSWORD:-}"
    if [ -n "$pass" ]; then
        docker exec "$PRIMARY_CONTAINER" redis-cli -a "$pass" "$@" 2>/dev/null
    else
        docker exec "$PRIMARY_CONTAINER" redis-cli "$@"
    fi
}

# ── Step 1: Check Sentinel is reachable ──────────────────────────────────────
echo ""
echo -e "${CYAN}[1/5] Checking Sentinel availability ...${NC}"
if ! docker ps --format '{{.Names}}' | grep -q "$SENTINEL_1"; then
    echo -e "${YELLOW}  ⚠ Sentinel container '$SENTINEL_1' is not running.${NC}"
    echo -e "${YELLOW}  → Is the HA Redis stack up? (docker compose ps redis-sentinel-1)${NC}"
    echo -e "${YELLOW}  → Skipping recovery (no Sentinel to query).${NC}"
    exit 0
fi

SENTINEL_INFO=$(redis_cli_sentinel SENTINEL get-master-addr-by-name "$SENTINEL_MASTER" 2>/dev/null || true)
CURRENT_MASTER_HOST=$(echo "$SENTINEL_INFO" | head -1)
CURRENT_MASTER_PORT=$(echo "$SENTINEL_INFO" | tail -1)

if [ -z "$CURRENT_MASTER_HOST" ]; then
    echo -e "${RED}  ❌ Could not determine current master from Sentinel.${NC}"
    echo -e "${RED}     Check sentinel logs: docker logs $SENTINEL_1${NC}"
    exit 1
fi

echo -e "${GREEN}  ✅ Sentinel reachable.${NC}"
echo -e "${GREEN}  → Current master according to Sentinel: ${CYAN}$CURRENT_MASTER_HOST:$CURRENT_MASTER_PORT${NC}"

# ── Step 2: Check if redis-primary container exists ──────────────────────────
echo ""
echo -e "${CYAN}[2/5] Checking redis-primary container ...${NC}"
if ! docker ps -a --format '{{.Names}}' | grep -q "$PRIMARY_CONTAINER"; then
    echo -e "${YELLOW}  ⚠ Container '$PRIMARY_CONTAINER' does not exist.${NC}"
    echo -e "${YELLOW}  → Nothing to recover.${NC}"
    exit 0
fi

PRIMARY_RUNNING=$(docker ps --format '{{.Names}}' | grep -c "$PRIMARY_CONTAINER" || true)
if [ "$PRIMARY_RUNNING" -eq 0 ]; then
    echo -e "${YELLOW}  ⚠ Container '$PRIMARY_CONTAINER' exists but is not running.${NC}"
    echo -e "${YELLOW}  → Nothing to recover (already stopped).${NC}"
    exit 0
fi

# Get the IP of the redis-primary container
PRIMARY_IP=$(docker inspect "$PRIMARY_CONTAINER" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || echo "unknown")

echo -e "${GREEN}  ✅ Container '$PRIMARY_CONTAINER' is running (IP: $PRIMARY_IP).${NC}"

# ── Step 3: Compare ─────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[3/5] Comparing container against Sentinel master ...${NC}"

if [ "$PRIMARY_IP" = "$CURRENT_MASTER_HOST" ]; then
    echo -e "${GREEN}  ✅ Container '$PRIMARY_CONTAINER' ($PRIMARY_IP) IS the Sentinel master.${NC}"
    echo -e "${GREEN}  → No recovery needed.${NC}"
    exit 0
fi

# The container hostname might differ from IP; also check by resolving
PRIMARY_HOSTNAME=$(docker exec "$PRIMARY_CONTAINER" hostname 2>/dev/null || echo "")
if [ -n "$PRIMARY_HOSTNAME" ] && [ "$PRIMARY_HOSTNAME" = "$CURRENT_MASTER_HOST" ]; then
    echo -e "${GREEN}  ✅ Container '$PRIMARY_CONTAINER' (hostname: $PRIMARY_HOSTNAME) IS the Sentinel master.${NC}"
    echo -e "${GREEN}  → No recovery needed.${NC}"
    exit 0
fi

# ── Failover detected ───────────────────────────────────────────────────────
echo -e "${RED}  ❌ Failover detected!${NC}"
echo -e "${RED}     Container  : $PRIMARY_CONTAINER ($PRIMARY_IP)${NC}"
echo -e "${RED}     Master is  : $CURRENT_MASTER_HOST:$CURRENT_MASTER_PORT${NC}"
echo ""

# ── Step 4: Stop orphaned primary ────────────────────────────────────────────
echo -e "${CYAN}[4/5] Stopping orphaned primary ...${NC}"

if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}  [DRY-RUN] Would stop and remove container '$PRIMARY_CONTAINER'.${NC}"
else
    echo -e "${YELLOW}  → Stopping container '$PRIMARY_CONTAINER' ...${NC}"
    if docker stop --time 10 "$PRIMARY_CONTAINER" >/dev/null 2>&1; then
        echo -e "${GREEN}    ✓ Container stopped.${NC}"
    else
        echo -e "${RED}    ✗ Failed to stop container. Check logs: docker logs $PRIMARY_CONTAINER${NC}"
        exit 1
    fi

    echo -e "${YELLOW}  → Removing container '$PRIMARY_CONTAINER' ...${NC}"
    if docker rm -v "$PRIMARY_CONTAINER" >/dev/null 2>&1; then
        echo -e "${GREEN}    ✓ Container removed.${NC}"
    else
        echo -e "${RED}    ✗ Failed to remove container.${NC}"
        exit 1
    fi
fi

# ── Step 5: Summary ──────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}[5/5] Summary${NC}"
echo ""
if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}  DRY RUN — no changes made.${NC}"
    echo -e ""
    echo -e "${YELLOW}  To recover: run this script without --dry-run${NC}"
else
    echo -e "${GREEN}  ✅ Recovery complete.${NC}"
    echo -e ""
    echo -e "  ${YELLOW}Next steps:${NC}"
    echo -e "  • The old primary container has been removed."
    echo -e "  • To bring it back as a replica of the new master, run:"
    echo -e "    ${CYAN}  docker compose -f $COMPOSE_FILE up -d --no-deps redis-primary${NC}"
    echo -e "  • Docker Compose will recreate it with the replicaof config"
    echo -e "    pointing to 'redis-primary' (which Docker DNS resolves to"
    echo -e "    the NEW master after the container is recreated)."
    echo -e ""
    echo -e "  ${YELLOW}Note:${NC} The recreated container performs a full sync"
    echo -e "  from the new master (data is not preserved from the old primary)."
fi
echo ""
echo -e "${BLUE}══════════════════════════════════════════════════════════════${NC}"
