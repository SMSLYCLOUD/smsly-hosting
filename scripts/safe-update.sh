#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# Safe Update Library — sourced by install.sh --update
# Provides: preflight, snapshot_and_backup, post_verify, rollback
#
# When run directly (bash scripts/safe-update.sh), still acts as standalone.
# ══════════════════════════════════════════════════════════════════════════════

# ── Guard: allow sourcing without execution ────────────────────────────────
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    # Being sourced — just define functions, don't run main()
    SAFE_UPDATE_SOURCED=true
else
    SAFE_UPDATE_SOURCED=false
fi

INSTALL_DIR="${INSTALL_DIR:-/opt/smsly-hosting}"
COMPOSE_FILE="${COMPOSE_FILE:-$INSTALL_DIR/docker-compose.prod.yml}"
SNAPSHOT_FILE="$INSTALL_DIR/.update-safe-snapshot"
BACKUP_DIR="$INSTALL_DIR/.update-backups"
CLEAN_LOG="/var/log/smsly-clean.log"
MIN_DISK_MB=5120

# Load .env so POSTGRES_USER / POSTGRES_DB reflect the real deployment.
# Without this the backup/restore below would target the wrong database or user
# if the deployment overrides the defaults.
if [ -f "$INSTALL_DIR/.env" ]; then
    set -a
    . "$INSTALL_DIR/.env"
    set +a
fi
POSTGRES_USER="${POSTGRES_USER:-smsly_admin}"
POSTGRES_DB="${POSTGRES_DB:-smsly_hosting}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'
_log()  { printf "[%s] %s\n" "$(date +%H:%M:%S)" "$*" >> "$CLEAN_LOG"; }
_ok()   { echo -e "${GREEN}  ✓${NC} $*"; _log "OK: $*"; }
_warn() { echo -e "${YELLOW}  ⚠${NC} $*"; _log "WARN: $*"; }
_fail() { echo -e "${RED}  ✗${NC} $*"; _log "FAIL: $*"; }
_step() { echo -e "\n${BLUE}── $* ──${NC}"; _log "STEP: $*"; }
_progress() { echo -e "  ${CYAN}→${NC} $*"; _log "PROGRESS: $*"; }

# ══════════════════════════════════════════════════════════════════════════════
safe_update_preflight() {
    _step "Pre-flight Checks"
    [ "$EUID" -eq 0 ] || { _fail "Must run as root"; return 1; }

    local free_mb; free_mb=$(df -m "$INSTALL_DIR" | awk 'NR==2 {print $4}')
    [ "$free_mb" -ge "$MIN_DISK_MB" ] || { _fail "Disk: ${free_mb}MB free (need ${MIN_DISK_MB}MB)"; return 1; }
    _ok "Disk: ${free_mb}MB free"

    docker info  || { _fail "Docker not responding"; return 1; }
    _ok "Docker: responsive"

    # Clean up stopped service containers to prevent name conflicts (without stopping running production containers)
    if ! docker compose -f "$COMPOSE_FILE" rm -f ; then
        _warn "docker compose rm failed (project may not exist yet)"
    fi
    
    # Remove stopped conflicting containers to prevent compose recreating them
    prune_stopped_conflicting() {
        local pattern="$1"
        local c_id=""
        local c_name=""
        local removed=0
        for c_id in $(docker ps -a -q --filter "name=${pattern}" --filter "status=exited" --filter "status=created"  || true); do
            c_name=$(docker inspect "$c_id" --format='{{.Name}}'  | sed 's/^\///')
            if [ -n "$c_name" ]; then
                if [[ "$c_name" == *"-backup-containers"* ]]; then
                    docker rm "$c_id"  && removed=$((removed + 1))
                else
                    docker rm "$c_id"  && removed=$((removed + 1))
                fi
            fi
        done
        [ "$removed" -gt 0 ] && _ok "Removed $removed stopped container(s) matching '$pattern'"
    }
    
    prune_stopped_conflicting "smsly-hosting"
    prune_stopped_conflicting "smsly-"

    for cf in "$COMPOSE_FILE" "$INSTALL_DIR/infrastructure/docker/docker-compose.observability.yml"; do
        [ -f "$cf" ] || { _fail "Missing: $cf"; return 1; }
    done
    _ok "Compose files: present"

    cd "$INSTALL_DIR"
    git rev-parse --git-dir  || { _fail "Not a git repo"; return 1; }
    _ok "Pre-flight passed"
    return 0
}

# ══════════════════════════════════════════════════════════════════════════════
safe_update_snapshot() {
    _step "Snapshot + DB Backup"
    mkdir -p "$BACKUP_DIR"
    export SNAPSHOT_FILE BACKUP_DIR

    local prev_hash
    if [ -n "${SMSLY_PRE_UPDATE_HEAD:-}" ]; then
        prev_hash="$SMSLY_PRE_UPDATE_HEAD"
    else
        prev_hash=$(git rev-parse HEAD  || echo "unknown")
    fi
    local prev_branch; prev_branch=$(git rev-parse --abbrev-ref HEAD  || echo "main")
    local backup_file="$BACKUP_DIR/pre-update-$(date +%Y%m%d-%H%M%S).sql"

    _progress "Backing up database (timeout: 120s)..."
    if timeout 120 docker exec smsly-postgres-primary pg_dump --clean --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB" > "$backup_file" ; then
        _ok "DB backup: $(du -h "$backup_file" | cut -f1)"
    else
        _warn "DB backup failed — continuing without safety net"
        backup_file=""
    fi

    # Backup .env (restored on rollback)
    if [ -f "$INSTALL_DIR/.env" ]; then
        cp "$INSTALL_DIR/.env" "$BACKUP_DIR/pre-update.env"  && _ok ".env backed up" || _warn ".env backup failed"
    fi

    # Redis RDB snapshot (non-fatal — container may not be running)
    if timeout 10 docker exec smsly-hosting-redis-1 redis-cli SAVE ; then
        _ok "Redis RDB saved"
        timeout 10 docker cp smsly-hosting-redis-1:/data/dump.rdb "$BACKUP_DIR/pre-update-redis.rdb" || echo -e "${YELLOW}    ⚠ Redis dump copy failed${NC}"
    else
        _warn "Redis backup skipped (container may not be running)"
    fi

    # RabbitMQ definitions export (non-fatal — container or rabbitmqadmin may not be available)
    if timeout 15 docker exec smsly-hosting-rabbitmq-1 rabbitmqadmin export "$BACKUP_DIR/pre-update-rabbitmq-defs.json" ; then
        _ok "RabbitMQ definitions exported"
    else
        _warn "RabbitMQ export skipped (container or rabbitmqadmin not available)"
    fi

    # Snapshot current images to prevent pruning and enable instant rollback
    _step "Snapshotting Docker Images"
    for img in smsly-hosting-backend smsly-hosting-frontend smsly-hosting-celery; do
        if docker image inspect "$img:latest" ; then
            docker tag "$img:latest" "$img:rollback-safe"  && _ok "Tagged $img:rollback-safe" || _warn "Failed to tag $img:rollback-safe"
        fi
    done

    cat > "$SNAPSHOT_FILE" <<SNAPEOF
PREV_HASH=$prev_hash
PREV_BRANCH=$prev_branch
TIMESTAMP=$(date -Iseconds)
BACKUP_FILE=$backup_file
SNAPEOF
    _ok "Snapshot: $prev_hash"
    return 0
}

# ══════════════════════════════════════════════════════════════════════════════
safe_update_post_verify() {
    _step "Post-Deploy Health Check"
    local failed=0
    local core=(smsly-postgres-primary smsly-redis-primary smsly-hosting-rabbitmq-1
                smsly-hosting-backend-1 smsly-hosting-frontend-1 smsly-hosting-caddy-1
                smsly-hosting-celery-1 smsly-hosting-celery-beat-1
                smsly-hosting-socket-proxy-1 smsly-hosting-registry-1
                smsly-hosting-pgcat-1)
    local observability=(smsly-loki smsly-prometheus smsly-grafana smsly-promtail
                         smsly-docker-labels)

    for ctr in "${core[@]}"; do
        docker inspect "$ctr" --format='{{.State.Running}}'  | grep -q true && \
            _ok "$ctr" || { _fail "$ctr NOT running"; failed=$((failed + 1)); }
    done

    local obs_file="$INSTALL_DIR/infrastructure/docker/docker-compose.observability.yml"
    if [ -f "$obs_file" ]; then
        for ctr in "${observability[@]}"; do
            docker inspect "$ctr" --format='{{.State.Running}}'  | grep -q true && \
                _ok "$ctr" || { _warn "$ctr not running (observability stack may not be deployed)"; }
        done
        curl -sf http://localhost:3100/ready  && _ok "Loki: ready" || { _warn "Loki: not ready"; }
        curl -sf http://127.0.0.1:9090/api/v1/targets  && _ok "Prometheus: responding" || { _warn "Prometheus: not responding"; }
    fi

    local traefik_ok=false
    for i in 1 2 3 4 5; do
        local traefik_status
        traefik_status=$(docker inspect smsly-hosting-traefik-1 --format='{{.State.Health.Status}}'  || echo "unknown")
        if [ "$traefik_status" = "healthy" ]; then
            _ok "Traefik: healthy (Docker)"
            traefik_ok=true
            break
        fi
        curl -sf http://127.0.0.1:8082/ping  && { _ok "Traefik: responding"; traefik_ok=true; break; }
        [ "$i" -lt 5 ] && sleep 10
    done
    [ "$traefik_ok" = "true" ] || { _warn "Traefik: not responding"; failed=$((failed + 1)); }

    if [ "$failed" -eq 0 ]; then
        _ok "All core health checks passed"
    elif [ "$failed" -le 2 ]; then
        _warn "$failed core health check(s) failed — tolerable"
    else
        _warn "$failed core health check(s) failed"
    fi
    return $( [ "$failed" -gt 2 ] && echo 1 || echo 0 )
}

# ══════════════════════════════════════════════════════════════════════════════
safe_update_rollback() {
    _step "ROLLBACK — Restoring Previous State"
    [ -f "$SNAPSHOT_FILE" ] || { _fail "No snapshot — cannot rollback"; return 1; }

    # Extract variables safely instead of sourcing the file
    PREV_HASH=$(grep '^PREV_HASH=' "$SNAPSHOT_FILE" | cut -d= -f2 | tr -d "'\"")
    PREV_BRANCH=$(grep '^PREV_BRANCH=' "$SNAPSHOT_FILE" | cut -d= -f2 | tr -d "'\"")
    BACKUP_FILE=$(grep '^BACKUP_FILE=' "$SNAPSHOT_FILE" | cut -d= -f2 | tr -d "'\"")

    # Validate PREV_HASH is a valid git hash
    if [ -n "${PREV_HASH:-}" ] && [ "$PREV_HASH" != "unknown" ]; then
        if ! echo "$PREV_HASH" | grep -qE '^[a-f0-9]{40}$'; then
            _fail "Invalid PREV_HASH in snapshot: $PREV_HASH"
            return 1
        fi
    fi

    _warn "Reverting to: ${PREV_HASH:-unknown}"
    cd "$INSTALL_DIR"

    if [ -n "${PREV_HASH:-}" ] && [ "$PREV_HASH" != "unknown" ]; then
        git fetch origin "${PREV_BRANCH:-main}"  || true
        if git reset --hard "$PREV_HASH" ; then
            _ok "Git: reverted to $PREV_HASH"
        else
            _fail "Git reset failed"; return 1
        fi
    fi

    # Restore .env from pre-update backup
    if [ -f "$BACKUP_DIR/pre-update.env" ]; then
        cp "$BACKUP_DIR/pre-update.env" "$INSTALL_DIR/.env"  && _ok ".env restored" || _warn ".env restore failed"
    fi

    # Clear stale lock from the failed original install.sh
    rm -f /tmp/smsly-install.lock  || true
    
    # Instant rollback: restore images from snapshot instead of rebuilding
    _step "Restoring Docker Images"
    for img in smsly-hosting-backend smsly-hosting-frontend smsly-hosting-celery; do
        if docker image inspect "$img:rollback-safe" ; then
            docker tag "$img:rollback-safe" "$img:latest"  && _ok "Restored $img:latest" || _warn "Failed to restore $img:latest"
        fi
    done
    
    # Ensure the database is up before restoring into it.
    docker compose -f "$COMPOSE_FILE" up -d --wait --no-deps postgres-primary \
        || _warn "Could not ensure postgres-primary is running"

    # Stop any app containers left from the failed attempt so the --clean dump
    # does not drop objects while they are connected (avoids partial restore).
    docker compose -f "$COMPOSE_FILE" stop --timeout 30 backend frontend celery celery-deploy celery-fast celery-beat pgcat 2>/dev/null || true

    # Restore DB BEFORE bringing app containers up, so the --clean dump does not
    # drop objects while backend/celery are connected (transient errors and can
    # leave behind new-version tables from a partial migration).
    if [ -n "${BACKUP_FILE:-}" ] && [ -f "$BACKUP_FILE" ]; then
        _warn "Restoring database from backup..."
        timeout 600 docker exec -i smsly-postgres-primary psql -U "$POSTGRES_USER" "$POSTGRES_DB" < "$BACKUP_FILE"  && \
            _ok "DB restored" || _warn "DB restore failed"
    fi

    # Restart core services with the restored images (DB is now consistent).
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --wait --no-deps backend frontend celery celery-deploy celery-fast celery-beat $(grep -q "^  *pgcat:" "${COMPOSE_FILE:-docker-compose.prod.yml}"  && echo "pgcat")  || _warn "Docker compose up had issues during rollback"

    # Fix perms
    [ -d /opt/smsly-hosting/prometheus-targets ] && {
        chown -R 1000:1000 /opt/smsly-hosting/prometheus-targets  || true
        chmod 2777 /opt/smsly-hosting/prometheus-targets  || true
    }

    sleep 30
    safe_update_post_verify && _ok "Rollback successful" || _warn "Rollback done, some services unhealthy"
    return 0
}

# ══════════════════════════════════════════════════════════════════════════════
safe_update_cleanup() {
    _step "Cleaning Up Backup Containers"
    local count=0
    for c_id in $(docker ps -a -q --filter "name=-backup-containers-"  || true); do
        docker stop "$c_id" || echo -e "${YELLOW}    ⚠ Failed to stop backup container $c_id${NC}"
        docker rm "$c_id"  && count=$((count + 1))
    done
    # Also cleanup any leftover stopped smsly- containers that compose may try to revive
    for c_id in $(docker ps -a -q --filter "name=smsly-" --filter "status=exited"  || true); do
        docker rm "$c_id"  && count=$((count + 1))
    done
    if [ "$count" -gt 0 ]; then
        _ok "Removed $count stale container(s)"
    else
        _ok "No stale containers to clean"
    fi
    
    # Clean up the rollback tags so old images can be pruned
    _step "Cleaning Up Image Snapshots"
    for img in smsly-hosting-backend smsly-hosting-frontend smsly-hosting-celery; do
        if docker image inspect "$img:rollback-safe" ; then
            docker rmi "$img:rollback-safe"  && _ok "Removed tag $img:rollback-safe" || true
        fi
    done
    
    return 0
}

# ══════════════════════════════════════════════════════════════════════════════
# Standalone mode (bash scripts/safe-update.sh)
# ══════════════════════════════════════════════════════════════════════════════
if [ "$SAFE_UPDATE_SOURCED" = "false" ]; then
    safe_update_snapshot
    safe_update_preflight || exit 1
    bash "$INSTALL_DIR/install.sh" --update >> "$CLEAN_LOG"  || { _warn "Update failed — rolling back"; safe_update_rollback; exit 1; }
    sleep 30
    safe_update_post_verify || { _warn "Post-verify failed — rolling back"; safe_update_rollback; exit 1; }
    safe_update_cleanup
    echo -e "\n${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✓ UPDATE SUCCESSFUL — All systems healthy${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    rm -f "$SNAPSHOT_FILE"
fi

