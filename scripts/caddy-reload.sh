#!/bin/bash
# =============================================================================
# Caddy Config Watcher
# Watches for Caddyfile changes from the backend container and reloads Caddy.
# Run as a systemd service: caddy-watcher.service
# =============================================================================

set -euo pipefail

WATCH_DIR="${1:-/opt/smsly-hosting/caddy-config}"
CADDY_CONF="/opt/smsly-hosting/caddy-config/Caddyfile"
LAST_GOOD_CONF="/opt/smsly-hosting/caddy-config/Caddyfile.smsly-last-good"
RELOAD_FLAG="$WATCH_DIR/.reload"
TOKEN_FILE="$WATCH_DIR/.cloudflare_token"
TOKEN_CLEAR_FILE="$WATCH_DIR/.cloudflare_token_clear"
OVERRIDE_DIR="/etc/systemd/system/caddy.service.d"
OVERRIDE_CONF="$OVERRIDE_DIR/override.conf"
LOG_PREFIX="[caddy-watcher]"

echo "$LOG_PREFIX Starting — watching $WATCH_DIR for changes"

# Load Cloudflare token if available (needed for validation of wildcard configs)
load_cloudflare_env() {
    if [ ! -f "$OVERRIDE_CONF" ]; then
        return
    fi

    local token_line token
    token_line="$(grep '^Environment="CLOUDFLARE_API_TOKEN=' "$OVERRIDE_CONF" 2>/dev/null || true)"
    token="${token_line#Environment=\"CLOUDFLARE_API_TOKEN=}"
    token="${token%\"}"

    if [ -n "$token" ]; then
        export CLOUDFLARE_API_TOKEN="$token"
        echo "$LOG_PREFIX Loaded Cloudflare env from Caddy service override"
    else
        unset CLOUDFLARE_API_TOKEN || true
    fi
}

clear_cloudflare_override() {
    local had_token=0
    if [ -f "$OVERRIDE_CONF" ] && grep -q 'CLOUDFLARE_API_TOKEN=' "$OVERRIDE_CONF"; then
        had_token=1
    fi

    if [ -f "$OVERRIDE_CONF" ]; then
        rm -f "$OVERRIDE_CONF"
    fi
    unset CLOUDFLARE_API_TOKEN || true

    if [ "$had_token" -eq 1 ]; then
        systemctl daemon-reload 2>&1 || true
        echo "$LOG_PREFIX Cleared Cloudflare token override"
    fi
}

# Sync Cloudflare token from shared volume to systemd override.
# This bridges the gap between the web UI (writes token to DB → shared volume)
# and Caddy (reads token from systemd environment).
sync_cloudflare_token() {
    if [ -f "$TOKEN_CLEAR_FILE" ]; then
        clear_cloudflare_override
        rm -f "$TOKEN_CLEAR_FILE" "$TOKEN_FILE"
    fi

    if [ -f "$TOKEN_FILE" ]; then
        NEW_TOKEN=$(cat "$TOKEN_FILE")
        if [ -n "$NEW_TOKEN" ] && echo "$NEW_TOKEN" | grep -qE '^[A-Za-z0-9._-]+$'; then
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
            rm -f "$CANDIDATE_CADDY"
        elif [ -z "$NEW_TOKEN" ]; then
            clear_cloudflare_override
        else
            echo "$LOG_PREFIX ERROR: Cloudflare token contains invalid characters; skipping sync"
        fi
        # Remove token file after processing (security: don't leave on disk)
        rm -f "$TOKEN_FILE"
    fi
}

load_cloudflare_env

# Ensure watch directory exists
mkdir -p "$WATCH_DIR"

https_listener_active() {
    if command -v ss >/dev/null 2>&1; then
        ss -H -tln 2>/dev/null | awk '{print $4}' | grep -Eq ':443$'
    else
        lsof -iTCP:443 -sTCP:LISTEN >/dev/null 2>&1
    fi
}

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

candidate_has_explicit_443() {
    grep -Eq '^[[:space:]]*:443[[:space:]]*\{' "$1"
}

normalize_caddy_candidate() {
    local source="$1"
    local target="$2"

    cp "$source" "$target"
    if ! candidate_requires_https "$source" || candidate_has_explicit_443 "$source"; then
        return 0
    fi

    echo "$LOG_PREFIX Adding explicit TCP 443 listener to HTTPS Caddyfile candidate"
    if grep -q 'on_demand_tls' "$source"; then
        cp "$source" "$target"
    else
        {
            cat <<'CADDY_GLOBAL'
{
    on_demand_tls {
        ask http://localhost:8000/api/v1/services/check-domain/
    }
}

CADDY_GLOBAL
            cat "$source"
        } > "$target"
    fi

    cat >> "$target" <<'CADDY_HTTPS_FALLBACK'

:443 {
    tls {
        on_demand
    }
    reverse_proxy localhost:8000
}
CADDY_HTTPS_FALLBACK
}

restore_previous_caddyfile() {
    local previous="$1"

    if [ -f "$previous" ]; then
        cp "$previous" "$CADDY_CONF"
    elif [ -f "$LAST_GOOD_CONF" ]; then
        cp "$LAST_GOOD_CONF" "$CADDY_CONF"
    fi
    true
}

apply_validated_caddyfile() {
    local candidate="$1"
    local previous="${CADDY_CONF}.prev.$$"

    [ -f "$CADDY_CONF" ] && cp "$CADDY_CONF" "$previous" 2>/dev/null || true
    cp "$candidate" "$CADDY_CONF"

    sleep 2
    if candidate_requires_https "$candidate" && ! https_listener_active; then
        echo "$LOG_PREFIX ERROR: Caddy accepted the candidate but TCP 443 is not listening; restoring previous config"
        restore_previous_caddyfile "$previous"
        rm -f "$previous"
        return 1
    fi
    cp "$CADDY_CONF" "$LAST_GOOD_CONF" 2>/dev/null || true
    rm -f "$previous"
    return 0
}

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
                CANDIDATE_CADDY="$(mktemp /tmp/smsly-caddy-candidate.XXXXXX)"
                normalize_caddy_candidate "$WATCH_CADDY" "$CANDIDATE_CADDY"
                # Validate before applying with simple retry/backoff to avoid transient DNS/ACME race
                attempts=0
                delay=2
                while [ $attempts -lt 4 ]; do
                    if caddy validate --config "$CANDIDATE_CADDY" 2>&1; then
                        echo "$LOG_PREFIX Validation passed — applying"
                        if ! apply_validated_caddyfile "$CANDIDATE_CADDY"; then
                            break
                        fi
                        echo "$LOG_PREFIX Caddy reloaded successfully"
                        # Post-reload smoke (non-blocking)
                        host_line=$(grep -m1 -E '^[^#].*{?$' "$CANDIDATE_CADDY" | head -n1 | awk '{print $1}')
                        wildcard_line=$(grep -m1 -E '^\\*\\.' "$CANDIDATE_CADDY" | head -n1 | awk '{print $1}')
                        if command -v bash >/dev/null 2>&1 && [ -x "/opt/smsly-hosting/scripts/smoke_routes.sh" ]; then
                            /opt/smsly-hosting/scripts/smoke_routes.sh "$host_line" "$wildcard_line" >/var/log/caddy/smoke.log 2>&1 || true
                        fi
                        break
                    fi
                    attempts=$((attempts+1))
                    echo "$LOG_PREFIX Validation failed (attempt $attempts), retrying in ${delay}s"
                    sleep $delay
                delay=$((delay*2))
            done
            if [ $attempts -ge 4 ]; then
                echo "$LOG_PREFIX ERROR: Caddyfile validation failed after retries — NOT applying"
            fi
        else
            echo "$LOG_PREFIX WARNING: No Caddyfile found in $WATCH_DIR"
        fi

        # Remove flag regardless
        rm -f "$RELOAD_FLAG"
    fi

    sleep 2
done
