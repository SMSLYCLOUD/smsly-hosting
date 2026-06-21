#!/bin/sh
# Traefik watchdog: restart Traefik if it becomes unresponsive
# (symptom: API stops responding but process is still alive,
# usually because the Docker provider is stuck on a slow socket-proxy call).
set -e

TRAEFIK_CONTAINER="${TRAEFIK_CONTAINER:-smsly-hosting-traefik-1}"
CHECK_INTERVAL="${CHECK_INTERVAL:-30}"
API_TIMEOUT="${API_TIMEOUT:-5}"
MAX_FAILURES="${MAX_FAILURES:-3}"

failures=0
while true; do
    # Check if Traefik is running
    if ! docker inspect --format='{{.State.Running}}' "$TRAEFIK_CONTAINER" 2>/dev/null | grep -q true; then
        echo "[$(date -Iseconds)] $TRAEFIK_CONTAINER is not running, skipping check"
        sleep "$CHECK_INTERVAL"
        continue
    fi

    # Check if Traefik API responds (use the metrics endpoint which is lighter than /api)
    if docker exec "$TRAEFIK_CONTAINER" wget -q -O /dev/null --timeout="$API_TIMEOUT" http://127.0.0.1:8082/ping 2>/dev/null; then
        failures=0
    else
        failures=$((failures + 1))
        echo "[$(date -Iseconds)] $TRAEFIK_CONTAINER ping failed ($failures/$MAX_FAILURES)"

        if [ "$failures" -ge "$MAX_FAILURES" ]; then
            echo "[$(date -Iseconds)] Restarting $TRAEFIK_CONTAINER"
            docker restart "$TRAEFIK_CONTAINER" 2>/dev/null || true
            failures=0
            sleep 30  # Give Traefik time to recover after restart
        fi
    fi

    sleep "$CHECK_INTERVAL"
done
