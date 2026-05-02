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
LOG_PREFIX="[update-watcher]"

echo "$LOG_PREFIX Starting — watching $WATCH_DIR for updates"

# Ensure watch directory exists
mkdir -p "$WATCH_DIR"

while true; do
    # Check for update flag
    if [ -f "$UPDATE_FLAG" ]; then
        echo "$LOG_PREFIX Update flag detected — initiating platform update"
        
        # Remove flag first to prevent loop
        rm -f "$UPDATE_FLAG"
        
        if [ -f "$INSTALL_DIR/install.sh" ]; then
            cd "$INSTALL_DIR"
            # Run update in non-interactive mode.
            # We use --non-interactive if supported, otherwise just hope for the best.
            # Most of install.sh is already safe if env vars exist.
            echo "$LOG_PREFIX Executing: sudo bash install.sh --update"
            sudo bash install.sh --update --non-interactive >> /var/log/smsly-install.log 2>&1 || {
                echo "$LOG_PREFIX ERROR: Platform update failed. Check /var/log/smsly-install.log"
            }
            echo "$LOG_PREFIX Update process completed."
        else
            echo "$LOG_PREFIX ERROR: install.sh not found in $INSTALL_DIR"
        fi
    fi

    sleep 10
done
