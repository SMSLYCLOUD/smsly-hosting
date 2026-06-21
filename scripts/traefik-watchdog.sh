#!/bin/sh
# Traefik watchdog: restart Traefik if it becomes unresponsive
# (symptom: API stops responding but process is still alive,
# usually because the Docker provider is stuck on a slow socket-proxy call).
set -e

TRAEFIK_CONTAINER="${TRAEFIK_CONTAINER:-smsly-hosting-traefik-1}"
TRAEFIK_METRICS_HOST="${TRAEFIK_METRICS_HOST:-smsly-hosting-traefik-1}"
TRAEFIK_METRICS_PORT="${TRAEFIK_METRICS_PORT:-8082}"
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

    # Check if Traefik /ping endpoint responds. Connect from the watchdog
    # container itself (which is on smsly-net with Traefik) rather than
    # via 'docker exec' — the traefik:v3.6 image is distroless and has
    # no wget/curl/nc, so exec-based checks always fail.
    #
    # The /ping endpoint returns 200 OK with body "OK" when Traefik is ready.
    # The watchdog container is based on docker:cli which has curl.
    if curl -fsS --max-time "$API_TIMEOUT" --connect-timeout "$API_TIMEOUT" \
            "http://${TRAEFIK_METRICS_HOST}:${TRAEFIK_METRICS_PORT}/ping" \
            > /dev/null 2>&1; then
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
