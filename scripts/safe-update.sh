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

# Load .env safely so POSTGRES_USER / POSTGRES_DB reflect the real deployment.
if [ -f "$INSTALL_DIR/.env" ]; then
    if ! declare -F env_load_file_safe >/dev/null 2>&1 && [ -f "$INSTALL_DIR/lib/env.sh" ]; then
        # shellcheck disable=SC1091
        source "$INSTALL_DIR/lib/env.sh"
    fi
    if declare -F env_load_file_safe >/dev/null 2>&1; then
        env_load_file_safe "$INSTALL_DIR/.env"
    else
        while IFS= read -r _env_line || [ -n "$_env_line" ]; do
            _env_line="${_env_line%$'\r'}"
            [[ -z "$_env_line" || "$_env_line" =~ ^[[:space:]]*# ]] && continue
            [[ "$_env_line" == export[[:space:]]* ]] && _env_line="${_env_line#export }"
            [[ "$_env_line" == *=* ]] || continue
            _env_key="${_env_line%%=*}"
            _env_key="${_env_key#"${_env_key%%[![:space:]]*}"}"
            _env_key="${_env_key%"${_env_key##*[![:space:]]}"}"
            [[ "$_env_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
            _env_val="${_env_line#*=}"
            if [[ "$_env_val" == \"*\" && "$_env_val" == *\" && ${#_env_val} -ge 2 ]]; then
                _env_val="${_env_val:1:${#_env_val}-2}"
            elif [[ "$_env_val" == \'*\' && "$_env_val" == *\' && ${#_env_val} -ge 2 ]]; then
                _env_val="${_env_val:1:${#_env_val}-2}"
            fi
            export "$_env_key=$_env_val"
        done < "$INSTALL_DIR/.env"
        unset _env_line _env_key _env_val
    fi
fi
POSTGRES_USER="${POSTGRES_USER:-smsly_admin}"
POSTGRES_DB="${POSTGRES_DB:-smsly_hosting}"
COMPOSE_PROFILES="${COMPOSE_PROFILES:-local-ha,medium,full}"
export COMPOSE_PROFILES

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

    for cf in "$COMPOSE_FILE"; do
        [ -f "$cf" ] || { _fail "Missing: $cf"; return 1; }
    done
    if [ "${MODE_AGENT_LITE:-false}" != "true" ] && [ "${MODE_NODE:-false}" != "true" ] && [[ "${COMPOSE_FILE:-}" != */docker-compose.agent-lite.yml ]] && [[ "${COMPOSE_FILE:-}" != */docker-compose.node.yml ]]; then
        local obs_cf="$INSTALL_DIR/infrastructure/docker/docker-compose.observability.yml"
        [ -f "$obs_cf" ] || { _fail "Missing: $obs_cf"; return 1; }
    fi
    _ok "Compose files: present"

    cd "$INSTALL_DIR"
    git rev-parse --git-dir  || { _fail "Not a git repo"; return 1; }
    _ok "Pre-flight passed"
    return 0
}

# ══════════════════════════════════════════════════════════════════════════════
safe_update_snapshot() {
    _step "Snapshot + DB Backup"
    cd "$INSTALL_DIR" || { _fail "Cannot cd to $INSTALL_DIR"; return 1; }
    if [ -f "$SNAPSHOT_FILE" ]; then
        local _snap_ts="" _snap_epoch="" _snap_prev_hash="" _expected_prev="" _now="" _snap_age=""
        _snap_epoch=$(grep '^SNAPSHOT_EPOCH=' "$SNAPSHOT_FILE" 2>/dev/null | head -n1 | cut -d= -f2- | tr -d "'\"" | tr -d '[:space:]')
        if ! [[ "$_snap_epoch" =~ ^[0-9]+$ ]]; then
            _snap_ts=$(grep '^TIMESTAMP=' "$SNAPSHOT_FILE" 2>/dev/null | head -n1 | cut -d= -f2- | tr -d "'\"" | tr -d '[:space:]')
            if [ -n "$_snap_ts" ]; then
                _snap_epoch=$(date -d "$_snap_ts" +%s 2>/dev/null || echo 0)
            else
                _snap_epoch=0
            fi
        fi
        _snap_prev_hash=$(grep '^PREV_HASH=' "$SNAPSHOT_FILE" 2>/dev/null | head -n1 | cut -d= -f2- | tr -d "'\"" | tr -d '[:space:]')
        if [ -n "${SMSLY_PRE_UPDATE_HEAD:-}" ]; then
            _expected_prev="$SMSLY_PRE_UPDATE_HEAD"
        else
            _expected_prev=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
        fi
        _now=$(date +%s)
        if [[ "$_snap_epoch" =~ ^[0-9]+$ ]] && [ "$_snap_epoch" -gt 0 ]; then
            _snap_age=$((_now - _snap_epoch))
        else
            _snap_age=999999
        fi
        if [ "$_snap_age" -ge 0 ] && [ "$_snap_age" -lt 3600 ] && [ -n "$_snap_prev_hash" ] && [ "$_snap_prev_hash" = "$_expected_prev" ]; then
            export SNAPSHOT_FILE BACKUP_DIR
            _ok "Preserving existing fresh snapshot (${_snap_age}s old)"
            return 0
        fi
        _warn "Discarding stale snapshot (hash=${_snap_prev_hash:0:8}, expected=${_expected_prev:0:8})"
        rm -f "$SNAPSHOT_FILE" 2>/dev/null || true
    fi
    local _prev_umask
    _prev_umask=$(umask)
    umask 077
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR" || true
    export SNAPSHOT_FILE BACKUP_DIR

    local prev_hash
    if [ -n "${SMSLY_PRE_UPDATE_HEAD:-}" ]; then
        prev_hash="$SMSLY_PRE_UPDATE_HEAD"
    else
        prev_hash=$(git rev-parse HEAD  || echo "unknown")
    fi
    local prev_branch; prev_branch=$(git rev-parse --abbrev-ref HEAD  || echo "master")
    local backup_file="$BACKUP_DIR/pre-update-$(date +%Y%m%d-%H%M%S).sql"

    _progress "Backing up database (timeout: 120s)..."
    if timeout -k 5 120 docker exec -e PGPASSWORD="${POSTGRES_PASSWORD:-}" smsly-postgres-primary pg_dump --clean --if-exists -U "$POSTGRES_USER" "$POSTGRES_DB" > "$backup_file" ; then
        _ok "DB backup: $(du -h "$backup_file" | cut -f1)"
    else
        _warn "DB backup failed — continuing without safety net"
        backup_file=""
    fi

    # Backup .env (restored on rollback)
    if [ -f "$INSTALL_DIR/.env" ]; then
        cp "$INSTALL_DIR/.env" "$BACKUP_DIR/pre-update.env"  && _ok ".env backed up" || _warn ".env backup failed"
    fi

    # Backup registry auth material alongside .env. The deploy step
    # generates REGISTRY_PASSWORD into .env AND writes auth/htpasswd from
    # it as a pair; restoring a pre-password .env while keeping a
    # post-generation htpasswd (or vice versa) permanently breaks every
    # registry push with "no basic auth credentials" (2026-09-19).
    if [ -d "$INSTALL_DIR/auth" ]; then
        rm -rf "$BACKUP_DIR/pre-update.auth" 2>/dev/null || true
        cp -a "$INSTALL_DIR/auth" "$BACKUP_DIR/pre-update.auth"  && _ok "registry auth backed up" || _warn "registry auth backup failed"
    fi

    # Redis RDB snapshot (non-fatal — container may not be running)
    local _redis_ctr="smsly-redis-primary"
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$_redis_ctr"; then
        _redis_ctr="smsly-hosting-redis-1"
    fi
    if timeout -k 5 10 docker exec -e REDISCLI_AUTH="${REDIS_PASSWORD:-}" "$_redis_ctr" redis-cli SAVE ; then
        _ok "Redis RDB saved"
        timeout 10 docker cp "${_redis_ctr}:/data/dump.rdb" "$BACKUP_DIR/pre-update-redis.rdb" || echo -e "${YELLOW}    ⚠ Redis dump copy failed${NC}"
    else
        _warn "Redis backup skipped (container may not be running)"
    fi

    # RabbitMQ definitions export (non-fatal — container or rabbitmqadmin may not be available)
    if timeout -k 5 15 docker exec smsly-hosting-rabbitmq-1 rabbitmqadmin export "$BACKUP_DIR/pre-update-rabbitmq-defs.json" ; then
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
SNAPSHOT_EPOCH=$(date +%s)
BACKUP_FILE=$backup_file
SNAPEOF
    umask "$_prev_umask"
    _ok "Snapshot: $prev_hash"
    return 0
}

# ══════════════════════════════════════════════════════════════════════════════
safe_update_post_verify() {
    _step "Post-Deploy Health Check"
    local failed=0
    local tier0_failed=0
    local is_agent_lite=false is_node_mode=false
    if [ "${MODE_AGENT_LITE:-false}" = "true" ] || [ "${NODE_TYPE:-}" = "agent-lite" ] || [ "${MODE:-}" = "agent" ] || [[ "${COMPOSE_FILE:-}" == */docker-compose.agent-lite.yml ]]; then
        is_agent_lite=true
    elif [ "${MODE_NODE:-false}" = "true" ] || [ "${NODE_TYPE:-}" = "node" ] || [ "${MODE:-}" = "node" ] || [[ "${COMPOSE_FILE:-}" == */docker-compose.node.yml ]]; then
        is_node_mode=true
    fi
    local db_ha_mode="${DB_HA_ENABLED:-local-ha}"

    local _su_rmq=()
    if [ "${CELERY_BROKER_MODE:-redis}" = "amqp" ] || [[ "${CELERY_BROKER_URL:-}" =~ ^amqps?:// ]]; then
        _su_rmq=(smsly-hosting-rabbitmq-1)
    fi

    local tier0=()
    local core=()
    if [ "$is_agent_lite" = "true" ]; then
        tier0=(smsly-hosting-redis-1 smsly-hosting-backend-1 smsly-hosting-traefik-1)
        core=(smsly-hosting-redis-1 "${_su_rmq[@]}" smsly-hosting-backend-1
              smsly-hosting-celery-worker-1 smsly-hosting-socket-proxy-1 smsly-hosting-traefik-1)
    elif [ "$is_node_mode" = "true" ]; then
        tier0=(smsly-postgres-primary smsly-hosting-redis-1 smsly-hosting-backend-1 smsly-hosting-traefik-1)
        core=(smsly-postgres-primary smsly-hosting-redis-1 "${_su_rmq[@]}"
              smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-celery-beat-1
              smsly-hosting-socket-proxy-1 smsly-hosting-traefik-1)
    else
        local db_ctrs=()
        case "$db_ha_mode" in
            patroni)  db_ctrs=(smsly-patroni-1 smsly-haproxy) ;;
            external) db_ctrs=() ;;
            *)        db_ctrs=(smsly-postgres-primary) ;;
        esac
        tier0=("${db_ctrs[@]}" smsly-redis-primary smsly-hosting-pgcat-1
               smsly-hosting-backend-1 smsly-hosting-frontend-1 smsly-hosting-caddy-1)
        core=("${db_ctrs[@]}" smsly-redis-primary "${_su_rmq[@]}"
              smsly-hosting-backend-1 smsly-hosting-frontend-1 smsly-hosting-caddy-1
              smsly-hosting-celery-1 smsly-hosting-celery-beat-1
              smsly-hosting-socket-proxy-1 smsly-hosting-registry-1
              smsly-hosting-pgcat-1)
    fi
    local observability=(smsly-loki smsly-prometheus smsly-grafana smsly-promtail
                         smsly-docker-labels)

    for ctr in "${core[@]}"; do
        if docker inspect "$ctr" --format='{{.State.Running}}' | grep -q true; then
            _ok "$ctr"
        else
            _fail "$ctr NOT running"
            failed=$((failed + 1))
            for t0 in "${tier0[@]}"; do
                if [ "$ctr" = "$t0" ]; then
                    tier0_failed=$((tier0_failed + 1))
                    break
                fi
            done
        fi
    done

    local obs_file="$INSTALL_DIR/infrastructure/docker/docker-compose.observability.yml"
    if [ "$is_agent_lite" != "true" ] && [ "$is_node_mode" != "true" ] && [ -f "$obs_file" ]; then
        for ctr in "${observability[@]}"; do
            docker inspect "$ctr" --format='{{.State.Running}}'  | grep -q true && \
                _ok "$ctr" || { _warn "$ctr not running (observability stack may not be deployed)"; }
        done
        curl -sf --max-time 10 http://localhost:3100/ready  && _ok "Loki: ready" || { _warn "Loki: not ready"; }
        curl -sf --max-time 10 http://127.0.0.1:9090/api/v1/targets  && _ok "Prometheus: responding" || { _warn "Prometheus: not responding"; }
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
        curl -sf --max-time 10 http://127.0.0.1:8082/ping  && { _ok "Traefik: responding"; traefik_ok=true; break; }
        [ "$i" -lt 5 ] && sleep 10
    done
    [ "$traefik_ok" = "true" ] || { _warn "Traefik: not responding"; failed=$((failed + 1)); }

    if [ "$tier0_failed" -gt 0 ]; then
        _fail "$tier0_failed tier-0 critical service(s) down — failing verification"
        return 1
    elif [ "$failed" -eq 0 ]; then
        _ok "All core health checks passed"
    elif [ "$failed" -le 2 ]; then
        _warn "$failed non-tier-0 core health check(s) failed — tolerable"
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
        git fetch origin "${PREV_BRANCH:-master}"  || true
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

    # Restore registry auth material with .env (keeps the pair consistent —
    # see snapshot note above; htpasswd alone would keep a password .env
    # no longer has).
    if [ -d "$BACKUP_DIR/pre-update.auth" ]; then
        rm -rf "$INSTALL_DIR/auth" 2>/dev/null || true
        cp -a "$BACKUP_DIR/pre-update.auth" "$INSTALL_DIR/auth"  && _ok "registry auth restored" || _warn "registry auth restore failed"
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

    local _avail_svcs=""
    _avail_svcs="$(docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null || true)"

    # Ensure the database is up before restoring into it.
    if printf '%s\n' "$_avail_svcs" | grep -qx "db"; then
        docker compose -f "$COMPOSE_FILE" up -d --wait --no-deps db \
            || _warn "Could not ensure postgres-primary is running"
    fi

    # Stop any app containers left from the failed attempt so the --clean dump
    # does not drop objects while they are connected (avoids partial restore).
    local _stop_svcs=()
    for _s in backend frontend celery celery-worker celery-deploy celery-fast celery-beat pgcat; do
        if printf '%s\n' "$_avail_svcs" | grep -qx "$_s"; then
            _stop_svcs+=("$_s")
        fi
    done
    if [ "${#_stop_svcs[@]}" -gt 0 ]; then
        docker compose -f "$COMPOSE_FILE" stop --timeout 30 "${_stop_svcs[@]}" 2>/dev/null || true
    fi

    # Restore DB BEFORE bringing app containers up, so the --clean dump does not
    # drop objects while backend/celery are connected (transient errors and can
    # leave behind new-version tables from a partial migration).
    if [ -n "${BACKUP_FILE:-}" ] && [ -f "$BACKUP_FILE" ]; then
        _warn "Restoring database from backup..."
        timeout -k 5 600 docker exec -i -e PGPASSWORD="${POSTGRES_PASSWORD:-}" smsly-postgres-primary psql -U "$POSTGRES_USER" "$POSTGRES_DB" < "$BACKUP_FILE"  && \
            _ok "DB restored" || _warn "DB restore failed"
    fi

    # Heal detached coredns bind volume / runtime perms before bringing services back up
    if declare -F heal_detached_coredns_volume >/dev/null 2>&1; then
        heal_detached_coredns_volume || true
    fi
    if declare -F ensure_infrastructure_permissions >/dev/null 2>&1; then
        ensure_infrastructure_permissions || true
    fi

    # Restart core services with the restored images (DB is now consistent).
    local _up_svcs=()
    for _s in backend frontend celery celery-worker celery-deploy celery-fast celery-beat pgcat; do
        if printf '%s\n' "$_avail_svcs" | grep -qx "$_s"; then
            _up_svcs+=("$_s")
        fi
    done
    if [ "${#_up_svcs[@]}" -gt 0 ]; then
        docker compose -f "$COMPOSE_FILE" up -d --force-recreate --wait --no-deps "${_up_svcs[@]}" || _warn "Docker compose up had issues during rollback"
    fi

    # Fix perms
    [ -d /opt/smsly-hosting/prometheus-targets ] && {
        chown -R 1000:1000 /opt/smsly-hosting/prometheus-targets  || true
        chmod 2777 /opt/smsly-hosting/prometheus-targets  || true
    }

    sleep 30
    safe_update_post_verify && _ok "Rollback successful" || _warn "Rollback done, some services unhealthy"
    rm -f "$SNAPSHOT_FILE" 2>/dev/null || true
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
    # Remove only explicitly ephemeral exited containers (preserve sleeping tiers and stopped addons)
    for c_id in $(docker ps -a -q --filter "label=com.smsly.ephemeral=true" --filter "status=exited"  || true); do
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
    safe_update_preflight || exit 1
    safe_update_snapshot
    bash "$INSTALL_DIR/install.sh" --update >> "$CLEAN_LOG"  || { _warn "Update failed — rolling back"; safe_update_rollback; exit 1; }
    sleep 30
    safe_update_post_verify || { _warn "Post-verify failed — rolling back"; safe_update_rollback; exit 1; }
    safe_update_cleanup
    echo -e "\n${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✓ UPDATE SUCCESSFUL — All systems healthy${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    rm -f "$SNAPSHOT_FILE"
fi

