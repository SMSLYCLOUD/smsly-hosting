#!/usr/bin/env bash
# Monitor critical SMSLY hosting containers on the primary server
# and restart them if they are not running or are unhealthy.

set -euo pipefail

INSTALL_DIR="/opt/smsly-hosting"
COMPOSE_FILE="$INSTALL_DIR/docker-compose.prod.yml"
LOG_TAG="smsly-infra-monitor"

log() {
    logger -t "$LOG_TAG" "$*" 2>/dev/null || true
    printf '[%s] %s\n' "$LOG_TAG" "$*"
}

SERVICES=("db" "redis" "rabbitmq" "pgcat" "backend" "celery" "caddy")

if [ ! -f "$COMPOSE_FILE" ]; then
    log "Error: Compose file not found at $COMPOSE_FILE"
    exit 1
fi

# ─── Zombie Process Cleanup ─────────────────────────────────────────────
zombies=$(ps -eo pid=,ppid=,stat=,comm= 2>/dev/null | awk '$3 ~ /^Z/ {print $1":"$2":"$4}' || true)
if [ -n "$zombies" ]; then
    zombie_count=$(echo "$zombies" | wc -l)
    log "Zombie processes: $zombie_count. Sending SIGCHLD to parents..."
    echo "$zombies" | while IFS=: read -r pid ppid comm; do
        kill -s SIGCHLD "$ppid" 2>/dev/null || true
    done
    sleep 1
    remaining=$(ps -eo pid=,stat= 2>/dev/null | awk '$2 ~ /^Z/' | wc -l)
    if [ "$remaining" -gt 0 ]; then
        log "Warning: $remaining zombie(s) remain. Unreapable zombies:"
        ps -eo pid=,ppid=,stat=,comm= 2>/dev/null | awk '$3 ~ /^Z/ {print "  PID="$1" PPID="$2" CMD="$4}' | while read -r line; do
            log "$line"
        done
    else
        log "All zombie processes reaped successfully"
    fi
fi

for service in "${SERVICES[@]}"; do
    container_id=$(docker compose -f "$COMPOSE_FILE" ps -q "$service" 2>/dev/null || true)
    
    if [ -z "$container_id" ]; then
        log "Warning: Container for service '$service' is missing. Attempting to start..."
        docker compose -f "$COMPOSE_FILE" up -d "$service"
        continue
    fi
    
    inspect_data=$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null || true)
    
    if [ -z "$inspect_data" ]; then
        log "Warning: Failed to inspect container '$container_id' for service '$service'. Attempting restart..."
        docker compose -f "$COMPOSE_FILE" restart "$service"
        continue
    fi
    
    status=$(echo "$inspect_data" | awk '{print $1}')
    health=$(echo "$inspect_data" | awk '{print $2}')
    
    if [ "$status" != "running" ]; then
        log "Alert: Container for service '$service' is not running (status: $status). Restarting..."
        docker compose -f "$COMPOSE_FILE" restart "$service"
    elif [ "$health" = "unhealthy" ]; then
        log "Alert: Container for service '$service' is running but UNHEALTHY. Restarting..."
        docker compose -f "$COMPOSE_FILE" restart "$service"
    fi
done
