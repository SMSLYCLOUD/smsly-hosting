#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Celery Worker Autoscaler — idle-minimal, burst-allowed
#
# Monitors queue depth in RabbitMQ and stops/starts the BURST workers
# (celery-fast, celery-deploy) based on configurable thresholds.
#
# Safety: the primary `celery` worker drains ALL queues
# (CELERY_QUEUES=celery,fast,deploy, enforced by the installer), so a
# scaled-down burst worker only slows the drain — work never stalls.
# celery-beat (scheduler) is never touched. Worst case added latency on
# an ice-cold queue is ~scale_up_after + container start (~70s),
# negligible next to multi-minute deploy tasks.
#
# Designed to run as a systemd service on the host.
#
# Environment (read from INSTALL_DIR/.env):
#   CELERY_AUTOSCALE_ENABLED   — "true" to enable (default: true)
#   CELERY_SCALE_UP_THRESHOLD  — queue depth to trigger scale-up (default: 50)
#   CELERY_SCALE_DOWN_THRESHOLD— queue depth to trigger scale-down (default: 5)
#   CELERY_SCALE_UP_AFTER      — seconds above threshold before scale-up (default: 60)
#   CELERY_SCALE_DOWN_AFTER    — seconds below threshold before scale-down (default: 120)
#   CELERY_SCALE_CHECK_INTERVAL— polling interval in seconds (default: 15)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="${SMSLY_INSTALL_DIR:-/opt/smsly-hosting}"
COMPOSE_FILE="${INSTALL_DIR}/docker-compose.prod.yml"
ENV_FILE="${INSTALL_DIR}/.env"
LOG_TAG="celery-autoscaler"

# Burst capacity: stopped when idle, started on pressure. The primary
# `celery` worker + `celery-beat` are always on and are never managed here.
# (Older revisions named these celery-2/celery-3 — services that never
# existed in docker-compose.prod.yml, so scale-up was a silent no-op.)
BURST_WORKERS=(celery-fast celery-deploy)

# Resolved at startup against the ACTIVE compose file: agent-lite/node
# modes have no burst workers, and `up -d` of a missing service fails —
# under `set -o pipefail` that failure would exit this loop and systemd
# (Restart=on-failure) would crash-loop it. Resolve once, exit cleanly
# when there is nothing to manage.
MANAGED_WORKERS=()
_resolve_managed_workers() {
    local available=""
    available="$(docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null || true)"
    local svc=""
    for svc in "${BURST_WORKERS[@]}"; do
        if printf '%s\n' "$available" | grep -qx "$svc"; then
            MANAGED_WORKERS+=("$svc")
        else
            _log WARN "Service $svc not in active compose file — will not manage it"
        fi
    done
}

# ── Load config from .env ────────────────────────────────────────────────────
_load_env() {
    # shellcheck disable=SC1090
    [ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
}

_log() {
    local level="$1"; shift
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$LOG_TAG] [$level] $*"
    logger -t "$LOG_TAG" "[$level] $*" 2>/dev/null || true
}

# ── Query RabbitMQ for queue depth ───────────────────────────────────────────
# Uses rabbitmqctl inside the container — no management API port exposure needed.
# NOTE: the compose service has no `container_name:` (runtime name is
# `smsly-hosting-rabbitmq-1`), so `docker exec rabbitmq` never resolves.
# Always go through _rabbitmq_container() (compose ps -q).
_rabbitmq_container() {
    docker compose -f "$COMPOSE_FILE" ps -q rabbitmq 2>/dev/null | head -n 1
}
# Sums depth across ALL platform queues. Grepping only '^celery' missed the
# 'deploy' and 'fast' queues where most heavy work lands (see config/celery.py
# task_routes), so the scaler never triggered on deploy-queue backpressure.
# Probe helpers return non-zero when RabbitMQ is unreachable so the main
# loop can tell "idle" apart from "blind". A blind tick must never scale
# (in either direction): stopping workers during a broker outage, or
# starting them on phantom depth, both make recovery worse.
_get_queue_depth() {
    local total=0
    local raw
    local mq
    mq="$(_rabbitmq_container)"
    [ -n "$mq" ] || return 1
    if ! raw=$(timeout 10 docker exec "$mq" rabbitmqctl list_queues name messages 2>/dev/null); then
        return 1
    fi
    raw=$(echo "$raw" | grep -E '^(celery|deploy|fast|media-telemetry|media-audit)\b' \
        | awk '{print $2}' \
        || echo "0")
    for n in $raw; do
        total=$((total + n))
    done
    echo "$total"
}

# ── In-flight work on the burst queues ───────────────────────────────────────
# messages_unacknowledged = delivered to a burst worker but not finished.
# `stop --timeout 15` SIGKILLs after the grace period, so stopping a worker
# with unacked tasks can murder a running (multi-minute) deploy. Scale-down
# requires this to be 0 — an idle queue with running tasks still waits.
_get_burst_unacked() {
    local total=0
    local raw
    local mq
    mq="$(_rabbitmq_container)"
    [ -n "$mq" ] || return 1
    if ! raw=$(timeout 10 docker exec "$mq" rabbitmqctl list_queues name messages_unacknowledged 2>/dev/null); then
        return 1
    fi
    raw=$(echo "$raw" | grep -E '^(deploy|fast)\b' \
        | awk '{print $2}' \
        || echo "0")
    for n in $raw; do
        total=$((total + n))
    done
    echo "$total"
}

# ── Worker state tracking ────────────────────────────────────────────────────
_worker_running() {
    local svc="$1"
    docker compose -f "$COMPOSE_FILE" ps "$svc" 2>/dev/null | grep -q "Up"
}

_scale_up() {
    _log INFO "Scaling UP: starting ${MANAGED_WORKERS[*]}"
    docker compose -f "$COMPOSE_FILE" up -d --no-deps "${MANAGED_WORKERS[@]}" 2>&1 | while read -r line; do
        _log INFO "  $line"
    done
}

_scale_down() {
    # `stop` (not rm): containers keep their state for a ~2s resume, and a
    # later full `up` (update/refresh flows) restarts them fail-open.
    _log INFO "Scaling DOWN: stopping ${MANAGED_WORKERS[*]}"
    docker compose -f "$COMPOSE_FILE" stop --timeout 15 "${MANAGED_WORKERS[@]}" 2>&1 | while read -r line; do
        _log INFO "  $line"
    done
}

# ── Main loop ────────────────────────────────────────────────────────────────
main() {
    _load_env

    local enabled="${CELERY_AUTOSCALE_ENABLED:-true}"
    if [ "$enabled" != "true" ]; then
        _log INFO "Autoscaler disabled (CELERY_AUTOSCALE_ENABLED != true). Exiting."
        exit 0
    fi

    local scale_up_threshold="${CELERY_SCALE_UP_THRESHOLD:-50}"
    local scale_down_threshold="${CELERY_SCALE_DOWN_THRESHOLD:-5}"
    local scale_up_after="${CELERY_SCALE_UP_AFTER:-60}"
    local scale_down_after="${CELERY_SCALE_DOWN_AFTER:-120}"
    local check_interval="${CELERY_SCALE_CHECK_INTERVAL:-15}"

    _log INFO "Starting — burst workers: ${BURST_WORKERS[*]} up_threshold=$scale_up_threshold down_threshold=$scale_down_threshold up_after=${scale_up_after}s down_after=${scale_down_after}s interval=${check_interval}s"

    _resolve_managed_workers
    if [ "${#MANAGED_WORKERS[@]}" -eq 0 ]; then
        _log INFO "No burst workers in this compose file (agent-lite/node mode?) — nothing to manage. Exiting."
        exit 0
    fi

    local above_threshold_since=0
    local below_threshold_since=0
    local extra_workers_up=false

    # Check initial state (only managed workers — never probe unmanaged ones)
    local _svc=""
    for _svc in "${MANAGED_WORKERS[@]}"; do
        if _worker_running "$_svc"; then
            extra_workers_up=true
            break
        fi
    done
    if [ "$extra_workers_up" = "true" ]; then
        _log INFO "Burst workers already running at startup"
    fi

    while true; do
        sleep "$check_interval"

        local depth
        if ! depth=$(_get_queue_depth); then
            _log WARN "RabbitMQ unreachable — skipping tick (no scale action blind)"
            continue
        fi

        local now
        now=$(date +%s)

        if [ "$depth" -ge "$scale_up_threshold" ]; then
            below_threshold_since=0
            [ "$above_threshold_since" -eq 0 ] && above_threshold_since=$now
            local elapsed=$((now - above_threshold_since))

            if [ "$extra_workers_up" = "false" ] && [ "$elapsed" -ge "$scale_up_after" ]; then
                _log INFO "Queue depth $depth >= $scale_up_threshold for ${elapsed}s — scaling up"
                _scale_up
                extra_workers_up=true
                above_threshold_since=0
            fi

        elif [ "$depth" -le "$scale_down_threshold" ]; then
            above_threshold_since=0
            [ "$below_threshold_since" -eq 0 ] && below_threshold_since=$now
            local elapsed=$((now - below_threshold_since))

            if [ "$extra_workers_up" = "true" ] && [ "$elapsed" -ge "$scale_down_after" ]; then
                local unacked
                if ! unacked=$(_get_burst_unacked); then
                    _log WARN "RabbitMQ unreachable during scale-down check — deferring"
                    below_threshold_since=$now
                elif [ "${unacked:-0}" -gt 0 ] 2>/dev/null; then
                    _log INFO "Queue idle but $unacked task(s) still running on burst workers — deferring scale-down"
                    below_threshold_since=$now
                else
                    _log INFO "Queue depth $depth <= $scale_down_threshold for ${elapsed}s — scaling down"
                    _scale_down
                    extra_workers_up=false
                    below_threshold_since=0
                fi
            fi

        else
            # Between thresholds — reset both timers
            above_threshold_since=0
            below_threshold_since=0
        fi
    done
}

main "$@"
