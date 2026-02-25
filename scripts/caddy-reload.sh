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
TOKEN_FILE="$WATCH_DIR/.cloudflare_token"
OVERRIDE_DIR="/etc/systemd/system/caddy.service.d"
OVERRIDE_CONF="$OVERRIDE_DIR/override.conf"
LOG_PREFIX="[caddy-watcher]"

echo "$LOG_PREFIX Starting — watching $WATCH_DIR for changes"

# Load Cloudflare token if available (needed for validation of wildcard configs)
load_cloudflare_env() {
    if [ -f "$OVERRIDE_CONF" ]; then
        eval "$(grep '^Environment=' "$OVERRIDE_CONF" | sed 's/^Environment="//;s/"$//;s/^/export /')"
        echo "$LOG_PREFIX Loaded Cloudflare env from Caddy service override"
    fi
}

# Sync Cloudflare token from shared volume to systemd override.
# This bridges the gap between the web UI (writes token to DB → shared volume)
# and Caddy (reads token from systemd environment).
sync_cloudflare_token() {
    if [ -f "$TOKEN_FILE" ]; then
        NEW_TOKEN=$(cat "$TOKEN_FILE")
        if [ -n "$NEW_TOKEN" ]; then
            # Check if override needs updating
            CURRENT_TOKEN=""
            if [ -f "$OVERRIDE_CONF" ]; then
                CURRENT_TOKEN=$(grep 'CLOUDFLARE_API_TOKEN=' "$OVERRIDE_CONF" 2>/dev/null | sed 's/.*CLOUDFLARE_API_TOKEN=//;s/"$//' || true)
            fi

            if [ "$NEW_TOKEN" != "$CURRENT_TOKEN" ]; then
                echo "$LOG_PREFIX Syncing Cloudflare token to systemd override"
                mkdir -p "$OVERRIDE_DIR"
                cat > "$OVERRIDE_CONF" <<ENVEOF
[Service]
Environment="CLOUDFLARE_API_TOKEN=$NEW_TOKEN"
ENVEOF
                chmod 600 "$OVERRIDE_CONF"
                systemctl daemon-reload 2>&1 || true
                # Export for caddy validate to use
                export CLOUDFLARE_API_TOKEN="$NEW_TOKEN"
                echo "$LOG_PREFIX Cloudflare token synced"
            else
                export CLOUDFLARE_API_TOKEN="$NEW_TOKEN"
            fi
        fi
        # Remove token file after processing (security: don't leave on disk)
        rm -f "$TOKEN_FILE"
    fi
}

load_cloudflare_env

# Ensure watch directory exists
mkdir -p "$WATCH_DIR"

while true; do
    # Check for reload flag
    if [ -f "$RELOAD_FLAG" ]; then
        echo "$LOG_PREFIX Reload flag detected"

        # Always reload token from systemd override (in case it was
        # updated since watcher startup or by a previous cycle).
        load_cloudflare_env

        # Sync Cloudflare token from shared volume BEFORE validation
        sync_cloudflare_token

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
