#!/bin/bash
# =============================================================================
# Caddy Docker Reload Watcher
# Watches for .reload flag from the backend container and reloads the
# Caddy Docker container from the host (bypassing the socket-proxy).
#
# Run as a systemd service: caddy-docker-watcher.service
# =============================================================================

set -euo pipefail

WATCH_DIR="${1:-/opt/smsly-hosting/caddy-config}"
CADDY_CONF="$WATCH_DIR/Caddyfile"
RELOAD_FLAG="$WATCH_DIR/.reload"
LAST_GOOD_CONF="$WATCH_DIR/Caddyfile.smsly-last-good"
CONTAINER_NAME="smsly-hosting-caddy-1"
LOG_PREFIX="[caddy-docker-watcher]"

echo "$LOG_PREFIX Starting — watching $WATCH_DIR for .reload flag"

candidate_requires_https() {
    local candidate="$1"
    awk '
        /^[[:space:]]*#/ { next }
        /^[[:space:]]*$/ { next }
        /^[[:space:]]*\{/ { next }
        /^:[0-9]+[[:space:]]*\{/ { next }
        /^http:\/\// { next }
        /^([*][.])?[A-Za-z0-9_.-]+[[:space:]]*\{/ {
            host = $1
            sub(/[{].*/, "", host)
            if (host != "localhost" && host !~ /^([0-9]{1,3}[.]){3}[0-9]{1,3}$/) {
                found = 1
            }
        }
        END { exit found ? 0 : 1 }
    ' "$candidate"
}

https_listener_active() {
    ss -H -tln  | awk '{print $4}' | grep -Eq ':443$'
}

container_is_running() {
    docker inspect --format='{{.State.Running}}' "$CONTAINER_NAME"  | grep -q true
}

reload_caddy() {
    local attempt=1
    local max_attempts=3
    local delay=2

    while [ $attempt -le $max_attempts ]; do
        echo "$LOG_PREFIX Reload attempt $attempt/$max_attempts"

        if ! container_is_running; then
            echo "$LOG_PREFIX WARNING: Container $CONTAINER_NAME is not running, attempting start..."
            docker start "$CONTAINER_NAME" || echo -e "${YELLOW}    ⚠ Failed to start $CONTAINER_NAME${NC}"
            sleep 3
        fi

        # Try caddy reload inside the container
        if docker exec "$CONTAINER_NAME" caddy reload --config /etc/caddy/Caddyfile ; then
            echo "$LOG_PREFIX Caddy reloaded successfully via docker exec"
            sleep 2
            if candidate_requires_https "$CADDY_CONF" && ! https_listener_active; then
                echo "$LOG_PREFIX ERROR: TCP 443 not listening after reload, retrying..."
                docker restart "$CONTAINER_NAME" || echo -e "${YELLOW}    ⚠ Failed to restart $CONTAINER_NAME${NC}"
                sleep 5
                if ! https_listener_active; then
                    echo "$LOG_PREFIX ERROR: TCP 443 still not active, falling back to restart"
                fi
            fi
            cp "$CADDY_CONF" "$LAST_GOOD_CONF"  || true
            return 0
        fi

        echo "$LOG_PREFIX Docker exec failed, trying docker restart..."
        if docker restart "$CONTAINER_NAME" ; then
            echo "$LOG_PREFIX Container restarted"
            sleep 5
            if candidate_requires_https "$CADDY_CONF" && ! https_listener_active; then
                echo "$LOG_PREFIX ERROR: TCP 443 not listening after restart"
            else
                cp "$CADDY_CONF" "$LAST_GOOD_CONF"  || true
                return 0
            fi
        fi

        echo "$LOG_PREFIX Attempt $attempt failed, retrying in ${delay}s..."
        sleep $delay
        delay=$((delay * 2))
        attempt=$((attempt + 1))
    done

    echo "$LOG_PREFIX ERROR: All reload attempts failed"
    return 1
}

while true; do
    if [ -f "$RELOAD_FLAG" ]; then
        echo "$LOG_PREFIX Reload flag detected at $(date)"

        if [ -f "$CADDY_CONF" ]; then
            # Validate the config first
            CANDIDATE="$(mktemp /tmp/smsly-caddy-dwatch.XXXXXX)"
            cp "$CADDY_CONF" "$CANDIDATE"

            if caddy validate --config "$CANDIDATE" ; then
                echo "$LOG_PREFIX Config validated OK"
                # Copy validated config into the container's volume
                docker cp "$CADDY_CONF" "$CONTAINER_NAME:/etc/caddy/Caddyfile"  || true
                reload_caddy
            else
                echo "$LOG_PREFIX ERROR: Config validation failed — NOT applying"
            fi
            rm -f "$CANDIDATE"
        else
            echo "$LOG_PREFIX WARNING: No Caddyfile found at $CADDY_CONF"
        fi

        rm -f "$RELOAD_FLAG"
        echo "$LOG_PREFIX Reload cycle complete"
    fi

    sleep 2
done
