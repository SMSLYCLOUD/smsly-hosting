wipe_existing_install() {
    echo -e "${YELLOW}[WIPE] Removing existing SMSLY Hosting installation artifacts...${NC}"

    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --wipe)${NC}"
        exit 1
    fi

    if [ "${FORCE_WIPE:-0}" != "1" ]; then
        if [ -e /dev/tty ]; then
            echo -e "${RED}  WARNING: This permanently deletes containers, volumes, networks, and $INSTALL_DIR${NC}"
            read -r -p "  Type WIPE to continue: " WIPE_CONFIRM < /dev/tty
            if [ "$WIPE_CONFIRM" != "WIPE" ]; then
                echo -e "${YELLOW}  Wipe cancelled by user.${NC}"
                exit 1
            fi
        else
            echo -e "${RED}x Non-interactive wipe requires FORCE_WIPE=1${NC}"
            exit 1
        fi
    fi

    if [ -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
        cd "$INSTALL_DIR"
        docker compose -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true
    fi

    SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly-hosting" -q 2>/dev/null || true)
    if [ -n "$SMSLY_CONTAINERS" ]; then
        docker rm -f $SMSLY_CONTAINERS 2>/dev/null || true
    fi

    SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly-hosting" -q 2>/dev/null || true)
    if [ -n "$SMSLY_VOLUMES" ]; then
        for vol in $SMSLY_VOLUMES; do
            docker volume rm "$vol" 2>/dev/null || true
        done
    fi

    SMSLY_NETWORKS=$(docker network ls --filter "name=smsly-hosting" -q 2>/dev/null || true)
    if [ -n "$SMSLY_NETWORKS" ]; then
        for net in $SMSLY_NETWORKS; do
            docker network rm "$net" 2>/dev/null || true
        done
    fi

    # Clean up Caddy watcher service (prevents stale config on reinstall)
    systemctl stop caddy-watcher 2>/dev/null || true
    systemctl disable caddy-watcher 2>/dev/null || true
    rm -f /etc/systemd/system/caddy-watcher.service

    # Reset Caddyfile to default (prevents stale routing)
    if [ -f "$INSTALL_DIR"/caddy-config/Caddyfile ]; then
        echo ':80 { respond "Caddy is running" 200 }' > "$INSTALL_DIR"/caddy-config/Caddyfile
    fi

    # Remove Cloudflare token override
    rm -rf /etc/systemd/system/caddy.service.d
    systemctl daemon-reload 2>/dev/null || true

    rm -rf "$INSTALL_DIR"
    rm -f "$LOG_FILE"

    trap - EXIT
    release_install_lock
    echo -e "${GREEN}OK Wipe complete. The server is ready for a fresh install.${NC}"
    echo -e "${YELLOW}  Run: curl -fsSL https://raw.githubusercontent.com/smsly/smsly-hosting/main/install.sh -o install.sh${NC}"
    echo -e "${YELLOW}       gpg --verify install.sh  # if you have a signed copy${NC}"
    echo -e "${YELLOW}       sudo bash install.sh${NC}"
    exit 0
}
fix_env_permissions() {
    local env_file="${1:-$INSTALL_DIR/.env}"
    echo -e "${BLUE}  → Fixing .env permissions...${NC}"

    if [ ! -f "$env_file" ]; then
        echo -e "${YELLOW}  ⚠ .env not found at $env_file${NC}"
        return 1
    fi

    # The backend container runs as UID 1000 (smsly user).
    # .env must be group-writable by GID 1000 so the domain-config
    # signal can persist DOMAIN/USE_SSL changes back to .env.
    chown root:1000 "$env_file" 2>/dev/null || true
    chmod 664 "$env_file" 2>/dev/null || true

    local owner mode
    owner="$(stat -c '%u:%g' "$env_file" 2>/dev/null || echo "?")"
    mode="$(stat -c '%a' "$env_file" 2>/dev/null || echo "?")"
    echo -e "${GREEN}  ✓ .env permissions: $mode owner=$owner${NC}"

    # Also fix caddy-config directory for good measure
    if [ -d "$INSTALL_DIR/caddy-config" ]; then
        chown -R 1000:1000 "$INSTALL_DIR/caddy-config" 2>/dev/null || true
        chmod -R u+rwX,g+rwX "$INSTALL_DIR/caddy-config" 2>/dev/null || true
        echo -e "${GREEN}  ✓ caddy-config permissions fixed${NC}"
    fi

    # Fix staticfiles/media directories
    for dir in staticfiles media backups; do
        if [ -d "$INSTALL_DIR/$dir" ]; then
            chown -R 1000:1000 "$INSTALL_DIR/$dir" 2>/dev/null || true
        fi
    done

    # Fix builds and prometheus-targets directories
    for dir in builds prometheus-targets; do
        if [ -d "$INSTALL_DIR/$dir" ]; then
            chown -R 1000:1000 "$INSTALL_DIR/$dir" 2>/dev/null || true
            chmod 2777 "$INSTALL_DIR/$dir" 2>/dev/null || true
        fi
    done
}
fix_domain_sync() {
    local target_domain="${1:-}"
    local env_file="$INSTALL_DIR/.env"

    echo -e "${BLUE}  → Fixing domain sync for: $target_domain${NC}"

    # 1. Fix .env
    if grep -q '^DOMAIN=' "$env_file" 2>/dev/null; then
        sed -i "s|^DOMAIN=.*|DOMAIN=$target_domain|" "$env_file"
    else
        echo "DOMAIN=$target_domain" >> "$env_file"
    fi
    if grep -q '^USE_SSL=' "$env_file" 2>/dev/null; then
        sed -i 's/^USE_SSL=.*/USE_SSL=true/' "$env_file"
    else
        echo "USE_SSL=true" >> "$env_file"
    fi

    # Sync allowlists
    sync_env_domain_allowlists "$env_file" "$target_domain" "$(detect_public_ip)"

    # 2. Sync DB PlatformConfig
    if docker compose -f "$COMPOSE_FILE" ps -q backend 2>/dev/null | grep -q .; then
        timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
cfg = PlatformConfig.load()
cfg.domain = '$target_domain'
cfg.use_ssl = True
cfg.save()
print(f'PlatformConfig domain set to: {cfg.domain}')
" 2>/dev/null && echo -e "${GREEN}  ✓ PlatformConfig synced${NC}" || echo -e "${YELLOW}  ⚠ DB sync skipped${NC}"
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
    if docker compose -f "$COMPOSE_FILE" ps -q caddy 2>/dev/null | grep -q .; then
        timeout -k 5 20 docker compose -f "$COMPOSE_FILE" exec caddy caddy reload --config /etc/caddy/Caddyfile || \
            timeout -k 5 20 docker compose -f "$COMPOSE_FILE" restart caddy || \
            echo -e "${YELLOW}    ⚠ Caddy reload failed${NC}"
    fi

    echo -e "${GREEN}  ✓ Domain fix complete for: $target_domain${NC}"
}
recover_runtime_stack() {
    echo -e "${BLUE}  -> Running runtime recovery (network + core services + edge)...${NC}"

    ensure_update_networks
    ensure_infrastructure_permissions

    # Only restart Docker if the daemon was reconfigured (e.g. for registry trust).
    # Unconditional restart during recovery can cascade-fail all running
    # containers — including the proxy (Caddy/Traefik) — causing a total outage.
    if [ -f "/etc/docker/daemon.json" ] && [ -f "/var/run/docker.sock" ]; then
        echo -e "${BLUE}    -> Docker daemon is running; skipping restart to preserve live containers${NC}"
    fi

    echo -e "${BLUE}    -> Starting dependency services...${NC}"

    # Ensure registry TLS cert + htpasswd exist before starting the registry.
    # The registry container will crash-loop without these files. Also
    # regenerate if the existing key/cert don't match — `openssl req`
    # produces a matched pair in one shot, so a mismatch means one
    # file was rotated independently of the other.
    mkdir -p "$INSTALL_DIR/auth" "$INSTALL_DIR/certs"
    _regen_registry_tls() {
        echo -e "${BLUE}      Generating self-signed TLS cert for registry...${NC}"
        _tmp_dir="$(mktemp -d)"
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "${_tmp_dir}/registry.key" \
            -out    "${_tmp_dir}/registry.crt" \
            -subj "/CN=registry" \
            -addext "subjectAltName=DNS:registry,DNS:localhost,IP:127.0.0.1,IP:10.100.0.1" 2>/dev/null
        local _rc=$?
        if [ "$_rc" -ne 0 ]; then
            rm -rf "$_tmp_dir"
            return $_rc
        fi
        mv "${_tmp_dir}/registry.key" "$INSTALL_DIR/certs/registry.key"
        mv "${_tmp_dir}/registry.crt" "$INSTALL_DIR/certs/registry.crt"
        rm -rf "$_tmp_dir"
        chmod 644 "$INSTALL_DIR/certs/registry.crt" "$INSTALL_DIR/certs/registry.key"
    }
    _registry_tls_ok() {
        [ -f "$INSTALL_DIR/certs/registry.key" ] || return 1
        [ -f "$INSTALL_DIR/certs/registry.crt" ] || return 1
        local _cmod _kmod
        _cmod="$(openssl x509 -in "$INSTALL_DIR/certs/registry.crt" -noout -modulus 2>/dev/null | openssl sha256)" || return 1
        _kmod="$(openssl rsa  -in "$INSTALL_DIR/certs/registry.key" -noout -modulus 2>/dev/null | openssl sha256)" || return 1
        [ "$_cmod" = "$_kmod" ]
    }
    if ! _registry_tls_ok; then
        _regen_registry_tls
        if ! _registry_tls_ok; then
            echo -e "${RED}    ✗ Registry TLS cert/key still mismatched after regen attempt${NC}"
            echo -e "${YELLOW}      Manual fix: openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \\${NC}"
            echo -e "${YELLOW}        -keyout $INSTALL_DIR/certs/registry.key \\${NC}"
            echo -e "${YELLOW}        -out    $INSTALL_DIR/certs/registry.crt \\${NC}"
            echo -e "${YELLOW}        -subj '/CN=registry'${NC}"
        else
            echo -e "${BLUE}    Restarting registry container to pick up new TLS certs...${NC}"
            docker restart smsly-hosting-registry-1 || echo -e "${YELLOW}    ⚠ Registry restart failed${NC}"
        fi
    fi
    if [ ! -f "$INSTALL_DIR/auth/htpasswd" ] || [ -z "${REGISTRY_PASSWORD:-}" ] || [ -z "${REGISTRY_USER:-}" ]; then
        REGISTRY_PASS="${REGISTRY_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null || openssl rand -hex 12 2>/dev/null || echo 'auto-generated-change-me')}"
        if command -v htpasswd >/dev/null 2>&1; then
            htpasswd -Bbn "${REGISTRY_USER:-smsly-registry}" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"
        else
            python3 -c "
import bcrypt, sys
pw = sys.argv[1] if len(sys.argv) > 1 else '$REGISTRY_PASS'
print('${REGISTRY_USER:-smsly-registry}:' + bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode())
" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd" 2>/dev/null || true
        fi
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_USER" "${REGISTRY_USER:-smsly-registry}"
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_PASSWORD" "$REGISTRY_PASS"
    fi

    # ─── Self-heal: missing secrets + cosign keypair ───────────────────────
    if [ -f "$INSTALL_DIR/.env" ]; then
        _ensure_secret() {
            local _name="$1" _bytes="$2"
            if ! grep -q "^${_name}=" "$INSTALL_DIR/.env" 2>/dev/null || [ -z "$(grep "^${_name}=" "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2)" ]; then
                local _val="$(python3 -c "import secrets; print(secrets.token_hex($_bytes))" 2>/dev/null || openssl rand -hex "$_bytes" 2>/dev/null || true)"
                if [ -n "$_val" ]; then
                    printf -v "$_name" '%s' "$_val"
                    env_set_value "$INSTALL_DIR/.env" "$_name" "$_val" 2>/dev/null || true
                    echo -e "${BLUE}    → Self-healed $_name${NC}"
                fi
            fi
        }
        _ensure_secret REGISTRY_HTTP_SECRET 32
        _ensure_secret REPLICATION_PASSWORD 32
        _ensure_secret SENTINEL_PASSWORD 32
        _ensure_secret CROWDSEC_BOUNCER_KEY 32
        _ensure_secret COSIGN_PASSWORD 32
    fi
    if command -v cosign >/dev/null 2>&1; then
        mkdir -p "$INSTALL_DIR/cosign-keys"
        if [ ! -f "$INSTALL_DIR/cosign-keys/cosign.key" ] || [ ! -f "$INSTALL_DIR/cosign-keys/cosign.pub" ]; then
            echo -e "${BLUE}    → Cosign keypair missing — generating...${NC}"
            COSIGN_PASSWORD="${COSIGN_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32 2>/dev/null || true)}"
            COSIGN_PASSWORD="$COSIGN_PASSWORD" cosign generate-key-pair 2>/dev/null || true
            if [ -f cosign.key ]; then
                mv cosign.key "$INSTALL_DIR/cosign-keys/cosign.key"
                mv cosign.pub "$INSTALL_DIR/cosign-keys/cosign.pub"
                chmod 600 "$INSTALL_DIR/cosign-keys/cosign.key"
                chmod 644 "$INSTALL_DIR/cosign-keys/cosign.pub"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PASSWORD" "$COSIGN_PASSWORD" 2>/dev/null || true
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PRIVATE_KEY_PATH" "$INSTALL_DIR/cosign-keys/cosign.key" 2>/dev/null || true
                echo -e "${GREEN}      ✓ Cosign keypair created${NC}"
            fi
        fi
    fi

    if [ "$MODE_AGENT_LITE" = "true" ]; then
        docker compose -f "$COMPOSE_FILE" up -d redis rabbitmq socket-proxy || true
        wait_for_container_ready "smsly-redis-primary" 120 || true
        sync_agent_lite_rabbitmq_password
    else
        docker compose -f "$COMPOSE_FILE" up -d $(get_db_service) $(get_pgcat_if_exists) redis rabbitmq socket-proxy registry || true
        wait_for_container_ready "smsly-postgres-primary" 120 || true
        if [ -n "$(get_pgcat_if_exists)" ]; then wait_for_container_ready "smsly-hosting-pgcat-1" 120 || true; fi
        wait_for_container_ready "smsly-hosting-redis-1" 120 || true
    fi

    if should_manage_caddy && docker compose -f "$COMPOSE_FILE" ps caddy 2>/dev/null | grep -q "Up"; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "recover_runtime_stack"
        fi
    fi

    echo -e "${BLUE}    -> Refreshing runtime services...${NC}"
    if ! refresh_runtime_services; then
        echo -e "${YELLOW}  WARN Runtime recovery could not fully refresh all runtime services${NC}"
        return 1
    fi

    echo -e "${GREEN}  OK Runtime recovery completed${NC}"
}
debug_platform_status() {
    # TODO(install): replace set -e toggle with explicit conditional. The
    # entire body tolerates command failures (each diagnostic line has its own
    # `|| true` or `2>/dev/null`); leaving set -e toggled off is functional
    # but discouraged.
    set +e
    echo -e "\n${YELLOW}=== SMSLY DEBUG SNAPSHOT ===${NC}"
    echo "Timestamp: $(date -Iseconds)"
    echo "Install dir: $INSTALL_DIR"
    echo ""

    echo "---- Systemd ----"
    systemctl is-active docker 2>/dev/null || true
    true
    true
    systemctl is-active smsly-autoscaler 2>/dev/null || true
    echo ""

    echo "---- Docker Networks ----"
    docker network ls | grep -E 'smsly|socket-proxy' || true
    echo ""

    echo "---- Compose PS ----"
    docker compose -f "$COMPOSE_FILE" ps || true
    echo ""

    echo "---- Local Health ----"
    curl -iSsf http://127.0.0.1:8000/health 2>/dev/null | head -20 || echo "http://127.0.0.1:8000/health failed"
    echo ""

    echo "---- Backend DNS Checks ----"
    docker compose -f "$COMPOSE_FILE" exec -T backend getent hosts db pgcat redis 2>/dev/null || echo "backend DNS check failed"
    echo ""

    echo "---- Key Logs (tail 120) ----"
    docker compose -f "$COMPOSE_FILE" logs --tail=120 backend frontend traefik pgcat redis 2>/dev/null || true
    echo -e "${YELLOW}=== END DEBUG SNAPSHOT ===${NC}\n"
    set -e
}

# =============================================================================
# DEBUG/RECOVER MODES
# =============================================================================
if [ "$DEBUG_MODE" = "true" ]; then
    cd "$INSTALL_DIR" 2>/dev/null || cd /root 2>/dev/null || cd /
    debug_platform_status
    exit 0
fi

if [ "$RECOVER_MODE" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --recover)${NC}"
        exit 1
    fi
    if [ ! -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
        echo -e "${RED}x Missing $INSTALL_DIR/$COMPOSE_FILE. Run fresh install first.${NC}"
        exit 1
    fi
    cd "$INSTALL_DIR"
    ensure_env_runtime_defaults "$INSTALL_DIR/.env" || true
    RECOVER_STATUS=0
    recover_runtime_stack || RECOVER_STATUS=$?
    debug_platform_status
    exit "$RECOVER_STATUS"
fi

if [ "$REFRESH_MODE" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --refresh)${NC}"
        exit 1
    fi
    if [ ! -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
        echo -e "${RED}x Missing $INSTALL_DIR/$COMPOSE_FILE. Run fresh install first.${NC}"
        exit 1
    fi
    cd "$INSTALL_DIR"
    ensure_env_runtime_defaults "$INSTALL_DIR/.env" || true
    REFRESH_STATUS=0
    refresh_runtime_services || REFRESH_STATUS=$?
    debug_platform_status
    exit "$REFRESH_STATUS"
fi

# =============================================================================
# RECREATE-TRAEFIK MODE — One-time safe recreate of the traefik container.
# Preserves letsencrypt volume + acme.json; only forces recreate when needed
# to pick up new entrypoints/flags (e.g. websecure:443, metrics:8082).
# Bypassed by --update to avoid downtime. Caddy is also NOT recreated here.
# =============================================================================
if [ "$RECREATE_TRAEFIK" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --recreate-traefik)${NC}"
        exit 1
    fi
    if [ ! -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
        echo -e "${RED}x Missing $INSTALL_DIR/$COMPOSE_FILE. Run fresh install first.${NC}"
        exit 1
    fi
    cd "$INSTALL_DIR"
    should_manage_caddy || {
        echo -e "${YELLOW}  WARN should_manage_caddy=false; aborting to avoid clobbering.${NC}"
        exit 1
    }
    recreate_traefik_preserving_certs
    exit $?
fi

# =============================================================================
# CLEAR MODE — Remove stale addons and cache
# =============================================================================
if [ "${CLEAR_MODE:-false}" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --clear)${NC}"
        exit 1
    fi
    echo -e "\n${BLUE}  🧹 Running Maintenance Clear...${NC}"

    # Prune unused docker resources
    echo -e "  → Pruning unused Docker containers and images..."
    docker container prune -f >/dev/null 2>&1
    docker image prune -af >/dev/null 2>&1

    # Stop and remove all stale smsly-addon-* containers (only those NOT running)
    echo -e "  → Removing stale/orphaned service addons (protecting active databases)..."
    ADDON_IDS=$(docker ps -a -q --filter "name=smsly-addon" --filter "status=exited" --filter "status=created" --filter "status=dead")
    if [ -n "$ADDON_IDS" ]; then
        docker rm -f $ADDON_IDS >/dev/null 2>&1 || true
        echo -e "${GREEN}  ✓ Removed inactive orphaned addon containers.${NC}"
    else
        echo -e "${YELLOW}  - No inactive orphaned addons found.${NC}"
    fi

    # Stop and remove all stale deployment/blue-green containers
    echo -e "  → Removing stale deployment containers (protecting active routes)..."
    GREEN_IDS=$(docker ps -a -q --filter "name=-green-" --filter "status=exited" --filter "status=created" --filter "status=dead")
    ROUTER_IDS=$(docker ps -a -q --filter "name=ai-router" --filter "status=exited" --filter "status=created" --filter "status=dead")

    if [ -n "$GREEN_IDS" ]; then
        docker rm -f $GREEN_IDS >/dev/null 2>&1 || true
        echo -e "${GREEN}  ✓ Removed inactive deployment containers.${NC}"
    fi
    if [ -n "$ROUTER_IDS" ]; then
        docker rm -f $ROUTER_IDS >/dev/null 2>&1 || true
        echo -e "${GREEN}  ✓ Removed inactive AI routers.${NC}"
    fi

    # Clean caches
    echo -e "  → Cleaning system caches..."
    rm -rf /opt/smsly-cache/* 2>/dev/null || true
    echo -e "${GREEN}  ✓ Cleared /opt/smsly-cache/.${NC}"

    echo -e "\n${GREEN}  ✨ Maintenance complete. You can now re-run deployments.${NC}"
    exit 0
fi

# =============================================================================
# VERIFY MODE — Run endpoint checks only (no changes)
# =============================================================================
if [ "${VERIFY_MODE:-false}" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --verify)${NC}"
        exit 1
    fi
    cd "$INSTALL_DIR" 2>/dev/null || { echo -e "${RED}x $INSTALL_DIR not found. Run fresh install first.${NC}"; exit 1; }

    DOMAIN="$(env_get_value "$INSTALL_DIR/.env" "DOMAIN" 2>/dev/null || echo "")"

    if should_manage_caddy; then
        echo -e "\n${BLUE}  ⟳ Syncing Proxy Configurations...${NC}"
        reload_container_caddy 2>/dev/null || true
        install_caddy_health_guard "$DOMAIN"
    fi


    sleep 3

    echo -e "\n${BLUE}  → Running endpoint verification...${NC}"
    PASS_COUNT=0
    FAIL_COUNT=0

    # Backend health (internal) — docker exec into backend container
    EP1_FALLBACK_URL="http://127.0.0.1:8000/health"
    _LITE_HOST_HEADER=""
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        _ep1_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)"
        if [ -n "$_ep1_domain" ] && [ "$_ep1_domain" != "localhost" ]; then
            _LITE_HOST_HEADER="$_ep1_domain"
        fi
    fi
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        if [ -n "${_LITE_HOST_HEADER:-}" ]; then
            EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 -H "Host: ${_LITE_HOST_HEADER}" "http://127.0.0.1/health" 2>/dev/null) || EP1_CODE="000"
        else
            EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1/health" 2>/dev/null) || EP1_CODE="000"
        fi
    else
        if docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1; then
            EP1_CODE="200"
        elif curl -fsS --max-time 5 "$EP1_FALLBACK_URL" >/dev/null 2>&1; then
            EP1_CODE="200"
        else
            EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP1_FALLBACK_URL" 2>/dev/null) || EP1_CODE="000"
        fi
    fi
    case "$EP1_CODE" in
        2*|3*)
        echo -e "${GREEN}  ✓ Backend (local): HTTP $EP1_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
        ;;
    *)
        echo -e "${RED}  ✗ Backend (local): HTTP $EP1_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
        ;;
    esac

    # Platform domain (public-facing — tests Caddy → Traefik → backend chain)
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ]; then
        EP_PUB_URL="http://${DOMAIN}/health"
        if is_node_mode; then
            EP_PUB_URL="http://${DOMAIN}/health/live"
        fi
        EP_PUB_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 10 "$EP_PUB_URL" 2>/dev/null) || EP_PUB_CODE="000"
        if [ "$EP_PUB_CODE" = "200" ] || [ "$EP_PUB_CODE" = "301" ] || [ "$EP_PUB_CODE" = "308" ]; then
            echo -e "${GREEN}  ✓ Platform (${DOMAIN}): HTTP $EP_PUB_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
        else
            echo -e "${RED}  ✗ Platform (${DOMAIN}): HTTP $EP_PUB_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    fi

    # HTTPS domain (skip for raw IP addresses — certs can't be issued for IPs)
    if ! should_manage_caddy; then
        echo -e "${YELLOW}  ⊘ HTTPS: Skipped (Caddy/HTTPS is master-only in this mode)${NC}"
    elif [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ] && ! echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EP2_URL="https://${DOMAIN}/health"
        EP2_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 10 "$EP2_URL" 2>/dev/null) || EP2_CODE="000"
        case "$EP2_CODE" in
            2*|3*)
            echo -e "${GREEN}  ✓ HTTPS: HTTP $EP2_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
            ;;
        *)
            echo -e "${RED}  ✗ HTTPS: HTTP $EP2_CODE ($EP2_URL)${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
            ;;
        esac
    elif echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' 2>/dev/null; then
        echo -e "${YELLOW}  ⊘ HTTPS: Skipped (IP Mode — SSL requires a domain name)${NC}"
    fi

    # Traefik
    EP3_URL="http://127.0.0.1:8081/"
    if is_node_mode; then
        EP3_URL="http://127.0.0.1/health/live"
    fi
    EP3_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP3_URL" 2>/dev/null) || EP3_CODE="000"
    if [ "$EP3_CODE" != "000" ] && [ "$EP3_CODE" != "502" ]; then
        echo -e "${GREEN}  ✓ Traefik: HTTP $EP3_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Traefik: HTTP $EP3_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # Post-install smoke (HTTP/HTTPS/wildcard) if domain provided
    if [ -n "${DOMAIN:-}" ] && [ -x "/opt/smsly-hosting/scripts/smoke_routes.sh" ]; then
        echo -e "${YELLOW}  ⟳ Smoke-testing routes for ${DOMAIN}${NC}"
        /opt/smsly-hosting/scripts/smoke_routes.sh "$DOMAIN" "*.$DOMAIN" || true
    fi

    # Deployed service domains
    ALL_SVC_DOMAINS="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
for s in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain=''):
    print(f'{s.name}|{s.public_domain.strip()}')
" 2>/dev/null | tr -d '\r' || true)"

    if [ -n "$ALL_SVC_DOMAINS" ]; then
        while IFS='|' read -r svc_name svc_domain; do
            [ -z "$svc_domain" ] && continue
            if should_manage_caddy; then
                svc_url="https://${svc_domain}/"
            else
                svc_url="http://${svc_domain}/"
            fi
            svc_code=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$svc_url" 2>/dev/null) || svc_code="000"
            if [ "$svc_code" != "000" ] && [ "$svc_code" != "502" ] && [ "$svc_code" != "503" ]; then
                echo -e "${GREEN}  ✓ $svc_name ($svc_domain): HTTP $svc_code${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
            else
                echo -e "${RED}  ✗ $svc_name ($svc_domain): HTTP $svc_code${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
        done <<< "$ALL_SVC_DOMAINS"
    fi

    TOTAL=$((PASS_COUNT + FAIL_COUNT))
    if [ "$FAIL_COUNT" -eq 0 ]; then
        echo -e "\n${GREEN}  ✓ All $PASS_COUNT/$TOTAL checks passed${NC}"
    else
        echo -e "\n${YELLOW}  ⚠ $PASS_COUNT passed, $FAIL_COUNT failed out of $TOTAL checks${NC}"
    fi

    docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
        docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true
    exit 0
fi