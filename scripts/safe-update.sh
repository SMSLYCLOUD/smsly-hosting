#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════════
# Safe Update Protocol — wraps install.sh --update with:
#   1. Pre-flight checks (disk, docker, git, critical containers)
#   2. State snapshot + DB backup before touching anything
#   3. Runs existing battle-tested install.sh --update
#   4. Post-deploy health verification (all containers, Loki, Traefik)
#   5. Automatic rollback on any failure
#
# Usage:  bash scripts/safe-update.sh
# ══════════════════════════════════════════════════════════════════════════════
set -euo pipefail

INSTALL_DIR="/opt/smsly-hosting"
COMPOSE_FILE="$INSTALL_DIR/docker-compose.prod.yml"
SNAPSHOT_FILE="$INSTALL_DIR/.update-safe-snapshot"
BACKUP_DIR="$INSTALL_DIR/.update-backups"
LOG_FILE="/var/log/smsly-update.log"
MIN_DISK_MB=5120

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BLUE='\033[0;34m'; NC='\033[0m'
_log()  { printf "[%s] %s\n" "$(date +%H:%M:%S)" "$*" | tee -a "$LOG_FILE"; }
_ok()   { echo -e "${GREEN}  ✓${NC} $*"; _log "OK: $*"; }
_warn() { echo -e "${YELLOW}  ⚠${NC} $*"; _log "WARN: $*"; }
_fail() { echo -e "${RED}  ✗${NC} $*"; _log "FAIL: $*"; }
_step() { echo -e "\n${BLUE}── $* ──${NC}"; _log "STEP: $*"; }

# ══════════════════════════════════════════════════════════════════════════════
# PRE-FLIGHT
# ══════════════════════════════════════════════════════════════════════════════
preflight() {
    _step "Pre-flight Checks"
    [ "$EUID" -eq 0 ] || { _fail "Must run as root"; exit 1; }

    local free_mb; free_mb=$(df -m "$INSTALL_DIR" | awk 'NR==2 {print $4}')
    [ "$free_mb" -ge "$MIN_DISK_MB" ] || { _fail "Disk: ${free_mb}MB free (need ${MIN_DISK_MB}MB)"; exit 1; }
    _ok "Disk: ${free_mb}MB free"

    docker info >/dev/null 2>&1 || { _fail "Docker not responding"; exit 1; }
    _ok "Docker: responsive"

    for cf in "$COMPOSE_FILE" "$INSTALL_DIR/infrastructure/docker/docker-compose.observability.yml"; do
        [ -f "$cf" ] || { _fail "Missing: $cf"; exit 1; }
    done
    _ok "Compose files: present"

    cd "$INSTALL_DIR"
    git rev-parse --git-dir >/dev/null 2>&1 || { _fail "Not a git repo"; exit 1; }
    [ -n "$(git status --porcelain 2>/dev/null)" ] && { _warn "Uncommitted changes — stashing"; git stash push -m "safe-update-auto-$(date +%s)" 2>/dev/null || true; }
    _ok "Git: clean"

    for c in smsly-hosting-db-1 smsly-hosting-redis-1 smsly-hosting-caddy-1; do
        docker inspect "$c" --format='{{.State.Running}}' 2>/dev/null | grep -q true || { _fail "$c not running"; exit 1; }
    done
    _ok "Critical containers: RUNNING"

    [ -f "$INSTALL_DIR/.env" ] && source "$INSTALL_DIR/.env" 2>/dev/null || true
    _ok "All pre-flight checks passed"
}

# ══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT + DB BACKUP
# ══════════════════════════════════════════════════════════════════════════════
snapshot_and_backup() {
    _step "Snapshot + DB Backup"
    mkdir -p "$BACKUP_DIR"

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
    _ok "Snapshot saved: $prev_hash"
}

# ══════════════════════════════════════════════════════════════════════════════
# RUN UPDATE
# ══════════════════════════════════════════════════════════════════════════════
run_update() {
    _step "Running install.sh --update"
    bash "$INSTALL_DIR/install.sh" --update 2>&1 | tee -a "$LOG_FILE"
    local rc=${PIPESTATUS[0]}
    if [ "$rc" -eq 0 ]; then
        _ok "install.sh --update succeeded"
    else
        _fail "install.sh --update exited with code $rc"
        return 1
    fi
}

# ══════════════════════════════════════════════════════════════════════════════
# POST-VERIFY
# ══════════════════════════════════════════════════════════════════════════════
post_verify() {
    _step "Post-Deploy Verification"
    local failed=0

    # All critical containers
    for ctr in smsly-hosting-db-1 smsly-hosting-redis-1 smsly-hosting-rabbitmq-1 \
               smsly-hosting-backend-1 smsly-hosting-frontend-1 smsly-hosting-caddy-1 \
               smsly-hosting-celery-1 smsly-hosting-celery-beat-1 \
               smsly-loki smsly-prometheus smsly-grafana smsly-promtail \
               smsly-docker-labels; do
        docker inspect "$ctr" --format='{{.State.Running}}' 2>/dev/null | grep -q true && \
            _ok "$ctr" || { _fail "$ctr: NOT running"; failed=$((failed + 1)); }
    done

    # Loki
    curl -sf http://localhost:3100/ready >/dev/null 2>&1 && _ok "Loki: ready" || { _warn "Loki: not ready"; failed=$((failed + 1)); }

    # Prometheus targets
    if curl -sf http://127.0.0.1:9090/api/v1/targets >/dev/null 2>&1; then
        _ok "Prometheus: responding"
        local docker_labels_up
        docker_labels_up=$(curl -s http://127.0.0.1:9090/api/v1/targets 2>/dev/null | python3 -c "
import json,sys
d=json.load(sys.stdin)
ups=[t for t in d['data']['activeTargets'] if 'docker-labels' in str(t.get('labels',{})) and t['health']=='up']
print(len(ups))
" 2>/dev/null || echo "0")
        [ "$docker_labels_up" -gt 0 ] && _ok "Prometheus docker-labels: $docker_labels_up UP" || _warn "Prometheus docker-labels: 0 UP"
    else
        _warn "Prometheus: not responding"
        failed=$((failed + 1))
    fi

    # Traefik
    curl -sf http://127.0.0.1:8081/ping >/dev/null 2>&1 && _ok "Traefik: responding" || { _warn "Traefik: not responding"; failed=$((failed + 1)); }

    return $failed
}

# ══════════════════════════════════════════════════════════════════════════════
# ROLLBACK
# ══════════════════════════════════════════════════════════════════════════════
rollback() {
    _step "ROLLBACK — Restoring Previous State"
    [ -f "$SNAPSHOT_FILE" ] || { _fail "No snapshot — cannot rollback"; return 1; }
    source "$SNAPSHOT_FILE"

    _warn "Reverting to: $PREV_HASH"
    cd "$INSTALL_DIR"
    git fetch origin "$PREV_BRANCH" 2>/dev/null || true
    git reset --hard "$PREV_HASH" 2>/dev/null || { _fail "Git reset failed"; return 1; }
    _ok "Git: reverted"

    bash "$INSTALL_DIR/install.sh" --update 2>&1 | tail -30 || _warn "Rollback install had issues"

    # Restore DB if backed up
    if [ -n "${BACKUP_FILE:-}" ] && [ -f "$BACKUP_FILE" ]; then
        _warn "Restoring database..."
        docker exec -i smsly-hosting-db-1 psql -U smsly_admin smsly_hosting < "$BACKUP_FILE" 2>/dev/null && \
            _ok "DB restored" || _warn "DB restore failed — manual recovery needed"
    fi

    # Fix perms
    [ -d /opt/smsly-hosting/prometheus-targets ] && {
        chown -R 1000:1000 /opt/smsly-hosting/prometheus-targets 2>/dev/null || true
        chmod 2777 /opt/smsly-hosting/prometheus-targets 2>/dev/null || true
    }

    sleep 30
    post_verify && _ok "Rollback successful — system healthy" || _warn "Rollback done but some services unhealthy"
}

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
main() {
    echo -e "\n${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  SMSLY Safe Update Protocol${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"

    rm -f "$SNAPSHOT_FILE"
    mkdir -p "$BACKUP_DIR"

    preflight || exit 1
    snapshot_and_backup

    if ! run_update; then
        _warn "Update failed — initiating rollback"
        rollback
        exit 1
    fi

    # Wait for migrations + warmup
    _step "Warmup (30s)"
    sleep 30

    if post_verify; then
        echo -e "\n${GREEN}═══════════════════════════════════════════════════════════════${NC}"
        echo -e "${GREEN}  ✓ UPDATE SUCCESSFUL — All systems healthy${NC}"
        echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
        rm -f "$SNAPSHOT_FILE"
    else
        _warn "Post-verify found issues — rolling back"
        rollback
        exit 1
    fi
}

main "$@"
