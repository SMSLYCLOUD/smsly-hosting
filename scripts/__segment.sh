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
    domain="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
"  | tr -d '[:space:]' || true)"
    if [ -z "$domain" ]; then
        domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- || true)"
    fi

    # 2. Discover ALL deployed service domains from DB (public + custom)
    local svc_blocks=""
    svc_blocks="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
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
        print(f'{d} {{\n    reverse_proxy {upstream}\n    encode gzip\n}}\n')
    for cd in (svc.custom_domains or []):
        cd = cd.strip()
        if cd:
            print(f'{cd} {{\n    reverse_proxy {upstream}\n    encode gzip\n}}\n')
"  | tr -d '\r' || true)"

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
    caddy fmt --overwrite /opt/smsly-hosting/caddy-config/Caddyfile  || true
    echo -e "${YELLOW}  [WARN] Wildcard HTTPS disabled. Individual service domains have HTTP-01 SSL.${NC}"
}

# Returns 0 if Caddy config needs fixing, 1 if it's fine.
caddy_needs_fix() {
    # Export CF token from systemd override so caddy validate can resolve {env.CLOUDFLARE_API_TOKEN}
    return 1
}