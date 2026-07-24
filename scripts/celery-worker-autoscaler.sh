#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Celery Worker Autoscaler
#
# Monitors the celery queue depth in RabbitMQ and starts/stops celery-2 and
# celery-3 based on configurable thresholds.  The primary celery worker
# (celery-1) is always running — only the extra workers are scaled.
#
# Designed to run as a systemd service on the host.
#
# Environment (read from INSTALL_DIR/.env):
#   CELERY_AUTOSCALE_ENABLED   — "true" to enable (default: false)
#   CELERY_SCALE_UP_THRESHOLD  — queue depth to trigger scale-up (default: 50)
#   CELERY_SCALE_DOWN_THRESHOLD— queue depth to trigger scale-down (default: 5)
#   CELERY_SCALE_UP_AFTER      — seconds above threshold before scale-up (default: 60)
#   CELERY_SCALE_DOWN_AFTER    — seconds below threshold before scale-down (default: 300)
#   CELERY_SCALE_CHECK_INTERVAL— polling interval in seconds (default: 15)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="${SMSLY_INSTALL_DIR:-/opt/smsly-hosting}"
COMPOSE_FILE="${INSTALL_DIR}/docker-compose.prod.yml"
ENV_FILE="${INSTALL_DIR}/.env"
LOG_TAG="celery-autoscaler"

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
_get_queue_depth() {
    local total=0
    local raw
    raw=$(docker exec rabbitmq rabbitmqctl list_queues name messages 2>/dev/null \
        | grep -E '^celery\b' \
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
    _log INFO "Scaling UP: starting celery-2 and celery-3"
    docker compose -f "$COMPOSE_FILE" --profile extra-workers up -d --no-deps celery-2 celery-3 2>&1 | while read -r line; do
        _log INFO "  $line"
    done
}

_scale_down() {
    _log INFO "Scaling DOWN: stopping celery-2 and celery-3"
    docker compose -f "$COMPOSE_FILE" stop celery-2 celery-3 2>&1 | while read -r line; do
        _log INFO "  $line"
    done
}

# ── Main loop ────────────────────────────────────────────────────────────────
main() {
    _load_env

    local enabled="${CELERY_AUTOSCALE_ENABLED:-false}"
    if [ "$enabled" != "true" ]; then
        _log INFO "Autoscaler disabled (CELERY_AUTOSCALE_ENABLED != true). Exiting."
        exit 0
    fi

    local scale_up_threshold="${CELERY_SCALE_UP_THRESHOLD:-50}"
    local scale_down_threshold="${CELERY_SCALE_DOWN_THRESHOLD:-5}"
    local scale_up_after="${CELERY_SCALE_UP_AFTER:-60}"
    local scale_down_after="${CELERY_SCALE_DOWN_AFTER:-300}"
    local check_interval="${CELERY_SCALE_CHECK_INTERVAL:-15}"

    _log INFO "Starting — up_threshold=$scale_up_threshold down_threshold=$scale_down_threshold up_after=${scale_up_after}s down_after=${scale_down_after}s interval=${check_interval}s"

    local above_threshold_since=0
    local below_threshold_since=0
    local extra_workers_up=false

    # Check initial state
    if _worker_running celery-2 || _worker_running celery-3; then
        extra_workers_up=true
        _log INFO "Extra workers already running at startup"
    fi

    while true; do
        sleep "$check_interval"

        local depth
        depth=$(_get_queue_depth)

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
                _log INFO "Queue depth $depth <= $scale_down_threshold for ${elapsed}s — scaling down"
                _scale_down
                extra_workers_up=false
                below_threshold_since=0
            fi

        else
            # Between thresholds — reset both timers
            above_threshold_since=0
            below_threshold_since=0
        fi
    done
}

main "$@"
