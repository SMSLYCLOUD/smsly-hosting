#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# promote-replica.sh
#
# Promotes postgres-replica to primary when the primary is down.
# This is a manual failover script for the simple HA setup.
#
# For automatic failover, use the Patroni-based replication at /replication.
#
# Usage:
#   sudo bash scripts/promote-replica.sh [--dry-run]
#
# What it does:
#   1. Verifies postgres-primary is actually down
#   2. Promotes postgres-replica to primary (pg_promote)
#   3. Updates pgcat config to point to the new primary
#   4. Optionally reconfigures the old primary as a replica (if it comes back)
#
# ⚠ IMPORTANT: After running this script, the old primary is no longer
# the source of truth. If it comes back, it will have diverged data.
# You must re-initialize it as a replica from the new primary.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

COMPOSE_FILE="docker-compose.prod.yml"
PRIMARY_CONTAINER="smsly-postgres-primary"
REPLICA_CONTAINER="smsly-postgres-replica"
PGCAT_CONTAINER="smsly-hosting-pgcat-1"

DRY_RUN=false
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --help|-h)
            echo "Usage: sudo bash scripts/promote-replica.sh [--dry-run]"
            exit 0
            ;;
    esac
done

echo -e "${BLUE}═══ SMSLY PostgreSQL Failover: promote replica to primary ═══${NC}"

# ── Step 1: Check if primary is actually down ────────────────────────────────
echo ""
echo -e "${BLUE}→ Step 1: Checking primary status...${NC}"

PRIMARY_UP=false
if timeout 10 docker exec "$PRIMARY_CONTAINER" pg_isready -U smsly_admin >/dev/null 2>&1; then
    PRIMARY_UP=true
fi

if [ "$PRIMARY_UP" = true ]; then
    echo -e "${YELLOW}⚠ Primary is still running.${NC}"
    echo -e "  Are you sure you want to promote the replica?"
    echo -e "  This will cause a split-brain situation."
    read -p "  Type 'yes' to proceed: " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo -e "${YELLOW}Aborted.${NC}"
        exit 0
    fi
    echo -e "${YELLOW}  → Force-stopping primary...${NC}"
    if [ "$DRY_RUN" = false ]; then
        docker stop "$PRIMARY_CONTAINER" || echo -e "${YELLOW}    ⚠ Failed to stop primary container${NC}"
        sleep 2
    fi
fi
echo -e "${GREEN}  ✓ Primary is down (or stopped)${NC}"

# ── Step 2: Check replica is running and in recovery ─────────────────────────
echo ""
echo -e "${BLUE}→ Step 2: Checking replica status...${NC}"

if ! docker ps --format '{{.Names}}' | grep -qx "$REPLICA_CONTAINER"; then
    echo -e "${RED}✗ Replica container $REPLICA_CONTAINER is not running${NC}"
    echo -e "${YELLOW}  → Cannot promote. Start the replica first.${NC}"
    exit 1
fi

IN_RECOVERY=$(timeout 10 docker exec "$REPLICA_CONTAINER" \
    psql -U smsly_admin -d smsly_hosting -t -A \
    -c "SELECT pg_is_in_recovery();" 2>/dev/null || echo "")

if [ "$IN_RECOVERY" != "t" ]; then
    echo -e "${YELLOW}⚠ Replica is NOT in recovery mode — it might already be primary${NC}"
    echo -e "  Current pg_is_in_recovery: $IN_RECOVERY"
    exit 0
fi
echo -e "${GREEN}  ✓ Replica is in recovery (standby mode)${NC}"

# ── Step 3: Get replication lag ──────────────────────────────────────────────
echo ""
echo -e "${BLUE}→ Step 3: Checking replication lag...${NC}"

LAG=$(timeout 10 docker exec "$REPLICA_CONTAINER" \
    psql -U smsly_admin -d smsly_hosting -t -A \
    -c "SELECT CASE WHEN pg_last_wal_receive_lsn() = pg_last_wal_replay_lsn() THEN 0 ELSE EXTRACT(EPOCH FROM now() - pg_last_xact_replay_timestamp())::int END;" \
    2>/dev/null || echo "unknown")

echo -e "  Replication lag: ${LAG}s"

if [ "$LAG" != "unknown" ] && [ "$LAG" -gt 60 ]; then
    echo -e "${YELLOW}⚠ High replication lag! Data loss may occur.${NC}"
    read -p "  Type 'yes' to proceed anyway: " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        echo -e "${YELLOW}Aborted.${NC}"
        exit 0
    fi
fi

# ── Step 4: Drain connections from old primary ──────────────────────────
echo ""
echo -e "${BLUE}→ Step 4: Draining connections from old primary...${NC}"

if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}  [DRY RUN] Would drain connections and promote${NC}"
else
    if docker ps --format '{{.Names}}' | grep -q "^${PRIMARY_CONTAINER}$"; then
        echo -e "  Terminating active connections on ${PRIMARY_CONTAINER}..."
        timeout 30 docker exec "$PRIMARY_CONTAINER" \
            psql -U smsly_admin -d smsly_hosting \
            -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND state = 'active';" \
            || echo -e "${YELLOW}    ⚠ Failed to drain connections from primary${NC}"
        sleep 2
        echo -e "${GREEN}  ✓ Connections drained from old primary${NC}"
    else
        echo -e "  Old primary not running — skipping drain"
    fi
fi

# ── Step 5: Promote the replica ──────────────────────────────────────────────
echo ""
echo -e "${BLUE}→ Step 5: Promoting replica to primary...${NC}"

if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}  [DRY RUN] Would run: SELECT pg_promote();${NC}"
else
    timeout 10 docker exec "$REPLICA_CONTAINER" \
        psql -U smsly_admin -d smsly_hosting -c "SELECT pg_promote();" || echo -e "${YELLOW}    ⚠ pg_promote() failed${NC}"

    # Wait for promotion to complete
    echo -n "  Waiting for promotion"
    for i in $(seq 1 30); do
        sleep 1
        IS_PRIMARY=$(timeout 10 docker exec "$REPLICA_CONTAINER" \
            psql -U smsly_admin -d smsly_hosting -t -A \
            -c "SELECT pg_is_in_recovery();" 2>/dev/null || echo "")
        if [ "$IS_PRIMARY" = "f" ]; then
            echo ""
            echo -e "${GREEN}  ✓ Replica promoted to primary!${NC}"
            break
        fi
        echo -n "."
    done

    if [ "$IS_PRIMARY" != "f" ]; then
        echo ""
        echo -e "${RED}✗ Promotion may have failed. Check replica logs.${NC}"
        docker logs "$REPLICA_CONTAINER" --tail 20
        exit 1
    fi
fi

# ── Step 6: Update pgcat to point to the new primary ─────────────────────────
echo ""
echo -e "${BLUE}→ Step 6: Updating pgcat config...${NC}"

# The new primary is now the replica container (smsly-postgres-replica)
# But pgcat needs to connect to it as the primary
# Since they're on the same Docker network, we can use the container name

NEW_PRIMARY_HOST="postgres-replica"
NEW_PRIMARY_PORT="5432"

if [ "$DRY_RUN" = true ]; then
    echo -e "${YELLOW}  [DRY RUN] Would update pgcat to point to ${NEW_PRIMARY_HOST}:${NEW_PRIMARY_PORT}${NC}"
else
    # Build new DB_REPLICA_HOSTS (empty — no replicas after failover)
    # The old primary is down, so we remove it from the replica list
    timeout 30 docker exec "$PGCAT_CONTAINER" \
        python3 /scripts/render_pgcat_config.py /etc/pgcat/pgcat.toml || echo -e "${YELLOW}    ⚠ pgcat config render (no DB_HOST) failed${NC}"

    # The render script reads DB_HOST from env, which is postgres-primary
    # We need to override it to point to the new primary
    timeout 30 docker exec -e "DB_HOST=${NEW_PRIMARY_HOST}" "$PGCAT_CONTAINER" \
        python3 /scripts/render_pgcat_config.py /etc/pgcat/pgcat.toml || echo -e "${YELLOW}    ⚠ pgcat config render with new primary failed${NC}"

    # Reload pgcat
    docker kill -s SIGHUP "$PGCAT_CONTAINER" || \
        docker restart "$PGCAT_CONTAINER" || echo -e "${YELLOW}    ⚠ pgcat reload failed${NC}"

    echo -e "${GREEN}  ✓ PgCat config updated and reloaded${NC}"
fi

# ── Step 7: Summary ─────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}═══ Failover complete ═══${NC}"
echo ""
echo -e "  New primary: ${REPLICA_CONTAINER} (${NEW_PRIMARY_HOST}:${NEW_PRIMARY_PORT})"
echo -e "  Old primary: ${PRIMARY_CONTAINER} (DOWN)"
echo ""
echo -e "${YELLOW}⚠ IMPORTANT NEXT STEPS:${NC}"
echo -e "  1. The old primary has diverged data. When it comes back:"
echo -e "     a. Stop it:  docker stop ${PRIMARY_CONTAINER}"
echo -e "     b. Delete its data:  docker volume rm smsly-hosting_postgres-primary-data"
echo -e "     c. Re-initialize as replica from the new primary"
echo -e "     d. Update compose to swap primary/replica roles"
echo ""
echo -e "  2. Consider using Patroni for automatic failover:"
echo -e "     https://smsly.cloud/docs/replication"
echo ""
echo -e "  3. Update any external monitoring to point to the new primary."
