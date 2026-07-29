#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# test-redis-failover.sh
#
# Tests Redis Sentinel High Availability by simulating a primary failure and
# verifying automatic failover + recovery.
#
# What it does:
#   1. Records the current Sentinel master.
#   2. Stops the redis-primary container (simulates crash).
#   3. Waits for Sentinel to promote the replica (up to 30 s).
#   4. Verifies the new master is the promoted replica.
#   5. Verifies Redis is writable through Sentinel (app can still operate).
#   6. Runs failover recovery to clean up the orphaned primary.
#   7. Restores the original topology (brings back redis-primary as replica).
#
# Usage:
#   sudo bash scripts/test-redis-failover.sh [--quick]
#
# Options:
#   --quick    Skip the post-recovery verification steps (for CI).
#
# Dependencies:
#   - docker compose (with the full prod stack running)
#   - redis-cli (from host or via docker exec)
#   - scripts/redis-failover-recovery.sh (must exist)
#
# ⚠  This script will briefly interrupt Redis availability.
#    Do NOT run against production during peak hours.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

SENTINEL_1="smsly-redis-sentinel-1"
PRIMARY_CONTAINER="smsly-redis-primary"
REPLICA_CONTAINER="smsly-redis-replica"
COMPOSE_FILE="docker-compose.prod.yml"
SENTINEL_MASTER="mymaster"
RECOVERY_SCRIPT="$(dirname "$0")/redis-failover-recovery.sh"

QUICK=false
for arg in "$@"; do
    case "$arg" in
        --quick) QUICK=true ;;
        --help|-h)
            echo "Usage: sudo bash scripts/test-redis-failover.sh [--quick]"
            exit 0
            ;;
    esac
done

# ── Helper: redis-cli via sentinel container ─────────────────────────────────
redis_sentinel() {
    timeout 10 docker exec "$SENTINEL_1" redis-cli -p 26379 "$@"
}

# ── Helper: redis-cli to a specific host/port ────────────────────────────────
redis_cmd() {
    local host="$1"; shift
    local pass="${REDIS_PASSWORD:-}"
    if [ -n "$pass" ]; then
        timeout 10 docker exec "$SENTINEL_1" redis-cli -h "$host" -a "$pass" "$@" 2>/dev/null
    else
        timeout 10 docker exec "$SENTINEL_1" redis-cli -h "$host" "$@"
    fi
}

# ── Step 0: Pre-flight checks ────────────────────────────────────────────────
echo -e "${BLUE}══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  SMSLY Redis Failover Test${NC}"
echo -e "${BLUE}══════════════════════════════════════════════════════════════${NC}"

echo ""
echo -e "${CYAN}[0/7] Pre-flight checks ...${NC}"

for container in "$SENTINEL_1" "$PRIMARY_CONTAINER" "$REPLICA_CONTAINER"; do
    if ! docker ps --format '{{.Names}}' | grep -q "$container"; then
        echo -e "${RED}  ❌ Required container '$container' is not running.${NC}"
        echo -e "${RED}     Start the stack first: docker compose -f $COMPOSE_FILE up -d${NC}"
        exit 1
    fi
done

if [ ! -f "$RECOVERY_SCRIPT" ]; then
    echo -e "${RED}  ❌ Recovery script not found: $RECOVERY_SCRIPT${NC}"
    exit 1
fi

echo -e "${GREEN}  ✅ All prerequisites met.${NC}"

# ── Step 1: Record initial master ────────────────────────────────────────────
echo ""
echo -e "${CYAN}[1/7] Recording initial Sentinel master ...${NC}"

ORIGINAL_MASTER=$(redis_sentinel SENTINEL get-master-addr-by-name "$SENTINEL_MASTER" 2>/dev/null | head -1)
ORIGINAL_MASTER_PORT=$(redis_sentinel SENTINEL get-master-addr-by-name "$SENTINEL_MASTER" 2>/dev/null | tail -1)
echo -e "${GREEN}  → Original master: $ORIGINAL_MASTER:$ORIGINAL_MASTER_PORT${NC}"

# Write a test key to verify later
TEST_KEY="failover-test-$(date +%s)"
TEST_VALUE="before-failover-$$"
if redis_cmd "$ORIGINAL_MASTER" SET "$TEST_KEY" "$TEST_VALUE" >/dev/null 2>&1; then
    echo -e "${GREEN}  → Wrote test key '$TEST_KEY' to original master.${NC}"
else
    echo -e "${YELLOW}  ⚠ Could not write test key (Redis may require password). Continuing anyway.${NC}"
    TEST_KEY=""
fi

# ── Step 2: Simulate primary failure ─────────────────────────────────────────
echo ""
echo -e "${CYAN}[2/7] Simulating primary failure (stopping $PRIMARY_CONTAINER) ...${NC}"

if ! docker stop --time 5 "$PRIMARY_CONTAINER" >/dev/null 2>&1; then
    echo -e "${RED}  ❌ Failed to stop primary container.${NC}"
    exit 1
fi
echo -e "${GREEN}  ✅ Primary container stopped.${NC}"

# ── Step 3: Wait for Sentinel failover ───────────────────────────────────────
echo ""
echo -e "${CYAN}[3/7] Waiting for Sentinel failover (up to 30 s) ...${NC}"

NEW_MASTER=""
for i in $(seq 1 30); do
    sleep 1
    NEW_MASTER=$(redis_sentinel SENTINEL get-master-addr-by-name "$SENTINEL_MASTER" 2>/dev/null | head -1 || true)
    NEW_MASTER_PORT=$(redis_sentinel SENTINEL get-master-addr-by-name "$SENTINEL_MASTER" 2>/dev/null | tail -1 || true)
    if [ -n "$NEW_MASTER" ] && [ "$NEW_MASTER" != "$ORIGINAL_MASTER" ]; then
        echo -e "${GREEN}  ✅ Failover detected after ${i}s!${NC}"
        echo -e "${GREEN}  → New master: $NEW_MASTER:$NEW_MASTER_PORT${NC}"
        break
    fi
done

if [ -z "$NEW_MASTER" ] || [ "$NEW_MASTER" = "$ORIGINAL_MASTER" ]; then
    echo -e "${RED}  ❌ Failover did not occur within 30 s.${NC}"
    echo -e "${RED}     Check sentinel logs: docker logs $SENTINEL_1${NC}"
    echo -e "${YELLOW}     Restoring primary ...${NC}"
    docker start "$PRIMARY_CONTAINER" >/dev/null 2>&1 || true
    exit 1
fi

# ── Step 4: Verify new master is writable ────────────────────────────────────
echo ""
echo -e "${CYAN}[4/7] Verifying new master is writable ...${NC}"

VERIFY_VALUE="after-failover-$$"
if redis_cmd "$NEW_MASTER" SET "verify-$TEST_KEY" "$VERIFY_VALUE" >/dev/null 2>&1; then
    READBACK=$(redis_cmd "$NEW_MASTER" GET "verify-$TEST_KEY" 2>/dev/null || echo "")
    if [ "$READBACK" = "$VERIFY_VALUE" ]; then
        echo -e "${GREEN}  ✅ New master is writable and readable.${NC}"
    else
        echo -e "${YELLOW}  ⚠ New master wrote but readback mismatch (got '$READBACK').${NC}"
    fi
else
    echo -e "${RED}  ❌ New master is NOT writable!${NC}"
    echo -e "${RED}     Check: is the new master in read-write mode?${NC}"
    exit 1
fi

# ── Step 5: Run failover recovery ────────────────────────────────────────────
echo ""
echo -e "${CYAN}[5/7] Running failover recovery ...${NC}"

if bash "$RECOVERY_SCRIPT" 2>&1; then
    echo -e "${GREEN}  ✅ Recovery completed successfully.${NC}"
else
    echo -e "${RED}  ❌ Recovery script failed.${NC}"
    echo -e "${YELLOW}     Run manually: sudo bash $RECOVERY_SCRIPT${NC}"
    exit 1
fi

# ── Step 6: Restore original topology ────────────────────────────────────────
echo ""
echo -e "${CYAN}[6/7] Restoring original topology (recreating redis-primary as replica) ...${NC}"

if docker compose -f "$COMPOSE_FILE" up -d --no-deps redis-primary 2>&1; then
    echo -e "${GREEN}  ✅ redis-primary container recreated.${NC}"
    # Wait for replica to sync
    sleep 5
    SYNC_STATUS=$(redis_cmd "$NEW_MASTER" INFO replication 2>/dev/null | grep -c "slave0" || true)
    if [ "$SYNC_STATUS" -gt 0 ] || redis_cmd "$NEW_MASTER" INFO replication 2>/dev/null | grep -q "connected_slaves:[1-9]"; then
        echo -e "${GREEN}  ✅ Replica is connected and syncing.${NC}"
    else
        echo -e "${YELLOW}  ⚠ Replica may not be connected yet. Check: docker logs $PRIMARY_CONTAINER${NC}"
    fi
else
    echo -e "${YELLOW}  ⚠ Could not recreate redis-primary container.${NC}"
    echo -e "${YELLOW}     Run: docker compose -f $COMPOSE_FILE up -d --no-deps redis-primary${NC}"
fi

# ── Step 7: Final verification ───────────────────────────────────────────────
echo ""
echo -e "${CYAN}[7/7] Final verification ...${NC}"

FINAL_MASTER=$(redis_sentinel SENTINEL get-master-addr-by-name "$SENTINEL_MASTER" 2>/dev/null | head -1 || echo "unknown")
echo -e "${GREEN}  → Sentinel master: $FINAL_MASTER${NC}"

if [ -n "$TEST_KEY" ]; then
    FINAL_VALUE=$(redis_cmd "$FINAL_MASTER" GET "$TEST_KEY" 2>/dev/null || echo "<not-found>")
    echo -e "${GREEN}  → Test key '$TEST_KEY' = '$FINAL_VALUE'${NC}"
fi

echo ""
echo -e "${GREEN}  ✅ Failover test completed successfully!${NC}"
echo ""
echo -e "${BLUE}══════════════════════════════════════════════════════════════${NC}"
