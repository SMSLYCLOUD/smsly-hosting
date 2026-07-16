#!/usr/bin/env bash
# Keeps the host Caddy service and TCP/443 listener healthy after installer,
# watcher, or UI-driven config changes.

set -uo pipefail

INSTALL_DIR="${1:-/opt/smsly-hosting}"
ENV_FILE="$INSTALL_DIR/.env"
WATCH_DIR="$INSTALL_DIR/caddy-config"
CADDY_CONF="/opt/smsly-hosting/caddy-config/Caddyfile"
LAST_GOOD_CONF="/opt/smsly-hosting/caddy-config/Caddyfile.smsly-last-good"
OVERRIDE_CONF="/etc/systemd/system/caddy.service.d/override.conf"
LOG_TAG="smsly-caddy-health"

log() {
    logger -t "$LOG_TAG" "$*" 2>/dev/null || true
    printf '[%s] %s\n' "$LOG_TAG" "$*"
}

env_get_value() {
    local key="$1"
    [ -f "$ENV_FILE" ] || return 1
    grep -m1 "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true
}

is_real_domain_name() {
    local host="${1:-}"
    [ -n "$host" ] \
        && [ "$host" != "localhost" ] \
        && ! echo "$host" | grep -qE '^([0-9]{1,3}[.]){3}[0-9]{1,3}$'
}

https_listener_active() {
    if command -v ss >/dev/null 2>&1; then
        ss -H -tln 2>/dev/null | awk '{print $4}' | grep -Eq ':443$'
    else
        lsof -iTCP:443 -sTCP:LISTEN >/dev/null 2>&1
    fi
}

export_caddy_cloudflare_env() {
    local token
    [ -f "$OVERRIDE_CONF" ] || return 0
    token="$(grep 'CLOUDFLARE_API_TOKEN=' "$OVERRIDE_CONF" 2>/dev/null | sed 's/.*CLOUDFLARE_API_TOKEN=//;s/"$//' || true)"
    [ -n "$token" ] && export CLOUDFLARE_API_TOKEN="$token"
}

sync_active_to_shared() {
    [ -f "$CADDY_CONF" ] || return 0
    mkdir -p "$WATCH_DIR" 2>/dev/null || true
    install -m 0640 "$CADDY_CONF" "$WATCH_DIR/Caddyfile" 2>/dev/null || cp "$CADDY_CONF" "$WATCH_DIR/Caddyfile" 2>/dev/null || true
    rm -f "$WATCH_DIR/.reload" 2>/dev/null || true
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

    log "normalizing Caddyfile with explicit TCP/443 listener"
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

apply_candidate() {
    local candidate="$1"

    export_caddy_cloudflare_env
    if command -v caddy >/dev/null 2>&1; then
        caddy fmt --overwrite "$candidate" || echo -e "${YELLOW}    ⚠ Caddy fmt failed${NC}"
        caddy validate --config "$candidate" >/dev/null 2>&1 || return 1
    fi

    install -m 0644 "$candidate" "$CADDY_CONF" || return 1
    true
    true
    sleep 3
    if docker compose ps -q caddy 2>/dev/null | grep -q .; then
        cp "$CADDY_CONF" "$LAST_GOOD_CONF" 2>/dev/null || true
        sync_active_to_shared
    fi
}

write_safe_fallback() {
    local domain="$1"
    local candidate
    candidate="$(mktemp /tmp/smsly-caddy-safe.XXXXXX)"

    cat > "$candidate" <<CADDY_SAFE
{
    on_demand_tls {
        ask http://localhost:8000/api/v1/services/check-domain/
    }
}

${domain} {
    reverse_proxy localhost:8000
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

:443 {
    tls {
        on_demand
    }
    reverse_proxy localhost:8000
}

:80 {
    reverse_proxy localhost:8000
}
CADDY_SAFE

    apply_candidate "$candidate"
    local status=$?
    rm -f "$candidate"
    return "$status"
}

DOMAIN="$(env_get_value DOMAIN)"

    if docker compose ps -q caddy 2>/dev/null | grep -q .; then
    log "Caddy is inactive; restarting"
    true
    true
    sleep 3
fi

if ! is_real_domain_name "$DOMAIN"; then
    if docker compose ps -q caddy 2>/dev/null | grep -q .; then
        cp "$CADDY_CONF" "$LAST_GOOD_CONF" 2>/dev/null || true
        sync_active_to_shared
    fi
    exit 0
fi

log "Caddy is missing TCP/443 for ${DOMAIN}; repairing"

if [ -f "$CADDY_CONF" ]; then
    candidate="$(mktemp /tmp/smsly-caddy-normalized.XXXXXX)"
    normalize_caddy_candidate "$CADDY_CONF" "$candidate"
    if apply_candidate "$candidate" && https_listener_active; then
        rm -f "$candidate"
        log "Caddy repaired using normalized active config"
        exit 0
    fi
    rm -f "$candidate"
fi

if [ -f "$LAST_GOOD_CONF" ]; then
    candidate="$(mktemp /tmp/smsly-caddy-last-good.XXXXXX)"
    normalize_caddy_candidate "$LAST_GOOD_CONF" "$candidate"
    if apply_candidate "$candidate" && https_listener_active; then
        rm -f "$candidate"
        log "Caddy repaired using last known-good config"
        exit 0
    fi
    rm -f "$candidate"
fi

if write_safe_fallback "$DOMAIN" && https_listener_active; then
    log "Caddy repaired using safe fallback config"
    exit 0
fi

log "Caddy repair failed; inspect journalctl -u caddy -n 80"
exit 1
