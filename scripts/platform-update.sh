#!/bin/bash
# =============================================================================
# Platform Update Watcher
# Watches for an .update flag from the backend container and triggers a pull/rebuild.
# Run as a systemd service: smsly-update-watcher.service
# =============================================================================

set -euo pipefail

export PATH="/usr/local/bin:$PATH"

WATCH_DIR="${1:-/opt/smsly-hosting/caddy-config}"
UPDATE_FLAG="$WATCH_DIR/.update"
STATUS_FILE="$WATCH_DIR/.update.status"
INSTALL_DIR="/opt/smsly-hosting"
INSTALL_LOCK_FILE="/tmp/smsly-install.lock"
LOG_PREFIX="[update-watcher]"

echo "$LOG_PREFIX Starting — watching $WATCH_DIR for updates"

# Ensure watch directory exists
mkdir -p "$WATCH_DIR"

install_lock_active() {
    [ -f "$INSTALL_LOCK_FILE" ] || return 1

    if command -v flock ; then
        if (flock -n 9) 9<"$INSTALL_LOCK_FILE"; then
            return 1
        fi
        return 0
    fi

    local pid
    pid="$(cat "$INSTALL_LOCK_FILE"  || true)"
    [ -n "$pid" ] && kill -0 "$pid" 
}

write_status() {
    local state="$1"
    local request_id="$2"
    local mode="$3"
    local exit_code="${4:-}"
    local message="${5:-}"

    {
        echo "STATE=$state"
        echo "REQUEST_ID=$request_id"
        echo "MODE=$mode"
        echo "EXIT_CODE=$exit_code"
        echo "UPDATED_AT=$(date -Is)"
        echo "MESSAGE=$message"
    } > "$STATUS_FILE"
}

while true; do
    # Check for update flag
    if [ -f "$UPDATE_FLAG" ]; then
        if install_lock_active; then
            echo "$LOG_PREFIX Installer lock is active; deferring platform update"
            sleep 10
            continue
        fi

        PAYLOAD=$(tr -d ' \n\r' < "$UPDATE_FLAG"  || echo "update")
        [ -z "$PAYLOAD" ] && PAYLOAD="update"
        MODE="${PAYLOAD%%:*}"
        REQUEST_ID=""
        if [ "$PAYLOAD" != "$MODE" ]; then
            REQUEST_ID="${PAYLOAD#*:}"
        fi
        [ -z "$MODE" ] && MODE="update"
        [ -z "$REQUEST_ID" ] && REQUEST_ID="manual-$(date +%s)"
        
        echo "$LOG_PREFIX Update flag detected (mode: $MODE, request: $REQUEST_ID) — initiating platform update"
        
        # Remove flag first to prevent loop
        rm -f "$UPDATE_FLAG"
        
        if [ -f "$INSTALL_DIR/install.sh" ]; then
            cd "$INSTALL_DIR"
            
            # Map mode to install.sh flags
            FLAGS="--update"
            if [ "$MODE" = "frontend" ]; then
                FLAGS="--update-frontend"
            elif [ "$MODE" = "backend" ]; then
                FLAGS="--update-backend"
            fi

            write_status "running" "$REQUEST_ID" "$MODE" "" "Running bash install.sh $FLAGS --non-interactive on the host."
            echo "$LOG_PREFIX Executing in screen: sudo bash install.sh $FLAGS --non-interactive"
            # Kill any stale install screens before starting a new one
            sudo screen -ls | grep "Grid-install" | cut -d. -f1 | awk '{print $1}' | xargs sudo kill -9  || true
            sudo screen -wipe  || true

            rm -f "$WATCH_DIR/install.log"  || true
            touch "$WATCH_DIR/install.log"  || true
            chmod 666 "$WATCH_DIR/install.log"  || true

            set +e
            if command -v screen ; then
                # -D -m keeps this watcher process attached to the command lifetime while
                # still providing a named screen session for host-side inspection.
                sudo screen -S Grid-install -D -m bash -c "export PATH=\"/usr/local/bin:\$PATH\"; bash install.sh $FLAGS --non-interactive 2>&1 | tee -a /var/log/smsly-install.log \"$WATCH_DIR/install.log\""
                exit_code=$?
            else
                sudo env PATH="/usr/local/bin:$PATH" bash install.sh $FLAGS --non-interactive 2>&1 | tee -a /var/log/smsly-install.log "$WATCH_DIR/install.log"
                exit_code=$?
            fi
            set -e

            if [ "$exit_code" -eq 0 ]; then
                echo "$LOG_PREFIX Update process completed successfully."
                write_status "success" "$REQUEST_ID" "$MODE" "$exit_code" "Host update completed successfully."
            else
                echo "$LOG_PREFIX ERROR: update exited with code $exit_code"
                write_status "failed" "$REQUEST_ID" "$MODE" "$exit_code" "Host update failed with exit code $exit_code. Check /var/log/smsly-install.log."
            fi
        else
            echo "$LOG_PREFIX ERROR: install.sh not found in $INSTALL_DIR"
            write_status "failed" "$REQUEST_ID" "$MODE" "127" "install.sh not found in $INSTALL_DIR."
        fi
    fi

    sleep 10
done
