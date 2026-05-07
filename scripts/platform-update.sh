#!/bin/bash
# =============================================================================
# Platform Update Watcher
# Watches for an .update flag from the backend container and triggers a pull/rebuild.
# Run as a systemd service: smsly-update-watcher.service
# =============================================================================

set -euo pipefail

WATCH_DIR="${1:-/opt/smsly-hosting/caddy-config}"
UPDATE_FLAG="$WATCH_DIR/.update"
INSTALL_DIR="/opt/smsly-hosting"
INSTALL_LOCK_FILE="/tmp/smsly-install.lock"
LOG_PREFIX="[update-watcher]"

echo "$LOG_PREFIX Starting — watching $WATCH_DIR for updates"

# Ensure watch directory exists
mkdir -p "$WATCH_DIR"

install_lock_active() {
    [ -f "$INSTALL_LOCK_FILE" ] || return 1

    if command -v flock >/dev/null 2>&1; then
        if (flock -n 9) 9<"$INSTALL_LOCK_FILE"; then
            return 1
        fi
        return 0
    fi

    local pid
    pid="$(cat "$INSTALL_LOCK_FILE" 2>/dev/null || true)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

while true; do
    # Check for update flag
    if [ -f "$UPDATE_FLAG" ]; then
        if install_lock_active; then
            echo "$LOG_PREFIX Installer lock is active; deferring platform update"
            sleep 10
            continue
        fi

        MODE=$(cat "$UPDATE_FLAG" | tr -d ' \n\r' || echo "update")
        [ -z "$MODE" ] && MODE="update"
        
        echo "$LOG_PREFIX Update flag detected (mode: $MODE) — initiating platform update"
        
        # Remove flag first to prevent loop
        rm -f "$UPDATE_FLAG"
        
        if [ -f "$INSTALL_DIR/install.sh" ]; then
            cd "$INSTALL_DIR"
            
            # Map mode to install.sh flags
            FLAGS="--update"
            if [ "$MODE" = "frontend" ]; then
                FLAGS="--update-frontend"
            fi

            echo "$LOG_PREFIX Executing in screen: sudo bash install.sh $FLAGS --non-interactive"
            # Kill any stale install screens before starting a new one
            sudo screen -ls | grep "Grid-install" | cut -d. -f1 | awk '{print $1}' | xargs sudo kill -9 2>/dev/null || true
            sudo screen -wipe > /dev/null 2>&1 || true

            # We run in a detached screen so it's "wrapped in screen" but won't block the watcher.
            # Output is still logged for persistence.
            sudo screen -S Grid-install -d -m bash -c "bash install.sh $FLAGS --non-interactive >> /var/log/smsly-install.log 2>&1"
            
            echo "$LOG_PREFIX Update process backgrounded in screen session 'Grid-install'."
        else
            echo "$LOG_PREFIX ERROR: install.sh not found in $INSTALL_DIR"
        fi
    fi

    sleep 10
done
