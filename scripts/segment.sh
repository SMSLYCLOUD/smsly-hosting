ensure_container_on_network() {
    local container_name="$1"
    local network_name="$2"
    [ -z "$container_name" ] && return 0

    docker container inspect "$container_name" >/dev/null 2>&1 || return 0
    docker network inspect "$network_name" >/dev/null 2>&1 || return 0
    docker network connect "$network_name" "$container_name" >/dev/null 2>&1 || true
}

# --- Shared Caddy Safety Function --------------------------------------------
# Called from: recover_runtime_stack, update flow, restart_edge_stack.
# Generates a safe fallback Caddyfile when the current one is broken or risky.
# - Discovers domain from DB first, falls back to .env
# - Skips HTTPS blocks for IP addresses (certs can't be issued)
# - Adds individual Caddy blocks for each deployed service (HTTP-01 SSL)
# - Detects dns cloudflare + missing systemd override (validates passes, runtime crashes)
generate_safe_caddyfile() {
    local reason="${1:-unknown}"
    echo -e "${YELLOW}  Generating safe fallback Caddyfile...${NC}"

    # 1. Discover domain: DB first, .env fallback
    local domain=""
    domain=$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null <<'PY'
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
PY
    )
    domain="$(echo "$domain" | tr -d '[:space:]')"
    if [ -z "$domain" ]; then
        domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    fi

    # 2. Discover ALL deployed service domains from DB (public + custom)
    local svc_blocks=""
    svc_blocks=$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null <<'PY'
import os
upstream = os.environ.get('SMSLY_SERVICE_PROXY_UPSTREAM', 'traefik:80')
from apps.deployments.models import Service
allowlisted_services = $SERVICE_ALLOWLIST_PY
queryset = Service.objects.all()
if allowlisted_services:
    queryset = queryset.filter(name__in=allowlisted_services)
for svc in queryset.exclude(public_domain__isnull=True).exclude(public_domain=''):
    d = svc.public_domain.strip()
    if d:
        print(f'{d} {{\\n    reverse_proxy {upstream}\\n    encode gzip\\n}}\\n')
    for cd in (svc.custom_domains or []):
        cd = cd.strip()
        if cd:
            print(f'{cd} {{\\n    reverse_proxy {upstream}\\n    encode gzip\\n}}\\n')
PY
    )

    # 3. Check if domain is a real hostname (not an IP address)
    local is_real_domain=false
    if [ -n "$domain" ] && [ "$domain" != "localhost" ]; then
        if ! echo "$domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
            is_real_domain=true
        fi
    fi

    # 4. Build the Caddyfile
    if [ "$is_real_domain" = "true" ]; then
        cat > /opt/smsly-hosting/caddy-config/Caddyfile <<SAFECADDY
# Auto-generated safe fallback (reason: $reason)
# Individual service domains get SSL via Let's Encrypt HTTP-01 challenge.
# Set CLOUDFLARE_API_TOKEN in .env and run --update to re-enable wildcard SSL.
${domain} {
    reverse_proxy localhost:8000
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

:80 {
    handle {
        rewrite * /notice
        reverse_proxy localhost:8000
    }
}

${svc_blocks}
SAFECADDY
    else
        cat > /opt/smsly-hosting/caddy-config/Caddyfile <<SAFECADDY
# Auto-generated safe fallback (reason: $reason)
:80 {
    reverse_proxy localhost:8000
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

${svc_blocks}
SAFECADDY
    fi
    caddy fmt --overwrite /opt/smsly-hosting/caddy-config/Caddyfile 2>/dev/null || true
    echo -e "${YELLOW}  [WARN] Wildcard HTTPS disabled. Individual service domains have HTTP-01 SSL.${NC}"
}

# Returns 0 if Caddy config needs fixing, 1 if it's fine.
caddy_needs_fix() {
    # Export CF token from systemd override so caddy validate can resolve {env.CLOUDFLARE_API_TOKEN}
    if [ -f /etc/systemd/system/caddy.service.d/override.conf ]; then
        local cf_val
        cf_val=$(grep 'CLOUDFLARE_API_TOKEN=' /etc/systemd/system/caddy.service.d/override.conf 2>/dev/null | sed 's/.*CLOUDFLARE_API_TOKEN=//;s/\\x22//g' || true)
        if [ -n "$cf_val" ]; then
            export CLOUDFLARE_API_TOKEN="$cf_val"
        fi
    fi

    if ! caddy validate --config /opt/smsly-hosting/caddy-config/Caddyfile 2>/dev/null; then
        return 0  # Syntax error
    fi
    if grep -q 'dns cloudflare' /opt/smsly-hosting/caddy-config/Caddyfile 2>/dev/null \
       && [ ! -f /etc/systemd/system/caddy.service.d/override.conf ]; then
        return 0  # dns cloudflare without token override = runtime crash
    fi
    return 1  # Config is fine
}

bust_core_build_cache() {
    echo -e "${BLUE}  Busting frontend/backend build cache...${NC}"

    # Remove old app image layers for deterministic rebuilds (no DB/data touched).
    for svc in frontend backend celery celery-beat; do
        local image_ids=""
        image_ids="$(docker compose -f "$COMPOSE_FILE" images -q "$svc" 2>/dev/null | awk 'NF' | sort -u || true)"
        if [ -n "$image_ids" ]; then
            while read -r image_id; do
                [ -n "$image_id" ] && docker rmi -f "$image_id" >/dev/null 2>&1 || true
            done <<< "$image_ids"
        fi
    done

    # Build cache only (no global container/image prune).
    docker builder prune -af >/dev/null 2>&1 || true
    
    # NEW: Prune old unused images older than 7 days to prevent disk space exhaustion.
    # echo -e "${BLUE}  -> Pruning deeply stale images (>7 days old)...${NC}"
    # docker image prune -a -f --filter "until=168h" >/dev/null 2>&1 || true
    
    echo -e "${GREEN}  OK Cache bust complete (targeted images + build cache + deep prune)${NC}"
}

restart_edge_stack() {
    local edge_services="socket-proxy traefik route-fallback caddy"

    echo -e "${BLUE}  -> Refreshing edge proxy stack (caddy/traefik/socket-proxy/route-fallback)...${NC}"
    echo -e "${BLUE}    [1/5] Bringing up edge services...${NC}"
    local all_running=true
    for svc in $edge_services; do
        if ! docker compose -f "$COMPOSE_FILE" ps "$svc" 2>/dev/null | grep -q "Up"; then
            all_running=false
            break
        fi
    done
    if [ "$all_running" = true ]; then
        echo -e "${GREEN}      edge services already running, skipping restart${NC}"
    else
        # NOTE(Zero-Downtime): Removed --force-recreate to eliminate downtime for deployed services.
        timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d --no-deps $edge_services >/dev/null 2>&1 || \
            timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d $edge_services >/dev/null 2>&1 || true
    fi

    # Restart core app entrypoints so new upstream bindings are live.
    echo -e "${BLUE}    [2/5] Restarting frontend + backend...${NC}"
    timeout -k 5 30 docker compose -f "$COMPOSE_FILE" restart frontend backend >/dev/null 2>&1 || true
    echo -e "${BLUE}    [3/5] Restarting edge services...${NC}"
    timeout -k 5 30 docker compose -f "$COMPOSE_FILE" restart $edge_services >/dev/null 2>&1 || true

    # Re-attach expected external networks (idempotent).
    echo -e "${BLUE}    [4/5] Re-attaching external networks...${NC}"
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-caddy-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    # Validate Caddy config before restart (H1 fix)
    echo -e "${BLUE}    [5/5] Validating Caddy config...${NC}"
    if command -v caddy >/dev/null 2>&1; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "restart_edge_stack validation"
        fi
    true
    true
    fi
    echo -e "${GREEN}  OK Edge stack refreshed${NC}"
}

wait_for_container_ready() {
    local container_name="$1"
    local timeout_seconds="${2:-180}"
    local elapsed=0
    local state=""
}

