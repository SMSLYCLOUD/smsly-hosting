#!/bin/bash
# =============================================================================
# Caddy Config Watcher
# Watches for Caddyfile changes from the backend container and reloads Caddy.
# Run as a systemd service: caddy-watcher.service
# =============================================================================

set -euo pipefail

WATCH_DIR="${1:-/opt/smsly-hosting/caddy-config}"
CADDY_CONF="/etc/caddy/Caddyfile"
RELOAD_FLAG="$WATCH_DIR/.reload"
LOG_PREFIX="[caddy-watcher]"

echo "$LOG_PREFIX Starting — watching $WATCH_DIR for changes"

# Load Cloudflare token if available (needed for validation of wildcard configs)
if [ -f /etc/systemd/system/caddy.service.d/override.conf ]; then
    eval "$(grep '^Environment=' /etc/systemd/system/caddy.service.d/override.conf | sed 's/^Environment="//;s/"$//;s/^/export /')"
    echo "$LOG_PREFIX Loaded Cloudflare env from Caddy service override"
fi

# Ensure watch directory exists
mkdir -p "$WATCH_DIR"

while true; do
    # Check for reload flag
    if [ -f "$RELOAD_FLAG" ]; then
        echo "$LOG_PREFIX Reload flag detected"

        WATCH_CADDY="$WATCH_DIR/Caddyfile"
        if [ -f "$WATCH_CADDY" ]; then
            # Validate before applying
            if caddy validate --config "$WATCH_CADDY" 2>&1; then
                echo "$LOG_PREFIX Validation passed — applying"
                cp "$WATCH_CADDY" "$CADDY_CONF"
                systemctl reload caddy 2>&1 || systemctl restart caddy 2>&1
                echo "$LOG_PREFIX Caddy reloaded successfully"
            else
                echo "$LOG_PREFIX ERROR: Caddyfile validation failed — NOT applying"
            fi
        else
            echo "$LOG_PREFIX WARNING: No Caddyfile found in $WATCH_DIR"
        fi

        # Remove flag regardless
        rm -f "$RELOAD_FLAG"
    fi

    sleep 2
done
