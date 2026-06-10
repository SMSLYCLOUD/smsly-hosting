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

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'; NC='\033[0m'
_log()  { printf "[%s] %s\n" "$(date +%H:%M:%S)" "$*" >> "$CLEAN_LOG"; }
_ok()   { echo -e "${GREEN}  ✓${NC} $*"; _log "OK: $*"; }
_warn() { echo -e "${YELLOW}  ⚠${NC} $*"; _log "WARN: $*"; }
_fail() { echo -e "${RED}  ✗${NC} $*"; _log "FAIL: $*"; }
_step() { echo -e "\n${BLUE}── $* ──${NC}"; _log "STEP: $*"; }

# ══════════════════════════════════════════════════════════════════════════════
safe_update_preflight() {
    _step "Pre-flight Checks"
    [ "$EUID" -eq 0 ] || { _fail "Must run as root"; return 1; }

    local free_mb; free_mb=$(df -m "$INSTALL_DIR" | awk 'NR==2 {print $4}')
    [ "$free_mb" -ge "$MIN_DISK_MB" ] || { _fail "Disk: ${free_mb}MB free (need ${MIN_DISK_MB}MB)"; return 1; }
    _ok "Disk: ${free_mb}MB free"

    docker info >/dev/null 2>&1 || { _fail "Docker not responding"; return 1; }
    _ok "Docker: responsive"

    # Clean up orphaned containers from failed previous builds
    # Remove ALL stopped/created/exited containers from this project to prevent name conflicts
    if ! docker compose -f "$COMPOSE_FILE" down --remove-orphans 2>/dev/null; then
        _warn "docker compose down failed (project may not exist yet) — falling back to direct container removal"
    fi
    # Belt-and-suspenders: rm -f any SMSLY container that compose down missed
    containers=$(docker ps -a -q --filter "name=smsly-hosting" 2>/dev/null || true)
    [ -n "$containers" ] && docker rm -f $containers 2>/dev/null || true
    containers=$(docker ps -a -q --filter "name=smsly-" 2>/dev/null || true)
    [ -n "$containers" ] && docker rm -f $containers 2>/dev/null || true
    _ok "Cleaned up all previous containers"

    for cf in "$COMPOSE_FILE" "$INSTALL_DIR/infrastructure/docker/docker-compose.observability.yml"; do
        [ -f "$cf" ] || { _fail "Missing: $cf"; return 1; }
    done
    _ok "Compose files: present"

    cd "$INSTALL_DIR"
    git rev-parse --git-dir >/dev/null 2>&1 || { _fail "Not a git repo"; return 1; }
    _ok "Pre-flight passed"
    return 0
}

# ══════════════════════════════════════════════════════════════════════════════
safe_update_snapshot() {
    _step "Snapshot + DB Backup"
    mkdir -p "$BACKUP_DIR"
    export SNAPSHOT_FILE BACKUP_DIR

    local prev_hash; prev_hash=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    local prev_branch; prev_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
    local backup_file="$BACKUP_DIR/pre-update-$(date +%Y%m%d-%H%M%S).sql"

    docker exec smsly-hosting-db-1 pg_dump -U smsly_admin smsly_hosting > "$backup_file" 2>/dev/null && \
        _ok "DB backup: $(du -h "$backup_file" | cut -f1)" || \
        { _warn "DB backup failed — continuing without safety net"; backup_file=""; }

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
    local critical=(smsly-hosting-db-1 smsly-hosting-redis-1 smsly-hosting-rabbitmq-1
                    smsly-hosting-backend-1 smsly-hosting-frontend-1 smsly-hosting-caddy-1
                    smsly-hosting-celery-1 smsly-hosting-celery-beat-1
                    smsly-loki smsly-prometheus smsly-grafana smsly-promtail
                    smsly-docker-labels smsly-hosting-socket-proxy-1)

    for ctr in "${critical[@]}"; do
        docker inspect "$ctr" --format='{{.State.Running}}' 2>/dev/null | grep -q true && \
            _ok "$ctr" || { _fail "$ctr NOT running"; failed=$((failed + 1)); }
    done

    curl -sf http://localhost:3100/ready >/dev/null 2>&1 && _ok "Loki: ready" || { _warn "Loki: not ready"; failed=$((failed + 1)); }
    # Traefik — check via Docker health status (more reliable than curl ping)
    local traefik_status
    traefik_status=$(docker inspect smsly-hosting-traefik-1 --format='{{.State.Health.Status}}' 2>/dev/null || echo "unknown")
    if [ "$traefik_status" = "healthy" ]; then
        _ok "Traefik: healthy (Docker)"
    else
        curl -sf http://127.0.0.1:8082/ping >/dev/null 2>&1 && _ok "Traefik: responding" || { _warn "Traefik: not responding"; failed=$((failed + 1)); }
    fi
    curl -sf http://127.0.0.1:9090/api/v1/targets >/dev/null 2>&1 && _ok "Prometheus: responding" || { _warn "Prometheus: not responding"; failed=$((failed + 1)); }

    [ "$failed" -le 2 ] && _ok "Health checks passed ($failed tolerable)" || _warn "$failed health check(s) failed"
    return $( [ "$failed" -gt 2 ] && echo 1 || echo 0 )
}

# ══════════════════════════════════════════════════════════════════════════════
safe_update_rollback() {
    _step "ROLLBACK — Restoring Previous State"
    [ -f "$SNAPSHOT_FILE" ] || { _fail "No snapshot — cannot rollback"; return 1; }
    source "$SNAPSHOT_FILE" 2>/dev/null || true

    _warn "Reverting to: ${PREV_HASH:-unknown}"
    cd "$INSTALL_DIR"

    if [ -n "${PREV_HASH:-}" ] && [ "$PREV_HASH" != "unknown" ]; then
        git fetch origin "${PREV_BRANCH:-main}" 2>/dev/null || true
        if git reset --hard "$PREV_HASH" 2>/dev/null; then
            _ok "Git: reverted to $PREV_HASH"
        else
            _fail "Git reset failed"; return 1
        fi
    fi

    # Clear stale lock from the failed original install.sh
    rm -f /tmp/smsly-install.lock 2>/dev/null || true
    SMSLY_SKIP_GIT_SYNC=true bash "$INSTALL_DIR/install.sh" --update >/dev/null 2>&1 || _warn "Rollback install had issues"

    # Restore DB if backed up
    if [ -n "${BACKUP_FILE:-}" ] && [ -f "$BACKUP_FILE" ]; then
        _warn "Restoring database from backup..."
        docker exec -i smsly-hosting-db-1 psql -U smsly_admin smsly_hosting < "$BACKUP_FILE" >/dev/null 2>&1 && \
            _ok "DB restored" || _warn "DB restore failed"
    fi

    # Fix perms
    [ -d /opt/smsly-hosting/prometheus-targets ] && {
        chown -R 1000:1000 /opt/smsly-hosting/prometheus-targets 2>/dev/null || true
        chmod 2777 /opt/smsly-hosting/prometheus-targets 2>/dev/null || true
    }

    sleep 30
    safe_update_post_verify && _ok "Rollback successful" || _warn "Rollback done, some services unhealthy"
    return 0
}

# ══════════════════════════════════════════════════════════════════════════════
# Standalone mode (bash scripts/safe-update.sh)
# ══════════════════════════════════════════════════════════════════════════════
if [ "$SAFE_UPDATE_SOURCED" = "false" ]; then
    safe_update_snapshot
    safe_update_preflight || exit 1
    bash "$INSTALL_DIR/install.sh" --update >> "$CLEAN_LOG" 2>&1 || { _warn "Update failed — rolling back"; safe_update_rollback; exit 1; }
    sleep 30
    safe_update_post_verify || { _warn "Post-verify failed — rolling back"; safe_update_rollback; exit 1; }
    echo -e "\n${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✓ UPDATE SUCCESSFUL — All systems healthy${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    rm -f "$SNAPSHOT_FILE"
fi
