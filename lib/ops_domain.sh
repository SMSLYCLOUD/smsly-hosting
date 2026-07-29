fix_domain_sync() {
    local target_domain="${1:-}"
    local env_file="$INSTALL_DIR/.env"

    echo -e "${BLUE}  → Fixing domain sync for: $target_domain${NC}"

    # 1. Fix .env
    if grep -q '^DOMAIN=' "$env_file" ; then
        sed -i "s|^DOMAIN=.*|DOMAIN=$target_domain|" "$env_file"
    else
        echo "DOMAIN=$target_domain" >> "$env_file"
    fi
    if grep -q '^USE_SSL=' "$env_file" ; then
        sed -i 's/^USE_SSL=.*/USE_SSL=true/' "$env_file"
    else
        echo "USE_SSL=true" >> "$env_file"
    fi

    # Sync allowlists
    sync_env_domain_allowlists "$env_file" "$target_domain" "$(detect_public_ip)"

    # 2. Sync DB PlatformConfig
    if docker compose -f "$COMPOSE_FILE" ps -q backend  | grep -q .; then
        timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
cfg = PlatformConfig.load()
cfg.domain = '$target_domain'
cfg.use_ssl = True
cfg.save()
print(f'PlatformConfig domain set to: {cfg.domain}')
"  && echo -e "${GREEN}  ✓ PlatformConfig synced${NC}" || echo -e "${YELLOW}  ⚠ DB sync skipped${NC}"
    else
        echo -e "${YELLOW}  ⚠ Backend not running; DB sync deferred to --update${NC}"
    fi

    # 3. Generate self-signed cert + regenerate Caddyfile
    ensure_selfsigned_cert
    local fix_ip
    fix_ip="$(detect_public_ip)"
    if [ -d "caddy-config" ]; then
        cat > caddy-config/Caddyfile <<CADDYFIX
# SMSLY Caddyfile — Fixed by --fix-domain
{
    on_demand_tls {
        ask http://backend:8000/api/v1/services/check-domain/
    }
}

$target_domain {
    reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

${fix_ip} {
    tls internal
    redir http://${fix_ip}{uri} 308
}

:80 {
    @acme {
        path /.well-known/acme-challenge/*
    }
    handle @acme {
        reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    }
    @redirectable {
        not header_regexp host ^([0-9]{1,3}[.]){3}[0-9]{1,3}(:[0-9]+)?$
        not host localhost
        not host 127.0.0.1
        not host *.local
        header_regexp host .+
    }
    redir @redirectable https://{host}{uri} 308
    handle {
        reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    }
}
CADDYFIX
        echo -e "${GREEN}  ✓ Caddyfile regenerated${NC}"
    fi

    # 4. Reload Caddy
    if docker compose -f "$COMPOSE_FILE" ps -q caddy  | grep -q .; then
        timeout -k 5 20 docker compose -f "$COMPOSE_FILE" exec caddy caddy reload --config /etc/caddy/Caddyfile || \
            timeout -k 5 20 docker compose -f "$COMPOSE_FILE" restart caddy || \
            echo -e "${YELLOW}    ⚠ Caddy reload failed${NC}"
    fi

    echo -e "${GREEN}  ✓ Domain fix complete for: $target_domain${NC}"
}
