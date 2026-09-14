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
    # Keep the frontend's baked canonical origin in sync: the middleware
    # hostname check reads NEXT_PUBLIC_APP_URL from the image build, so a
    # domain change without a rebuild would 404 the real dashboard.
    # fix-domain forces USE_SSL=true above, hence https here.
    _prev_frontend_url="$(grep -m1 '^FRONTEND_APP_URL=' "$env_file" 2>/dev/null | cut -d= -f2- || true)"
    if grep -q '^FRONTEND_APP_URL=' "$env_file" ; then
        sed -i "s|^FRONTEND_APP_URL=.*|FRONTEND_APP_URL=https://$target_domain|" "$env_file"
    else
        echo "FRONTEND_APP_URL=https://$target_domain" >> "$env_file"
    fi
    if [ "${_prev_frontend_url:-}" != "https://$target_domain" ]; then
        _FRONTEND_URL_CHANGED="true"
    else
        _FRONTEND_URL_CHANGED="false"
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

    # 3. Regenerative sync (preferred): rebuild the FULL Caddyfile from
    # current DB state so service blocks, wildcard/custom redirects,
    # path redirects and TLS survive the domain change. The static stub
    # below is emergency fallback only (backend down): it deliberately
    # drops everything except the platform domain, so it must never be
    # the final state when the backend is available.
    ensure_selfsigned_cert
    local fix_ip
    fix_ip="$(detect_public_ip)"
    local _caddy_synced="false"
    if docker compose -f "$COMPOSE_FILE" ps -q backend  | grep -q .; then
        local _sync_out
        _sync_out="$(timeout -k 5 300 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell <<'PYEOF' 2>&1 || true
from apps.deployments.models import PlatformConfig
from apps.deployments.services.caddy_manager import apply_caddyfile, generate_caddyfile
config = PlatformConfig.load()
content = generate_caddyfile(config)
cf_token = (getattr(config, 'cloudflare_api_token', '') or '').strip()
result = apply_caddyfile(content, cloudflare_token=cf_token)
print('CADDY_SYNC_OK' if result.get('ok') else 'CADDY_SYNC_FAIL: ' + str(result.get('message', ''))[:200])
PYEOF
)"
        if echo "$_sync_out" | grep -q CADDY_SYNC_OK; then
            echo -e "${GREEN}  ✓ Caddyfile regenerated from live state${NC}"
            _caddy_synced="true"
        else
            echo -e "${YELLOW}    ⚠ Regenerative sync failed — falling back to minimal stub${NC}"
            echo "$_sync_out" | tail -n 5 || true
        fi
    else
        echo -e "${YELLOW}  ⚠ Backend not running; DB sync deferred, using minimal stub Caddyfile${NC}"
    fi
    if [ "$_caddy_synced" != "true" ] && [ -d "caddy-config" ]; then
        cat > caddy-config/Caddyfile <<CADDYFIX
# SMSLY Caddyfile — EMERGENCY fallback written by --fix-domain (backend was
# unavailable for a regenerative sync). Re-run install.sh --fix-domain or
# trigger a routing sync once the backend is up; this stub routes ONLY the
# platform domain and drops all service/custom routing until then.
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
        echo -e "${YELLOW}  ⚠ Emergency stub Caddyfile written (service routing dropped until re-sync)${NC}"
    fi

    # 4. Reload Caddy
    if docker compose -f "$COMPOSE_FILE" ps -q caddy  | grep -q .; then
        timeout -k 5 20 docker compose -f "$COMPOSE_FILE" exec caddy caddy reload --config /etc/caddy/Caddyfile || \
            timeout -k 5 20 docker compose -f "$COMPOSE_FILE" restart caddy || \
            echo -e "${YELLOW}    ⚠ Caddy reload failed${NC}"
    fi

    # 5. Rebuild frontend when the canonical origin changed so the baked
    # NEXT_PUBLIC_APP_URL (middleware hostname check) cannot go stale.
    # Skipped when unchanged — the rebuild takes minutes.
    if [ "${_FRONTEND_URL_CHANGED:-false}" = "true" ]; then
        echo -e "${BLUE}  → Domain changed: rebuilding frontend to re-bake canonical origin...${NC}"
        timeout -k 5 900 docker compose -f "$COMPOSE_FILE" up -d --build --no-deps frontend && \
            echo -e "${GREEN}  ✓ Frontend rebuilt with new canonical origin${NC}" || \
            echo -e "${YELLOW}    ⚠ Frontend rebuild failed — dashboard hostname check may use the old domain until next update${NC}"
    fi

    echo -e "${GREEN}  ✓ Domain fix complete for: $target_domain${NC}"
}
