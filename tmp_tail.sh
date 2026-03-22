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

    if ! caddy validate --config /etc/caddy/Caddyfile 2>/dev/null; then
        return 0  # Syntax error
    fi
    if grep -q 'dns cloudflare' /etc/caddy/Caddyfile 2>/dev/null \
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
    echo -e "${BLUE}  -> Pruning deeply stale images (>7 days old)...${NC}"
    docker image prune -a -f --filter "until=168h" >/dev/null 2>&1 || true
    
    echo -e "${GREEN}  OK Cache bust complete (targeted images + build cache + deep prune)${NC}"
}

restart_edge_stack() {
    local edge_services="socket-proxy traefik route-fallback nginx"

    echo -e "${BLUE}  -> Refreshing edge proxy stack (nginx/traefik/socket-proxy/route-fallback)...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps $edge_services >/dev/null 2>&1 || \
        docker compose -f "$COMPOSE_FILE" up -d --force-recreate $edge_services >/dev/null 2>&1 || true

    # Restart core app entrypoints so new upstream bindings are live.
    docker compose -f "$COMPOSE_FILE" restart frontend backend >/dev/null 2>&1 || true
    docker compose -f "$COMPOSE_FILE" restart $edge_services >/dev/null 2>&1 || true

    # Re-attach expected external networks (idempotent).
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-nginx-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    # Validate Caddy config before restart (H1 fix)
    if command -v caddy >/dev/null 2>&1; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "restart_edge_stack validation"
        fi
        systemctl restart caddy >/dev/null 2>&1 || true
        systemctl restart caddy-watcher >/dev/null 2>&1 || true
    fi
    echo -e "${GREEN}  OK Edge stack refreshed${NC}"
}

wait_for_container_ready() {
    local container_name="$1"
    local timeout_seconds="${2:-180}"
    local elapsed=0
    local state=""

    [ -z "$container_name" ] && return 1

    while [ "$elapsed" -lt "$timeout_seconds" ]; do
        state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name" 2>/dev/null || echo "missing")"
        if [ "$state" = "healthy" ] || [ "$state" = "running" ]; then
            echo -e "${GREEN}  OK $container_name is $state${NC}"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done

    echo -e "${YELLOW}  WARN $container_name not ready after ${timeout_seconds}s (state=$state)${NC}"
    return 1
}

recover_runtime_stack() {
    echo -e "${BLUE}  -> Running runtime recovery (network + core services + edge)...${NC}"

    ensure_update_networks
    ensure_caddy_config_permissions

    if systemctl list-unit-files docker.service >/dev/null 2>&1; then
        echo -e "${BLUE}    -> Restarting Docker daemon...${NC}"
        systemctl restart docker >/dev/null 2>&1 || true
        sleep 8
        ensure_update_networks
    fi

    echo -e "${BLUE}    -> Starting dependency services...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d db pgbouncer redis socket-proxy registry || true
    wait_for_container_ready "smsly-hosting-db-1" 120 || true
    wait_for_container_ready "smsly-hosting-pgbouncer-1" 120 || true
    wait_for_container_ready "smsly-hosting-redis-1" 120 || true

    echo -e "${BLUE}    -> Starting app services...${NC}"
    docker compose -f "$COMPOSE_FILE" -f docker-compose.socket-proxy.yml up -d backend frontend || true
    wait_for_container_ready "smsly-hosting-backend-1" 180 || true
    wait_for_container_ready "smsly-hosting-frontend-1" 120 || true

    echo -e "${BLUE}    -> Starting workers and edge services...${NC}"
    docker compose -f "$COMPOSE_FILE" -f docker-compose.socket-proxy.yml up -d celery celery-beat traefik route-fallback nginx || true

    # Re-attach expected networks (idempotent)
    ensure_container_on_network "smsly-net" "smsly-hosting-backend-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-frontend-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-nginx-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    if command -v caddy >/dev/null 2>&1; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "recover_runtime_stack"
        fi
        systemctl restart caddy >/dev/null 2>&1 || true
        systemctl restart caddy-watcher >/dev/null 2>&1 || true
    fi

    echo -e "${GREEN}  OK Runtime recovery completed${NC}"
}

debug_platform_status() {
    set +e
    echo -e "\n${YELLOW}=== SMSLY DEBUG SNAPSHOT ===${NC}"
    echo "Timestamp: $(date -Iseconds)"
    echo "Install dir: $INSTALL_DIR"
    echo ""

    echo "---- Systemd ----"
    systemctl is-active docker 2>/dev/null || true
    systemctl is-active caddy 2>/dev/null || true
    systemctl is-active caddy-watcher 2>/dev/null || true
    systemctl is-active smsly-autoscaler 2>/dev/null || true
    echo ""

    echo "---- Docker Networks ----"
    docker network ls | grep -E 'smsly|socket-proxy' || true
    echo ""

    echo "---- Compose PS ----"
    docker compose -f "$COMPOSE_FILE" ps || true
    echo ""

    echo "---- Local Health ----"
    curl -iSsf http://127.0.0.1/health 2>/dev/null | head -20 || echo "http://127.0.0.1/health failed"
    echo ""

    echo "---- Backend DNS Checks ----"
    docker compose -f "$COMPOSE_FILE" exec -T backend getent hosts db pgbouncer redis 2>/dev/null || echo "backend DNS check failed"
    echo ""

    echo "---- Key Logs (tail 120) ----"
    docker compose -f "$COMPOSE_FILE" logs --tail=120 backend frontend nginx traefik pgbouncer redis 2>/dev/null || true
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
    recover_runtime_stack
    debug_platform_status
    exit 0
fi

# =============================================================================
# VERIFY MODE  -  Run endpoint checks only (no changes)
# =============================================================================
if [ "${VERIFY_MODE:-false}" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --verify)${NC}"
        exit 1
    fi
    cd "$INSTALL_DIR" 2>/dev/null || { echo -e "${RED}x $INSTALL_DIR not found. Run fresh install first.${NC}"; exit 1; }

    DOMAIN="$(env_get_value "$INSTALL_DIR/.env" "DOMAIN" 2>/dev/null || echo "")"

    echo -e "\n${BLUE}  -> Running endpoint verification...${NC}"
    PASS_COUNT=0
    FAIL_COUNT=0

    # Backend health (internal)
    EP1_URL="http://127.0.0.1/health"
    EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP1_URL" 2>/dev/null) || EP1_CODE="000"
    if [ "$EP1_CODE" = "200" ] || [ "$EP1_CODE" = "301" ]; then
        echo -e "${GREEN}  [OK] Backend (local): HTTP $EP1_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo -e "${RED}  [ERR] Backend (local): HTTP $EP1_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # Platform domain (public-facing  -  tests Caddy -> nginx -> backend chain)
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ]; then
        EP_PUB_URL="http://${DOMAIN}/health"
        EP_PUB_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 10 "$EP_PUB_URL" 2>/dev/null) || EP_PUB_CODE="000"
        if [ "$EP_PUB_CODE" = "200" ] || [ "$EP_PUB_CODE" = "301" ] || [ "$EP_PUB_CODE" = "308" ]; then
            echo -e "${GREEN}  [OK] Platform (${DOMAIN}): HTTP $EP_PUB_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
        else
            echo -e "${RED}  [ERR] Platform (${DOMAIN}): HTTP $EP_PUB_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    fi

    # HTTPS domain (skip for raw IP addresses  -  certs can't be issued for IPs)
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ] && ! echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EP2_URL="https://${DOMAIN}/health"
        EP2_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 10 "$EP2_URL" 2>/dev/null) || EP2_CODE="000"
        if [ "$EP2_CODE" = "200" ] || [ "$EP2_CODE" = "301" ]; then
            echo -e "${GREEN}  [OK] HTTPS: HTTP $EP2_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
        else
            echo -e "${RED}  [ERR] HTTPS: HTTP $EP2_CODE ($EP2_URL)${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    elif echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' 2>/dev/null; then
        echo -e "${YELLOW}  [SKIP] HTTPS: Skipped (IP Mode  -  SSL requires a domain name)${NC}"
    fi

    # Traefik
    EP3_URL="http://127.0.0.1:8081/"
    EP3_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP3_URL" 2>/dev/null) || EP3_CODE="000"
    if [ "$EP3_CODE" != "000" ] && [ "$EP3_CODE" != "502" ]; then
        echo -e "${GREEN}  [OK] Traefik: HTTP $EP3_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo -e "${RED}  [ERR] Traefik: HTTP $EP3_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # Deployed service domains
ALL_SVC_DOMAINS="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
allowlisted_services = $SERVICE_ALLOWLIST_PY
queryset = Service.objects.all()
if allowlisted_services:
    queryset = queryset.filter(name__in=allowlisted_services)
for s in queryset.exclude(public_domain__isnull=True).exclude(public_domain=''):
    print(f'{s.name}|{s.public_domain.strip()}')
" 2>/dev/null | tr -d '\r' || true)"

    if [ -n "$ALL_SVC_DOMAINS" ]; then
        while IFS='|' read -r svc_name svc_domain; do
            [ -z "$svc_domain" ] && continue
            svc_url="https://${svc_domain}/"
            svc_code=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$svc_url" 2>/dev/null) || svc_code="000"
            if [ "$svc_code" != "000" ] && [ "$svc_code" != "502" ] && [ "$svc_code" != "503" ]; then
                echo -e "${GREEN}  [OK] $svc_name ($svc_domain): HTTP $svc_code${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
            else
                echo -e "${RED}  [ERR] $svc_name ($svc_domain): HTTP $svc_code${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
        done <<< "$ALL_SVC_DOMAINS"
    fi

    TOTAL=$((PASS_COUNT + FAIL_COUNT))
    if [ "$FAIL_COUNT" -eq 0 ]; then
        echo -e "\n${GREEN}  [OK] All $PASS_COUNT/$TOTAL checks passed${NC}"
    else
        echo -e "\n${YELLOW}  [WARN] $PASS_COUNT passed, $FAIL_COUNT failed out of $TOTAL checks${NC}"
    fi

    docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
        docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true
    exit 0
fi

# =============================================================================
# UPDATE MODE  -  Fast path for pulling latest code and rebuilding
# =============================================================================
if [ -n "$UPDATE_MODE" ]; then
    echo -e "${YELLOW}[UPDATE] Running in update mode: $UPDATE_MODE${NC}"
    echo -e "${BLUE}  -> Safe update: preserves database/redis volumes and addon data.${NC}"

    # Ensure repo cache directory exists for user service builds
    mkdir -p /opt/smsly-cache/repos
    chmod 775 /opt/smsly-cache
    chown -R 1000:1000 /opt/smsly-cache 2>/dev/null || true

    # --- Pre-flight ----------------------------------------------------------
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}[ERR] Please run as root (sudo bash install.sh --update)${NC}"
        exit 1
    fi

    ensure_caddy_config_permissions

    if [ ! -d "$INSTALL_DIR/.git" ]; then
        echo -e "${RED}[ERR] No git repository found at $INSTALL_DIR. Run a fresh install first.${NC}"
        exit 1
    fi

    if [ ! -f "$INSTALL_DIR/.env" ]; then
        echo -e "${RED}[ERR] No .env file found. Run a fresh install first.${NC}"
        exit 1
    fi

    cd "$INSTALL_DIR"

    echo -e "${BLUE}  -> Validating existing .env configuration...${NC}"
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x .env validation failed. Fix the values above and re-run update.${NC}"
        exit 1
    fi


    # --- Git Stash + Pull (CRITICAL BLINDSPOT FIX) ---------------------------
    echo -e "${BLUE}  -> Checking for local changes...${NC}"
    if ! git diff --quiet HEAD 2>/dev/null || ! git diff --cached --quiet HEAD 2>/dev/null; then
        echo -e "${YELLOW}  [WARN] Local changes detected  -  stashing before pull${NC}"
        git stash push -m "install-update-$(date +%s)"
        touch "$INSTALL_DIR/.git-stash-marker"
    fi

    echo -e "${BLUE}  -> Force-pulling latest code from GitHub...${NC}"
    git fetch origin main
    git reset --hard origin/main

    # Clean up stash marker (pull succeeded, we commit to the new code)
    rm -f "$INSTALL_DIR/.git-stash-marker"

    # --- Validate required files exist ---------------------------------------
    echo -e "${BLUE}  -> Validating deployment files...${NC}"

    if [ ! -f "$COMPOSE_FILE" ]; then
        echo -e "${RED}[ERR] Missing $COMPOSE_FILE  -  cannot deploy.${NC}"
        exit 1
    fi

    if [ ! -f "nginx.conf" ]; then
        echo -e "${RED}[ERR] Missing nginx.conf  -  cannot deploy. This file is required for routing.${NC}"
        exit 1
    fi

    if [ ! -f "backend/Dockerfile" ]; then
        echo -e "${RED}[ERR] Missing backend/Dockerfile${NC}"
        exit 1
    fi

    if [ ! -f "frontend/Dockerfile" ]; then
        echo -e "${RED}[ERR] Missing frontend/Dockerfile${NC}"
        exit 1
    fi

    echo -e "${GREEN}  [OK] All required files present${NC}"

    # --- Disk space check (prevents mid-build failure) -----------------------
    DISK_AVAIL_MB=$(df -BM "$INSTALL_DIR" | tail -1 | awk '{print $4}' | tr -d 'M')
    if [ "$DISK_AVAIL_MB" -lt 2000 ]; then
        echo -e "${YELLOW}  [WARN] WARNING: Only ${DISK_AVAIL_MB}MB disk space available.${NC}"
        echo -e "${YELLOW}    Docker builds typically need 2GB+. Cleaning safe caches (no volume deletion)...${NC}"
        bust_core_build_cache
        DISK_AVAIL_MB=$(df -BM "$INSTALL_DIR" | tail -1 | awk '{print $4}' | tr -d 'M')
        echo -e "${BLUE}  -> Disk space after cleanup: ${DISK_AVAIL_MB}MB${NC}"
        if [ "$DISK_AVAIL_MB" -lt 1000 ]; then
            echo -e "${RED}  [ERR] Still insufficient disk space (${DISK_AVAIL_MB}MB). Need at least 1GB.${NC}"
            exit 1
        fi
    fi

    # --- Targeted Rebuild (CRITICAL BLINDSPOT FIX: --no-deps) ----------------
    # Using --no-deps prevents cascade restart of unrelated services

    # --- Fix script permissions (Git on Windows strips execute bits) ----------
    echo -e "${BLUE}  -> Fixing script permissions...${NC}"
    find "$INSTALL_DIR" -name "*.sh" -exec chmod +x {} \;
    echo -e "${GREEN}  [OK] Script permissions fixed${NC}"

    # Ensure shared networks exist (prod stack uses external networks)
    ensure_update_networks

    # Cache bust only if disk is low (already runs in the disk check above when needed).
    # Moved into case blocks below to avoid redundant double bust.

    case "$UPDATE_MODE" in
        frontend)
            echo -e "${BLUE}  -> Rebuilding frontend container only...${NC}"
            docker compose -f "$COMPOSE_FILE" build --no-cache frontend
            docker compose -f "$COMPOSE_FILE" -f docker-compose.socket-proxy.yml up -d --no-deps frontend
            ;;
        backend)
            echo -e "${BLUE}  -> Rebuilding backend containers...${NC}"
            docker compose -f "$COMPOSE_FILE" build --no-cache backend celery
            echo -e "${BLUE}  -> Ensuring backend dependencies are running...${NC}"
            docker compose -f "$COMPOSE_FILE" -f docker-compose.socket-proxy.yml up -d db pgbouncer redis socket-proxy
            docker compose -f "$COMPOSE_FILE" -f docker-compose.socket-proxy.yml up -d --no-deps backend

            echo -e "${BLUE}  -> Running migrations...${NC}"
            sleep 10  # Wait for backend to start
            # Note: Do NOT run makemigrations here  -  migrations are committed in the repo.
            # Running makemigrations auto-generates files inside the container that conflict
            # with committed migrations on subsequent deploys.
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py migrate --noinput || {
                echo -e "${YELLOW}  [WARN] Migration failed  -  backend may still be starting. Retrying in 15s...${NC}"
                sleep 15
                docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py migrate --noinput
            }

            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput

            # Clean stale celerybeat-schedule (prevents Permission denied crash loop)
            echo -e "${BLUE}  -> Cleaning celerybeat-schedule...${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true

            echo -e "${BLUE}  -> Restarting celery workers...${NC}"
            docker compose -f "$COMPOSE_FILE" -f docker-compose.socket-proxy.yml up -d --no-deps celery celery-beat
            ;;
        full)
            echo -e "${BLUE}  -> [FULL REBUILD] Rebuilding PaaS core (preserving addon databases)...${NC}"

            # 1. Only stop PaaS core services  -  NEVER touch addon containers
            CORE_SERVICES="frontend backend celery celery-beat nginx traefik socket-proxy route-fallback"
            echo -e "${BLUE}    ↳ Stopping core services...${NC}"
            docker compose -f "$COMPOSE_FILE" stop $CORE_SERVICES 2>/dev/null || true
            docker compose -f "$COMPOSE_FILE" rm -f $CORE_SERVICES 2>/dev/null || true

            # 2. Remove old PaaS images (NOT addon images)
            echo -e "${BLUE}    ↳ Removing old core images...${NC}"
            for svc in $CORE_SERVICES; do
                img=$(docker compose -f "$COMPOSE_FILE" config --images 2>/dev/null | grep -i "$svc" || true)
                if [ -n "$img" ]; then
                    docker rmi -f "$img" 2>/dev/null || true
                fi
            done

            # 3. Prune dangling images and build cache
            echo -e "${BLUE}    ↳ Pruning dangling images and build cache...${NC}"
            docker builder prune -af 2>/dev/null || true

            # 4. Ensure shared networks exist (create if missing, don't destroy)
            echo -e "${BLUE}    ↳ Ensuring networks exist...${NC}"
            ensure_update_networks

            # 5. Rebuild core images from scratch
            echo -e "${BLUE}    ↳ Rebuilding core images (no cache)...${NC}"
            docker compose -f "$COMPOSE_FILE" build --no-cache $CORE_SERVICES

            # 6. Start everything (addons stay running, core gets fresh containers)
            echo -e "${BLUE}    ↳ Starting all services...${NC}"
            docker compose -f "$COMPOSE_FILE" -f docker-compose.socket-proxy.yml up -d --force-recreate $CORE_SERVICES

            # 7. Reconnect Traefik + socket-proxy to smsly-proxy network
            #    (recreation drops Docker DNS links  -  causes 502 gateway errors)
            echo -e "${BLUE}    ↳ Reconnecting proxy network...${NC}"
            for ctr in smsly-hosting-traefik-1 smsly-hosting-socket-proxy-1; do
                ensure_container_on_network "smsly-proxy" "$ctr"
            done
            docker restart smsly-hosting-traefik-1 2>/dev/null || true

            # 8. Run migrations
            echo -e "${BLUE}  -> Running migrations...${NC}"
            echo -e "${BLUE}  -> Ensuring backend dependencies are running...${NC}"
            docker compose -f "$COMPOSE_FILE" -f docker-compose.socket-proxy.yml up -d db pgbouncer redis socket-proxy
            sleep 10
            # Note: Do NOT run makemigrations  -  migrations are committed in the repo.
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py migrate --noinput || {
                echo -e "${YELLOW}  [WARN] Migration failed  -  backend may still be starting. Retrying in 15s...${NC}"
                sleep 15
                docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py migrate --noinput
            }

            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput

            # 9. Clean celerybeat-schedule and restart beat
            echo -e "${BLUE}  -> Cleaning celerybeat-schedule...${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true
            docker compose -f "$COMPOSE_FILE" restart celery-beat 2>/dev/null || true
            ;;
    esac

    # --- Ensure Local Docker cloud provider exists --------------------------
    echo -e "${BLUE}  -> Ensuring Local Docker cloud provider exists...${NC}"
    echo "
from apps.cloud.models import CloudProvider
cp, created = CloudProvider.objects.get_or_create(
    provider_type='LOCAL',
    defaults={'name': 'Local Docker', 'is_active': True}
)
if not created and not cp.is_active:
    cp.is_active = True
    cp.save()
" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null || true
    echo -e "${GREEN}  [OK] Cloud provider ready${NC}"

    # Refresh proxy/runtime edge stack so routing and TLS state is always clean.
    # NOTE: restart_edge_stack now handles Caddy validation internally (H1+H2 fix).
    restart_edge_stack

    # Verify nginx loaded the correct custom config (not the default)
    sleep 2
    NGINX_CONFIG_CHECK=$(docker exec smsly-hosting-nginx-1 head -1 /etc/nginx/nginx.conf 2>/dev/null || echo "FAIL")
    if echo "$NGINX_CONFIG_CHECK" | grep -q "events"; then
        echo -e "${GREEN}  [OK] Nginx config verified (custom proxy config loaded)${NC}"
    else
        echo -e "${RED}  [ERR] WARNING: Nginx may be running default config!${NC}"
        echo -e "${YELLOW}    Expected 'events {' but got: $NGINX_CONFIG_CHECK${NC}"
        echo -e "${YELLOW}    Fix: docker compose -f $COMPOSE_FILE up -d --force-recreate nginx${NC}"
    fi

    # --- Caddy: Regenerate Caddyfile with service domains (writes directly to host) --
    if command -v caddy &> /dev/null; then
        echo -e "${BLUE}  -> Regenerating Caddyfile with current service domains...${NC}"

        # -- Step 1: Find the Cloudflare token FIRST (before generating Caddyfile) --
        CADDY_OVERRIDE_DIR="/etc/systemd/system/caddy.service.d"
        CADDY_OVERRIDE_FILE="$CADDY_OVERRIDE_DIR/override.conf"
        CF_TOKEN=""

        # Priority: existing systemd override > .env file > PlatformConfig DB
        if [ -f "$CADDY_OVERRIDE_FILE" ]; then
            CF_TOKEN="$(grep 'CLOUDFLARE_API_TOKEN=' "$CADDY_OVERRIDE_FILE" 2>/dev/null | sed 's/.*CLOUDFLARE_API_TOKEN=//;s/"$//' || true)"
        fi
        if [ -z "$CF_TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
            CF_TOKEN="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
        fi
        # Fallback: read from PlatformConfig in the database (set via Settings UI)
        if [ -z "$CF_TOKEN" ] || [ "$CF_TOKEN" = "fake" ]; then
            DB_TOKEN="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
config = PlatformConfig.load()
token = (getattr(config, 'cloudflare_api_token', '') or '').strip()
if token and token.lower() not in ('fake', 'changeme', 'test', ''):
    print(token)
" 2>/dev/null || true)"
            DB_TOKEN="$(echo "$DB_TOKEN" | tr -d '[:space:]')"
            if [ -n "$DB_TOKEN" ]; then
                CF_TOKEN="$DB_TOKEN"
                echo -e "${GREEN}  [OK] Cloudflare token found in Settings DB${NC}"
                # Sync back to .env so it persists
                if grep -q 'CLOUDFLARE_API_TOKEN' "$INSTALL_DIR/.env" 2>/dev/null; then
                    sed -i "s/CLOUDFLARE_API_TOKEN=.*/CLOUDFLARE_API_TOKEN=$CF_TOKEN/" "$INSTALL_DIR/.env"
                else
                    echo "CLOUDFLARE_API_TOKEN=$CF_TOKEN" >> "$INSTALL_DIR/.env"
                fi
            fi
        fi

        # -- Step 2: Generate Caddyfile WITH dns cloudflare if token exists --
        if [ -n "$CF_TOKEN" ] && [ "$CF_TOKEN" != "fake" ]; then
            echo -e "${GREEN}  [OK] Cloudflare token available  -  generating Caddyfile with wildcard SSL${NC}"

            # Ensure systemd override is set
            mkdir -p "$CADDY_OVERRIDE_DIR"
            cat > "$CADDY_OVERRIDE_FILE" <<ENVEOF
[Service]
ExecStart=
ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile
Environment="CLOUDFLARE_API_TOKEN=$CF_TOKEN"
ENVEOF
            chmod 600 "$CADDY_OVERRIDE_FILE"
            systemctl daemon-reload

            # Discover domain
            cf_domain=""
            cf_domain="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
" 2>/dev/null | tr -d '[:space:]' || true)"
            if [ -z "$cf_domain" ]; then
                cf_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
            fi

            # Discover wildcard-covered hosts and non-wildcard service blocks.
            # - Wildcard-covered hosts route through Traefik via matcher.
            # - Unknown wildcard hosts route to /notice on frontend.
            # - External custom domains keep explicit TLS blocks with Host rewrite.
            cf_wildcard_known_hosts=""
            cf_wildcard_known_hosts="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
suffix = '.${cf_domain}'.lower().strip()
hosts = set()
allowlisted_services = $SERVICE_ALLOWLIST_PY
queryset = Service.objects.all()
if allowlisted_services:
    queryset = queryset.filter(name__in=allowlisted_services)
for svc in queryset:
    d = (svc.public_domain or '').strip().lower()
    if d and suffix and d.endswith(suffix):
        hosts.add(d)
    for cd in (svc.custom_domains or []):
        cd = (cd or '').strip().lower()
        if cd and suffix and cd.endswith(suffix):
            hosts.add(cd)
print(' '.join(sorted(hosts)))
" 2>/dev/null | tr -d '\r' | tr -d '\n' || true)"

            cf_svc_blocks=""
            cf_svc_blocks="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
suffix = '.${cf_domain}'.lower().strip()
seen = set()
allowlisted_services = $SERVICE_ALLOWLIST_PY
queryset = Service.objects.all()
if allowlisted_services:
    queryset = queryset.filter(name__in=allowlisted_services)
for svc in queryset:
    public_domain = (svc.public_domain or '').strip().lower()
    if public_domain and (not suffix or not public_domain.endswith(suffix)) and public_domain not in seen:
        seen.add(public_domain)
        print(f'{public_domain} {{\n    reverse_proxy localhost:8081\n    encode gzip\n    tls {{\n        dns cloudflare {{env.CLOUDFLARE_API_TOKEN}}\n    }}\n}}\n')

    for item in (svc.custom_domains or []):
        custom_domain = (item or '').strip().lower()
        if not custom_domain:
            continue
        if suffix and custom_domain.endswith(suffix):
            continue
        if custom_domain in seen:
            continue
        seen.add(custom_domain)

        if public_domain and public_domain != custom_domain:
            print(f'{custom_domain} {{\n    reverse_proxy localhost:8081 {{\n        header_up Host {public_domain}\n    }}\n    encode gzip\n    tls {{\n        dns cloudflare {{env.CLOUDFLARE_API_TOKEN}}\n    }}\n}}\n')
        else:
            print(f'{custom_domain} {{\n    reverse_proxy localhost:8081\n    encode gzip\n    tls {{\n        dns cloudflare {{env.CLOUDFLARE_API_TOKEN}}\n    }}\n}}\n')
" 2>/dev/null | tr -d '\r' || true)"

            # Only generate wildcard Caddyfile for real domains
            cf_is_real_domain=false
            if [ -n "$cf_domain" ] && [ "$cf_domain" != "localhost" ]; then
                if ! echo "$cf_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
                    cf_is_real_domain=true
                fi
            fi

            if [ "$cf_is_real_domain" = "true" ]; then
                cf_known_stanza=""
                if [ -n "$cf_wildcard_known_hosts" ]; then
                    cf_known_stanza="    @known_hosts host ${cf_wildcard_known_hosts}
    handle @known_hosts {
        reverse_proxy localhost:8081
    }"
                fi

                cat > /etc/caddy/Caddyfile <<CFCADDY
# Auto-generated with Cloudflare DNS challenge (wildcard SSL)
${cf_domain} {
    reverse_proxy localhost:8090
    encode gzip
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
    log {
        output file /var/log/caddy/access.log
    }
}

*.${cf_domain} {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
${cf_known_stanza}
    handle {
        rewrite * /notice
        reverse_proxy localhost:8090
    }
}

:80 {
    handle {
        rewrite * /notice
        reverse_proxy localhost:8090
    }
}

${cf_svc_blocks}
CFCADDY
                caddy fmt --overwrite /etc/caddy/Caddyfile 2>/dev/null || true
                echo -e "${GREEN}  [OK] Caddyfile generated with wildcard SSL for *.${cf_domain}${NC}"
            else
                # IP mode or no domain  -  fall back to safe Caddyfile
                generate_safe_caddyfile "update flow (IP mode)"
            fi
        else
            # No valid token  -  generate safe Caddyfile (no dns cloudflare)
            generate_safe_caddyfile "update flow caddy regen"

            # Strip any leftover dns cloudflare blocks to prevent crash
            if grep -q 'dns cloudflare' /etc/caddy/Caddyfile 2>/dev/null; then
                echo -e "${YELLOW}  [WARN] No Cloudflare token  -  removing DNS challenge from Caddyfile${NC}"
                python3 -c "
import re
with open('/etc/caddy/Caddyfile') as f:
    content = f.read()
content = re.sub(r'\s*tls\s*\{[^}]*\}\s*\n?', '\n', content)
with open('/etc/caddy/Caddyfile', 'w') as f:
    f.write(content)
print('Stripped tls blocks')
" 2>/dev/null || true
                echo -e "${YELLOW}  [WARN] Wildcard HTTPS disabled. Set CLOUDFLARE_API_TOKEN in .env to re-enable.${NC}"
            fi
        fi

        # Final validation  -  if still broken, regenerate safe fallback
        if caddy_needs_fix; then
            generate_safe_caddyfile "post-update validation"
        fi

        systemctl restart caddy 2>/dev/null || true
        systemctl restart caddy-watcher 2>/dev/null || true

        # Verify Caddy is running
        sleep 2
        if systemctl is-active --quiet caddy 2>/dev/null; then
            echo -e "${GREEN}  [OK] Caddy config regenerated and running${NC}"
        else
            echo -e "${YELLOW}  [WARN] Caddy failed to start. Run: journalctl -u caddy --no-pager -n 20${NC}"
        fi
    fi

    # --- Auto-redeploy active services (only if platform code changed) --
    # H6 fix: Only redeploy if git detected actual changes (prevents unnecessary deploys)
    GIT_CHANGES="$(cd "$INSTALL_DIR" && git diff HEAD@{1} --name-only 2>/dev/null | head -5 || true)"
    if [ -n "$GIT_CHANGES" ]; then
        echo -e "${BLUE}  -> Auto-redeploying active services (platform code changed)...${NC}"
        docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
import traceback
try:
    from apps.deployments.models import Service, Deployment
    from apps.cloud.models import CloudProvider
    from apps.deployments.tasks import smart_deploy_task
    from django.utils import timezone
    provider = CloudProvider.objects.filter(is_active=True).first()
    if not provider:
        print('WARN: No active cloud provider')
    else:
        count = 0
        allowlisted_services = $SERVICE_ALLOWLIST_PY
        queryset = Service.objects.all()
        if allowlisted_services:
            queryset = queryset.filter(name__in=allowlisted_services)
        for svc in queryset:
            dep = svc.deployments.filter(status='ACTIVE').order_by('-created_at').first()
            if not dep or not dep.commit_hash:
                continue
            svc.deployments.filter(status='ACTIVE').update(
                status='CANCELLED', finished_at=timezone.now())
            new_dep = Deployment.objects.create(
                service=svc,
                status='QUEUED',
                commit_hash=dep.commit_hash,
                commit_message='Platform update auto-redeploy',
            )
            smart_deploy_task.delay(str(new_dep.id), str(provider.id), skip_review=True)
            count += 1
            print(f'  Queued: {svc.name} ({dep.commit_hash[:7]})')
        print(f'OK: {count} service(s) queued for redeploy')
except Exception as e:
    print(f'WARN: {e}')
    traceback.print_exc()
" 2>/dev/null || echo -e "${YELLOW}  [WARN] Auto-redeploy skipped (backend not ready)${NC}"
    else
        echo -e "${GREEN}  [OK] No platform code changes detected  -  skipping auto-redeploy${NC}"
    fi

    # --- Endpoint Verification (3 checks) ----------------------------------
    echo -e "\n${BLUE}  -> Running endpoint verification (3 checks)...${NC}"
    sleep 5
    PASS_COUNT=0
    FAIL_COUNT=0

    # -- Check 1: Backend API health (through Nginx on port 80) --
    EP1_URL="http://127.0.0.1/health"
    echo -e "${BLUE}  [1/3] Backend API health...${NC}"
    echo -e "${BLUE}        Endpoint: $EP1_URL${NC}"
    BACKEND_OK=false
    EP1_CODE="000"
    for attempt in 1 2 3 4 5; do
        EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP1_URL" 2>/dev/null) || EP1_CODE="000"
        if [ "$EP1_CODE" = "200" ] || [ "$EP1_CODE" = "301" ]; then
            BACKEND_OK=true
            break
        fi
        if [ "$attempt" -eq 1 ]; then
            docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true
        fi
        sleep 3
    done
    if [ "$BACKEND_OK" = "true" ]; then
        EP1_RESULT="${GREEN}PASS${NC}"
        echo -e "${GREEN}  [OK] [1/3] PASS  -  HTTP $EP1_CODE${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        EP1_RESULT="${RED}FAIL${NC}"
        echo -e "${RED}  [ERR] [1/3] FAIL  -  HTTP $EP1_CODE${NC}"
        echo -e "${YELLOW}        Fix: docker compose -f $COMPOSE_FILE logs --tail=30 backend${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # -- Check 2: HTTPS platform domain (auto-discovered from DB -> through Caddy) --
    echo -e "${BLUE}  [2/3] HTTPS platform domain...${NC}"
    # Auto-discover domain from PlatformConfig in DB  -  zero config needed
    EP_DOMAIN="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
config = PlatformConfig.load()
d = (config.domain or '').strip()
if d and d != 'localhost':
    print(d)
" 2>/dev/null | tr -d '[:space:]' || true)"
    # Fallback to .env if DB query failed
    if [ -z "$EP_DOMAIN" ]; then
        EP_DOMAIN="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    fi
    HTTPS_OK=false
    EP2_CODE="---"
    EP2_URL="(skipped)"
    if [ -n "$EP_DOMAIN" ] && [ "$EP_DOMAIN" != "localhost" ]; then
        EP2_URL="https://${EP_DOMAIN}/health"
        echo -e "${BLUE}        Endpoint: $EP2_URL${NC}"
        for attempt in 1 2 3; do
            EP2_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$EP2_URL" 2>/dev/null) || EP2_CODE="000"
            if [ "$EP2_CODE" = "200" ] || [ "$EP2_CODE" = "301" ]; then
                HTTPS_OK=true
                break
            fi
            sleep 3
        done
        if [ "$HTTPS_OK" = "true" ]; then
            EP2_RESULT="${GREEN}PASS${NC}"
            echo -e "${GREEN}  [OK] [2/3] PASS  -  HTTP $EP2_CODE${NC}"
            PASS_COUNT=$((PASS_COUNT + 1))
        else
            EP2_RESULT="${RED}FAIL${NC}"
            echo -e "${RED}  [ERR] [2/3] FAIL  -  HTTP $EP2_CODE${NC}"
            echo -e "${YELLOW}        Fix: systemctl status caddy && journalctl -u caddy --no-pager -n 15${NC}"
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    else
        EP2_RESULT="${YELLOW}SKIP${NC}"
        echo -e "${YELLOW}  [SKIP] [2/3] SKIPPED (no domain configured)${NC}"
    fi

    # -- Check 3+: ALL deployed services (auto-discovered from DB) --
    echo -e "${BLUE}  [3/N] Deployed services routing...${NC}"

    # Query ALL active service domains from the DB (public + custom)
ALL_SVC_DOMAINS="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
allowlisted_services = $SERVICE_ALLOWLIST_PY
queryset = Service.objects.all()
if allowlisted_services:
    queryset = queryset.filter(name__in=allowlisted_services)
for svc in queryset.exclude(public_domain__isnull=True).exclude(public_domain='').order_by('name'):
    print(f'{svc.name}|{svc.public_domain.strip()}')
    for cd in (svc.custom_domains or []):
        cd = cd.strip()
        if cd:
            print(f'{svc.name} (custom)|{cd}')
" 2>/dev/null | tr -d '\r' || true)"

    # Also check Traefik port directly
    EP3_URL="http://127.0.0.1:8081/"
    EP3_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP3_URL" 2>/dev/null) || EP3_CODE="000"
    if [ "$EP3_CODE" != "000" ] && [ "$EP3_CODE" != "502" ]; then
        EP3_RESULT="${GREEN}PASS${NC}"
        echo -e "${GREEN}  [OK] Traefik proxy ($EP3_URL)  -  HTTP $EP3_CODE${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        EP3_RESULT="${RED}FAIL${NC}"
        echo -e "${RED}  [ERR] Traefik proxy ($EP3_URL)  -  HTTP $EP3_CODE${NC}"
        echo -e "${YELLOW}        Fix: docker compose -f $COMPOSE_FILE logs --tail=20 traefik${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # Collect service results for the table
    SVC_RESULTS=""
    SVC_COUNT=0
    if [ -n "$ALL_SVC_DOMAINS" ]; then
        while IFS='|' read -r svc_name svc_domain; do
            [ -z "$svc_domain" ] && continue
            SVC_COUNT=$((SVC_COUNT + 1))
            svc_url="https://${svc_domain}/"
            echo -e "${BLUE}        Testing: $svc_name -> $svc_url${NC}"
            svc_code="000"
            svc_ok=false
            for attempt in 1 2 3; do
                svc_code=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$svc_url" 2>/dev/null) || svc_code="000"
                if [ "$svc_code" != "000" ] && [ "$svc_code" != "502" ] && [ "$svc_code" != "503" ]; then
                    svc_ok=true
                    break
                fi
                sleep 2
            done
            if [ "$svc_ok" = "true" ]; then
                svc_result="${GREEN}PASS${NC}"
                echo -e "${GREEN}  [OK] $svc_name: HTTP $svc_code${NC}"
                PASS_COUNT=$((PASS_COUNT + 1))
            else
                svc_result="${RED}FAIL${NC}"
                echo -e "${RED}  [ERR] $svc_name: HTTP $svc_code${NC}"
                FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
            SVC_RESULTS="${SVC_RESULTS}${svc_name}|${svc_url}|${svc_code}|${svc_result}\n"
        done <<< "$ALL_SVC_DOMAINS"
    fi
    if [ "$SVC_COUNT" -eq 0 ]; then
        echo -e "${YELLOW}        No active services deployed${NC}"
    fi

    # -- Results Table --
    TOTAL_CHECKS=$((PASS_COUNT + FAIL_COUNT))
    echo ""
    echo -e "${BLUE}  ╔══════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}  ║                        ENDPOINT VERIFICATION REPORT                     ║${NC}"
    echo -e "${BLUE}  ╠════════════════════════════════════════════════════════╦══════╦══════════╣${NC}"
    echo -e "${BLUE}  ║  Endpoint                                            ║ HTTP ║  Result  ║${NC}"
    echo -e "${BLUE}  ╠════════════════════════════════════════════════════════╬══════╬══════════╣${NC}"
    printf "  ║  %-52.52s ║ %-4s ║ " "Backend: $EP1_URL" "$EP1_CODE"
    echo -e " $EP1_RESULT  ║"
    printf "  ║  %-52.52s ║ %-4s ║ " "HTTPS: $EP2_URL" "$EP2_CODE"
    echo -e " $EP2_RESULT  ║"
    printf "  ║  %-52.52s ║ %-4s ║ " "Traefik: $EP3_URL" "$EP3_CODE"
    echo -e " $EP3_RESULT  ║"
    # Print each deployed service row
    if [ -n "$SVC_RESULTS" ]; then
        echo -e "${BLUE}  ╠════════════════════════════════════════════════════════╬══════╬══════════╣${NC}"
        while IFS='|' read -r s_name s_url s_code s_result; do
            [ -z "$s_name" ] && continue
            printf "  ║  %-52.52s ║ %-4s ║ " "$s_name" "$s_code"
            echo -e " $s_result  ║"
        done <<< "$(echo -e "$SVC_RESULTS")"
    fi
    echo -e "${BLUE}  ╚════════════════════════════════════════════════════════╩══════╩══════════╝${NC}"

    # -- Summary --
    if [ "$FAIL_COUNT" -eq 0 ]; then
        echo -e "\n${GREEN}  [OK] All $PASS_COUNT/$TOTAL_CHECKS endpoint checks passed${NC}"
    else
        echo -e "\n${YELLOW}  [WARN] $PASS_COUNT passed, $FAIL_COUNT failed out of $TOTAL_CHECKS checks${NC}"
    fi

    # Show container status
    echo -e "\n${BLUE}Container Status:${NC}"
    docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
        docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true

    # --- Update autoscaler service (picks up code changes + new token) --------
    if [ -f "$INSTALL_DIR/smsly-autoscaler.py" ]; then
        echo -e "${BLUE}  -> Updating smsly-autoscaler service...${NC}"
        mkdir -p /opt/smsly
        cp "$INSTALL_DIR/smsly-autoscaler.py" /opt/smsly/autoscaler.py
        chmod +x /opt/smsly/autoscaler.py

        AUTOSCALER_API_TOKEN="$(env_get_value "$INSTALL_DIR/.env" "AUTOSCALER_API_TOKEN")"
        if [ -n "$AUTOSCALER_API_TOKEN" ] && [ -f /etc/systemd/system/smsly-autoscaler.service ]; then
            # Update token in existing service file
            sed -i "s|^Environment=AUTOSCALER_API_TOKEN=.*|Environment=AUTOSCALER_API_TOKEN=${AUTOSCALER_API_TOKEN}|" \
                /etc/systemd/system/smsly-autoscaler.service
            systemctl daemon-reload
        fi
        systemctl restart smsly-autoscaler 2>/dev/null || true
        echo -e "${GREEN}  [OK] Autoscaler updated${NC}"
    fi

    # --- Re-apply OOM protection (scores reset when containers restart) ------
    echo -e "${BLUE}  -> Re-applying OOM protection for critical containers...${NC}"
    for CONTAINER in smsly-hosting-nginx-1 smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgbouncer-1 smsly-hosting-celery-1 smsly-hosting-celery-beat-1 smsly-socket-proxy; do
        CPID=$(docker inspect --format '{{.State.Pid}}' "$CONTAINER" 2>/dev/null || echo "")
        if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
            echo -500 > "/proc/$CPID/oom_score_adj" 2>/dev/null || true
        fi
    done
    echo -e "${GREEN}  [OK] OOM protection set (core, database, celery, proxy)${NC}"

    trap - EXIT
    echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}   [OK] UPDATE SUCCESSFUL ($UPDATE_MODE)${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  Debug snapshot:    sudo bash install.sh --debug${NC}"
    echo -e "${YELLOW}  Runtime recovery:  sudo bash install.sh --recover${NC}"
    exit 0
fi

# =============================================================================
# FRESH INSTALL  -  Full setup from scratch
# =============================================================================

# -----------------------------------------------------------------------------
# 1. Pre-flight Checks
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[1/9] Checking system requirements...${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERR] Please run as root (sudo bash install.sh)${NC}"
    exit 1
fi

# Check OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo -e "${BLUE}  Detected: $NAME $VERSION_ID${NC}"
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
        echo -e "${YELLOW}[WARN] Warning: This script is optimized for Ubuntu/Debian.${NC}"
        if [ -e /dev/tty ]; then
             echo -e "${YELLOW}  Press ENTER to continue anyway, or Ctrl+C to abort.${NC}"
             read -r < /dev/tty
        else
             echo -e "${YELLOW}  [WARN] Non-interactive mode: Continuing automatically...${NC}"
        fi
    fi
fi

# --- Disk space check (prevents mid-build OOM / no-space failures) ----------
DISK_AVAIL_MB=$(df -BM / | tail -1 | awk '{print $4}' | tr -d 'M')
echo -e "${BLUE}  Disk space available: ${DISK_AVAIL_MB}MB${NC}"
if [ "$DISK_AVAIL_MB" -lt 3000 ]; then
    echo -e "${YELLOW}  [WARN] Low disk space (${DISK_AVAIL_MB}MB). Recommended: 3GB+${NC}"
    echo -e "${YELLOW}    Attempting Docker cache cleanup...${NC}"
    docker system prune -f 2>/dev/null || true
    docker builder prune -f 2>/dev/null || true
    DISK_AVAIL_MB=$(df -BM / | tail -1 | awk '{print $4}' | tr -d 'M')
    if [ "$DISK_AVAIL_MB" -lt 1500 ]; then
        echo -e "${RED}  [ERR] Insufficient disk space (${DISK_AVAIL_MB}MB). Need at least 1.5GB for fresh install.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  [OK] After cleanup: ${DISK_AVAIL_MB}MB available${NC}"
fi
echo -e "${GREEN}  [OK] Pre-flight checks passed${NC}"

# -----------------------------------------------------------------------------
# 2. Dependency Management & cleanup
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[2/9] Installing dependencies...${NC}"

# Stop conflicting services if present (anything that holds port 80/443)
# NOTE: Don't stop Caddy here  -  we install/configure it in step 7.
# Stopping it on re-installs breaks the reverse proxy unnecessarily.
for svc in nginx apache2; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo -e "${YELLOW}  [WARN] Stopping conflicting service: $svc${NC}"
        systemctl stop "$svc" || true
        systemctl disable "$svc" || true
    fi
done

# --- NUCLEAR CLEANUP: Remove ALL stale SMSLY containers, volumes, networks --
# This prevents: port conflicts, stale DB password volumes, orphan containers
echo -e "${BLUE}  -> Cleaning up previous SMSLY installation artifacts...${NC}"

# Stop and remove stale smsly-hosting platform containers (NOT user-deployed services)
SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly-hosting-" -q 2>/dev/null || true)
if [ -n "$SMSLY_CONTAINERS" ]; then
    echo -e "${YELLOW}  -> Stopping smsly-hosting platform container(s)...${NC}"
    docker stop $SMSLY_CONTAINERS 2>/dev/null || true
    docker rm -f $SMSLY_CONTAINERS 2>/dev/null || true
fi

# Remove stale Docker volumes (postgres data with old passwords, etc.)
SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly" -q 2>/dev/null || true)
if [ -n "$SMSLY_VOLUMES" ]; then
    if [ "${SMSLY_ALLOW_DESTRUCTIVE_FRESH:-0}" = "1" ]; then
        echo -e "${YELLOW}  -> Removing stale SMSLY volumes (SMSLY_ALLOW_DESTRUCTIVE_FRESH=1)...${NC}"
        for vol in $SMSLY_VOLUMES; do
            docker volume rm "$vol" 2>/dev/null || true
        done
    else
        echo -e "${YELLOW}  [WARN] Existing SMSLY volumes detected; preserving data by default.${NC}"
        echo -e "${YELLOW}    Use --wipe for full reset, or set SMSLY_ALLOW_DESTRUCTIVE_FRESH=1 to delete volumes in fresh install.${NC}"
    fi
fi

# Remove stale Docker networks
SMSLY_NETWORKS=$(docker network ls --filter "name=smsly" -q 2>/dev/null || true)
if [ -n "$SMSLY_NETWORKS" ]; then
    for net in $SMSLY_NETWORKS; do
        docker network rm "$net" 2>/dev/null || true
    done
fi

echo -e "${GREEN}  [OK] Previous artifacts cleaned${NC}"

apt-get update -qq
apt-get install -y curl wget git python3 python3-pip python3-venv openssl ca-certificates gnupg lsb-release dnsutils

# Install Docker if missing
if ! command -v docker &> /dev/null; then
    echo -e "${BLUE}  -> Installing Docker...${NC}"
    mkdir -m 0755 -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
    echo -e "${GREEN}  [OK] Docker already installed ($(docker --version | head -c 40))${NC}"
fi

# Ensure docker compose is available
if ! docker compose version >/dev/null 2>&1; then
    echo -e "${BLUE}  -> Installing Docker Compose plugin...${NC}"
    apt-get install -y docker-compose-plugin || true
fi
echo -e "${GREEN}  [OK] Dependencies installed${NC}"

# -----------------------------------------------------------------------------
# 3. Configuration & Secrets (IDEMPOTENT)
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[3/9] Configuration...${NC}"

mkdir -p "$INSTALL_DIR"

# Ensure we are in the install directory with correct files
if [ "$(pwd)" != "$INSTALL_DIR" ]; then
    echo -e "${BLUE}  -> Setting up installation in $INSTALL_DIR${NC}"
    if [ -f "docker-compose.prod.yml" ]; then
        if [ "${SMSLY_FORCE_SOURCE_SYNC:-0}" = "1" ]; then
            cp -rf . "$INSTALL_DIR/"
        else
            cp -rn . "$INSTALL_DIR/" 2>/dev/null || cp -r . "$INSTALL_DIR/"
        fi
    else
        if [ -d "$INSTALL_DIR/.git" ]; then
             cd "$INSTALL_DIR"
             # Force-reset to match remote (handles chmod changes, local edits, etc.)
             git fetch origin main
             git reset --hard origin/main
        else
             git clone https://github.com/SMSLYCLOUD/smsly-hosting.git "$INSTALL_DIR"
        fi
    fi
fi
cd "$INSTALL_DIR"

# --- BLINDSPOT FIX: Validate required deployment files ----------------------
echo -e "${BLUE}  -> Validating deployment files...${NC}"
MISSING_FILES=()
for required_file in "$COMPOSE_FILE" "nginx.conf" "backend/Dockerfile" "frontend/Dockerfile" "backend/entrypoint.sh"; do
    if [ ! -f "$required_file" ]; then
        MISSING_FILES+=("$required_file")
    fi
done

if [ ${#MISSING_FILES[@]} -gt 0 ]; then
    echo -e "${RED}[ERR] Missing required files:${NC}"
    for f in "${MISSING_FILES[@]}"; do
        echo -e "${RED}    - $f${NC}"
    done
    exit 1
fi
echo -e "${GREEN}  [OK] All required deployment files present${NC}"

# --- BLINDSPOT FIX: Ensure correct compose file is used ---------------------
# Check if any containers are running with the wrong compose file (dev instead of prod)
if docker compose ps --format "table {{.Name}}" 2>/dev/null | grep -q "smsly-hosting"; then
    echo -e "${YELLOW}  [WARN] Found containers running from docker-compose.yml (dev). Stopping...${NC}"
    docker compose down 2>/dev/null || true
fi

# --- IDEMPOTENCY: Skip secret generation if .env already exists -------------
if [ -f "$INSTALL_DIR/.env" ]; then
    echo -e "${GREEN}  [OK] Existing .env found  -  preserving configuration${NC}"
    echo -e "${BLUE}  -> Backing up existing .env to .env.backup${NC}"
    cp "$INSTALL_DIR/.env" "$INSTALL_DIR/.env.backup"

    # Backfill newer required keys and validate before deployment.
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x Existing .env is invalid. Fix it or restore .env.backup and rerun.${NC}"
        exit 1
    fi

    # Source existing values for summary output.
    source "$INSTALL_DIR/.env" 2>/dev/null || true
    DOMAIN="${DOMAIN:-localhost}"
    USE_SSL="${USE_SSL:-false}"
    PUBLIC_IP="$(detect_public_ip)"


else
    # --- Fresh install: generate secrets ------------------------------------
    # Force IPv4 to ensure valid URL syntax (avoiding [IPv6] bracket issues)
    PUBLIC_IP="$(detect_public_ip)"

    # Allow non-interactive SSL installs by pre-seeding env vars:
    #   USE_SSL=true DOMAIN=cloud.smsly.cloud ACME_EMAIL=admin@example.com SKIP_SCREEN=1 bash install.sh
    PRESET_DOMAIN="${DOMAIN:-}"
    PRESET_ACME_EMAIL="${ACME_EMAIL:-}"
    PRESET_USE_SSL="${USE_SSL:-}"

    echo -e "\n${BLUE}Select Deployment Mode:${NC}"
    echo -e "  1) ${GREEN}IP Mode${NC} (Easy) - http://$PUBLIC_IP:8090"
    echo -e "  2) ${GREEN}SSL Mode${NC} (Prod) - https://your-domain.com (Requires DNS A Record pointing to $PUBLIC_IP)"

    # If env vars are pre-seeded, skip prompting even in interactive shells.
    if [ "${PRESET_USE_SSL}" = "true" ] && [ -n "${PRESET_DOMAIN}" ] && [ -n "${PRESET_ACME_EMAIL}" ]; then
        echo -e "${BLUE}  -> Preset detected. Using SSL Mode for ${PRESET_DOMAIN}.${NC}"
        MODE_CHOICE=2
    elif [ -e /dev/tty ]; then
        read -p "Enter choice [1]: " MODE_CHOICE < /dev/tty
        MODE_CHOICE=${MODE_CHOICE:-1}
    else
        echo -e "${YELLOW}  [WARN] Non-interactive mode detected. Defaulting to IP Mode.${NC}"
        MODE_CHOICE=1
    fi

    DOMAIN=""
    ACME_EMAIL=""
    USE_SSL="false"

    if [ "$MODE_CHOICE" -eq "2" ]; then
        USE_SSL="true"
        if [ ! -e /dev/tty ] && [ -n "${PRESET_DOMAIN}" ] && [ -n "${PRESET_ACME_EMAIL}" ]; then
            DOMAIN="${PRESET_DOMAIN}"
            ACME_EMAIL="${PRESET_ACME_EMAIL}"
        else
            while [ -z "$DOMAIN" ]; do
                read -p "Enter your Domain (e.g., app.example.com): " DOMAIN < /dev/tty
            done

            while [ -z "$ACME_EMAIL" ]; do
                read -p "Enter Email for SSL (e.g., admin@example.com): " ACME_EMAIL < /dev/tty
            done
        fi

        echo -e "${BLUE}  -> Verifying DNS for $DOMAIN...${NC}"
        if command -v host &> /dev/null; then
            DETECTED_IP=$(host -t A "$DOMAIN" 2>/dev/null | awk '{print $NF}' | tail -n 1)
            if [[ "$DETECTED_IP" != "$PUBLIC_IP" && "$DETECTED_IP" != "127.0.0.1" ]]; then
                echo -e "${YELLOW}  [WARN] WARNING: DNS for $DOMAIN ($DETECTED_IP) does not match this server ($PUBLIC_IP).${NC}"
                echo -e "${YELLOW}  SSL generation may fail. Ensure your DNS A record is set.${NC}"
                if [ -e /dev/tty ]; then
                    read -p "  Continue anyway? (y/n) " -n 1 -r < /dev/tty
                    echo
                    if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 1; fi
                else
                    echo -e "${RED}x Non-interactive install cannot prompt for DNS mismatch. Aborting.${NC}"
                    exit 1
                fi
            else
                echo -e "${GREEN}  [OK] DNS looks correct.${NC}"
            fi
        fi
    else
        DOMAIN="$PUBLIC_IP"
        echo -e "${BLUE}  -> Using IP Mode: $PUBLIC_IP${NC}"
    fi

    # --- Wildcard Subdomain & Cloudflare Setup (SSL mode only) ------------
    WILDCARD_SUBDOMAINS="false"
    CLOUDFLARE_API_TOKEN=""
    if [ "$USE_SSL" = "true" ] && [ -n "$DOMAIN" ] && [ "$DOMAIN" != "$PUBLIC_IP" ]; then
        echo ""
        echo -e "${BLUE}  Wildcard subdomains allow deployed services to get automatic SSL.${NC}"
        echo -e "  e.g., myapp-abc123.${DOMAIN} will automatically have HTTPS."
        echo -e "  This requires a Cloudflare API Token with DNS:Edit permission.\n"

        PRESET_WILDCARD="${WILDCARD_SUBDOMAINS:-}"
        PRESET_CF_TOKEN="${CLOUDFLARE_API_TOKEN:-}"

        if [ -n "$PRESET_WILDCARD" ] && [ -n "$PRESET_CF_TOKEN" ]; then
            WILDCARD_SUBDOMAINS="$PRESET_WILDCARD"
            CLOUDFLARE_API_TOKEN="$PRESET_CF_TOKEN"
            echo -e "${BLUE}  -> Preset detected: wildcard=$WILDCARD_SUBDOMAINS${NC}"
        elif [ -e /dev/tty ]; then
            read -p "  Enable wildcard subdomains? (y/n) [n]: " WILDCARD_CHOICE < /dev/tty
            WILDCARD_CHOICE=${WILDCARD_CHOICE:-n}
            if [[ $WILDCARD_CHOICE =~ ^[Yy]$ ]]; then
                WILDCARD_SUBDOMAINS="true"
                while [ -z "$CLOUDFLARE_API_TOKEN" ]; do
                    read -sp "  Enter Cloudflare API Token (DNS:Edit): " CLOUDFLARE_API_TOKEN < /dev/tty
                    echo
                done
                echo -e "${GREEN}  [OK] Wildcard subdomains enabled.${NC}"
            fi
        fi
    fi

    # --- Generate Secrets (Python-only, NO invalid fallback) ----------------
    echo -e "${BLUE}  -> Generating secure credentials...${NC}"

    # Install cryptography lib (--break-system-packages for Python 3.12+ on Ubuntu 24.04)
    pip3 install cryptography -q --break-system-packages 2>/dev/null || \
        pip3 install cryptography -q 2>/dev/null || true

    # Generate secrets  -  Python is the ONLY source of truth for Fernet keys
    SECRETS_GENERATED=false
    if python3 -c "
import secrets, string
from cryptography.fernet import Fernet

chars = string.ascii_letters + string.digits
secret_key = ''.join(secrets.choice(chars) for _ in range(50))
fernet_key = Fernet.generate_key().decode()
pg_pass = secrets.token_hex(16)
redis_pass = secrets.token_hex(16)
gateway_secret = secrets.token_hex(32)
webhook_secret = secrets.token_hex(32)
autoscaler_token = secrets.token_hex(32)

# Validate the Fernet key before outputting
Fernet(fernet_key.encode())

print(f'SECRET_KEY={secret_key}')
print(f'FIELD_ENCRYPTION_KEY={fernet_key}')
print(f'POSTGRES_PASSWORD={pg_pass}')
print(f'REDIS_PASSWORD={redis_pass}')
print(f'GATEWAY_SECRET={gateway_secret}')
print(f'GITHUB_WEBHOOK_SECRET={webhook_secret}')
print(f'AUTOSCALER_API_TOKEN={autoscaler_token}')
" > "$INSTALL_DIR/.secrets.tmp" 2>/dev/null; then
        source "$INSTALL_DIR/.secrets.tmp"
        rm -f "$INSTALL_DIR/.secrets.tmp"
        SECRETS_GENERATED=true
        echo -e "${GREEN}  [OK] Secrets generated (Fernet key validated)${NC}"
    fi

    if [ "$SECRETS_GENERATED" != "true" ]; then
        echo -e "${RED}  [ERR] CRITICAL: Cannot generate valid Fernet encryption key.${NC}"
        echo -e "${RED}    Install Python 3 and the 'cryptography' package, then re-run.${NC}"
        echo -e "${RED}    pip3 install cryptography${NC}"
        exit 1
    fi

    # Create .env
cat <<EOF > "$INSTALL_DIR/.env"
# SMSLY Hosting Configuration  -  Generated $(date -Iseconds)
ENVIRONMENT=production
DEBUG=False
PRODUCTION_SERVICES=${PRODUCTION_SERVICES:-$DEFAULT_PRODUCTION_SERVICES}
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$FIELD_ENCRYPTION_KEY
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_USER=smsly_admin
POSTGRES_DB=smsly_hosting
DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@pgbouncer:5432/smsly_hosting

REDIS_PASSWORD=$REDIS_PASSWORD
REDIS_URL=redis://:$REDIS_PASSWORD@redis:6379/0
CELERY_BROKER_URL=redis://:$REDIS_PASSWORD@redis:6379/0

DOMAIN=$DOMAIN
ACME_EMAIL=${ACME_EMAIL:-}
USE_SSL=$USE_SSL

# Inter-service HMAC authentication secret
GATEWAY_SECRET=$GATEWAY_SECRET

# GitHub webhook signature verification
GITHUB_WEBHOOK_SECRET=$GITHUB_WEBHOOK_SECRET

# Security
ALLOWED_HOSTS=$DOMAIN,$PUBLIC_IP,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://$PUBLIC_IP:8090,https://$DOMAIN,http://$DOMAIN,http://localhost:8090,http://$PUBLIC_IP
CORS_ALLOWED_ORIGINS=http://$PUBLIC_IP:8090,https://$DOMAIN,http://$DOMAIN,http://$PUBLIC_IP

# Docker networking
# Ensure addon containers and deployed app containers share the same network for connectivity.
DOCKER_NETWORK=smsly-net

# Wildcard subdomain SSL (Cloudflare DNS challenge)
WILDCARD_SUBDOMAINS=$WILDCARD_SUBDOMAINS
CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN:-}
CADDY_CONFIG_DIR=/caddy-config
PUBLIC_IP=$PUBLIC_IP

# Autoscaler API authentication (shared with smsly-autoscaler.service)
AUTOSCALER_API_TOKEN=$AUTOSCALER_API_TOKEN
EOF

    chmod 600 "$INSTALL_DIR/.env"
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}  x Generated .env failed validation. Aborting install.${NC}"
        exit 1
    fi

    echo -e "${GREEN}  [OK] Configuration saved to .env (chmod 600)${NC}"
fi

# -----------------------------------------------------------------------------
# 4. Deployment
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[4/9] Deploying Container Stack...${NC}"

# Ensure networks exist
docker network create smsly-net 2>/dev/null || true
docker network create smsly-proxy 2>/dev/null || true

# --- BLINDSPOT FIX: Ensure entrypoint.sh has execute permissions ------------
# Windows git can strip +x bits. Fix before building.
#
# NOTE: backend/Dockerfile already runs `chmod +x entrypoint.sh` inside the image.
# Avoid mutating the git working tree on the host (file mode flips can block `git pull`).
#

# Both IP and SSL modes use the same compose stack.
# Caddy (step 7) handles public-facing HTTP/HTTPS termination.
# Traefik is NOT used  -  Caddy natively handles Let's Encrypt SSL.
# Ensure bind-mounted config paths exist before `docker compose up`.
mkdir -p "$INSTALL_DIR/caddy-config"
chmod 777 "$INSTALL_DIR/caddy-config"
echo -e "${BLUE}  -> Starting App Stack...${NC}"
docker compose -f "$COMPOSE_FILE" -f docker-compose.socket-proxy.yml up -d --build --force-recreate --remove-orphans

# -----------------------------------------------------------------------------
# 5. Database Setup
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[5/9] Initializing Database...${NC}"

echo -e "${BLUE}  -> Waiting for Database...${NC}"
DB_READY=false
for i in $(seq 1 24); do
    if docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U smsly_admin >/dev/null 2>&1; then
        echo -e "${GREEN}  [OK] Database is ready (attempt $i).${NC}"
        DB_READY=true
        break
    fi
    printf "."
    sleep 5
done
echo ""

if [ "$DB_READY" != "true" ]; then
    echo -e "${RED}  [ERR] Database failed to become ready after 2 minutes.${NC}"
    echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs db${NC}"
    exit 1
fi

# --- Sync DB password to match .env (handles volume from previous install) --
# The DB volume persists with the password from FIRST init.
# Always reset the password inside PostgreSQL to match the current .env.
source "$INSTALL_DIR/.env" 2>/dev/null || true
echo -e "${BLUE}  -> Syncing database password...${NC}"

# Try local trust auth first (Docker default), then try with PGPASSWORD
if docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U postgres -c "ALTER USER smsly_admin WITH PASSWORD '${POSTGRES_PASSWORD}';" \
    >/dev/null 2>&1; then
    echo -e "${GREEN}  [OK] Database password synced${NC}"
elif docker compose -f "$COMPOSE_FILE" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
    psql -U smsly_admin -d smsly_hosting -c "SELECT 1;" >/dev/null 2>&1; then
    echo -e "${GREEN}  [OK] Database password already matches${NC}"
else
    echo -e "${YELLOW}  [WARN] Password mismatch  -  resetting via postgres superuser...${NC}"
    # Last resort: the Docker postgres container always accepts local postgres user
    docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U postgres -c "ALTER USER smsly_admin WITH PASSWORD '${POSTGRES_PASSWORD}';" \
        2>&1 || echo -e "${RED}  [ERR] Could not sync password. Check pg_hba.conf${NC}"
fi

# --- Restart backend so it picks up the correct DB credentials --------------
echo -e "${BLUE}  -> Restarting backend with synced credentials...${NC}"
docker compose -f "$COMPOSE_FILE" restart backend >/dev/null 2>&1
sleep 5

echo -e "${BLUE}  -> Running Migrations...${NC}"
# Note: Do NOT run makemigrations  -  migrations are committed in the repo.
# Running makemigrations generates files inside the container that conflict on redeploy.
MIGRATE_OK=false
for attempt in 1 2 3; do
    if docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py migrate --noinput 2>&1; then
        MIGRATE_OK=true
        break
    fi
    WAIT=$((attempt * 10))
    echo -e "${YELLOW}  [WARN] Migration attempt $attempt/3 failed  -  retrying in ${WAIT}s...${NC}"
    docker compose -f "$COMPOSE_FILE" restart backend >/dev/null 2>&1
    sleep "$WAIT"
done

if [ "$MIGRATE_OK" != "true" ]; then
    echo -e "${RED}  [ERR] Migrations failed after 3 attempts.${NC}"
    echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs backend${NC}"
    exit 1
fi

echo -e "${BLUE}  -> Collecting Static Files...${NC}"
# Fix volume ownership  -  Docker creates named volumes as root
docker compose -f "$COMPOSE_FILE" exec -T --user root backend chown -R 1000:1000 /app/staticfiles /app/media /app/backups 2>/dev/null || true
docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py collectstatic --noinput

# -----------------------------------------------------------------------------
# 6. Admin User (IDEMPOTENT  -  skips if admin already exists)
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[6/9] Creating Admin User...${NC}"
ADMIN_EXISTS=$(echo "from django.contrib.auth import get_user_model; User = get_user_model(); print('1' if User.objects.filter(username='admin').exists() else '0')" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1)

if [ "${ADMIN_EXISTS:-0}" = "1" ]; then
    echo -e "${GREEN}  [OK] Admin user already exists  -  skipping${NC}"
    if [ -f "$CREDENTIALS_FILE" ]; then
        echo -e "${GREEN}  [OK] Credentials file exists  -  leaving unchanged${NC}"
    else
        # Best effort: don't overwrite an unknown existing password.
        cat > "$CREDENTIALS_FILE" <<CREDS
# SMSLY Hosting Admin Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE
Username: admin
Password: <existing  -  not changed by installer>
CREDS
        chmod 600 "$CREDENTIALS_FILE"
    fi
else
    # Production hardening: never ship with a default admin password.
    # Use a shell-safe hex password (avoids quoting issues in manage.py shell).
    ADMIN_PASS="$(gen_hex_secret 16)"
    echo "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.create_superuser('admin', 'admin@smsly.cloud', '$ADMIN_PASS'); print('CREATED')" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1 >/dev/null
    echo -e "${GREEN}  [OK] Admin user created${NC}"

    # --- Save credentials to secure file (NOT echoed to terminal) ---------------
    cat > "$CREDENTIALS_FILE" <<CREDS
# SMSLY Hosting Admin Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE
Username: admin
Password: $ADMIN_PASS
CREDS
    chmod 600 "$CREDENTIALS_FILE"
fi

# -----------------------------------------------------------------------------
# 6b. Ensure Local Cloud Provider exists (required for deployments)
# -----------------------------------------------------------------------------
echo -e "${BLUE}  -> Ensuring Local Docker cloud provider exists...${NC}"
echo "
from apps.cloud.models import CloudProvider
cp, created = CloudProvider.objects.get_or_create(
    provider_type='LOCAL',
    defaults={'name': 'Local Docker', 'is_active': True}
)
if not created and not cp.is_active:
    cp.is_active = True
    cp.save()
print('CREATED' if created else 'EXISTS')
" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1 >/dev/null
echo -e "${GREEN}  [OK] Local Docker cloud provider ready${NC}"

# -----------------------------------------------------------------------------
# 7. Caddy Reverse Proxy (Public Access)
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[7/9] Setting up Caddy Reverse Proxy...${NC}"

# --- Build Caddy with Cloudflare DNS plugin -----------------------------------
# Always build custom Caddy with Cloudflare DNS support, even in IP mode.
# This ensures users can enable SSL + wildcard from the web UI later without SSH.
if caddy list-modules 2>/dev/null | grep -q 'dns.providers.cloudflare'; then
    echo -e "${GREEN}  [OK] Caddy already has cloudflare DNS module${NC}"
elif command -v caddy &> /dev/null; then
    echo -e "${BLUE}  -> Caddy found but missing Cloudflare DNS plugin  -  rebuilding...${NC}"
    _BUILD_CADDY=true
else
    echo -e "${BLUE}  -> Installing Caddy with Cloudflare DNS plugin...${NC}"
    _BUILD_CADDY=true
fi

if [ "${_BUILD_CADDY:-}" = "true" ]; then
    if ! command -v xcaddy &> /dev/null; then
        # xcaddy needs Go 1.21+. Ubuntu apt repos ship Go 1.18 which is
        # too old (go.mod 'toolchain' directive is unsupported). Use snap
        # or direct binary download to get a compatible version.
        _GO_OK=false
        if command -v go &> /dev/null; then
            _GO_VER=$(go version | grep -oP 'go1\.(\d+)' | grep -oP '\d+$')
            [ "${_GO_VER:-0}" -ge 21 ] && _GO_OK=true
        fi
        if [ "$_GO_OK" != "true" ]; then
            echo -e "${BLUE}  -> Installing Go 1.22 (xcaddy requires Go 1.21+)...${NC}"
            GO_TAR="go1.22.10.linux-amd64.tar.gz"
            curl -fsSL "https://go.dev/dl/$GO_TAR" -o "/tmp/$GO_TAR"
            rm -rf /usr/local/go
            tar -C /usr/local -xzf "/tmp/$GO_TAR"
            rm -f "/tmp/$GO_TAR"
            export PATH="/usr/local/go/bin:$PATH"
            echo -e "${GREEN}  [OK] Go $(go version | awk '{print $3}') installed${NC}"
        fi
        export GOPATH="${GOPATH:-/root/go}"
        export PATH="$PATH:$GOPATH/bin"
        go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
    fi

    # Build custom Caddy with Cloudflare DNS
    CADDY_TMP=$(mktemp -d)
    cd "$CADDY_TMP"
    if xcaddy build --with github.com/caddy-dns/cloudflare 2>&1 | tail -5; then
        # Replace system Caddy
        systemctl stop caddy 2>/dev/null || true
        mv ./caddy /usr/bin/caddy
        chmod +x /usr/bin/caddy
        echo -e "${GREEN}  [OK] Custom Caddy built with Cloudflare DNS plugin${NC}"
    else
        echo -e "${YELLOW}  [WARN] Custom Caddy build failed  -  trying pre-built download...${NC}"
        # Fallback 1: Download pre-built Caddy with Cloudflare DNS from Caddy's download API
        if curl -fsSL -o /usr/bin/caddy \
            "https://caddyserver.com/api/download?os=linux&arch=amd64&p=github.com/caddy-dns/cloudflare" 2>/dev/null; then
            chmod +x /usr/bin/caddy
            echo -e "${GREEN}  [OK] Pre-built Caddy with Cloudflare DNS downloaded${NC}"
        elif ! command -v caddy &> /dev/null; then
            # Fallback 2: Install stock Caddy from apt (no wildcard SSL, but basic HTTPS works)
            echo -e "${YELLOW}  [WARN] Download also failed  -  installing stock Caddy (no wildcard SSL)...${NC}"
            apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null 2>&1
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
            apt-get update >/dev/null 2>&1
            apt-get install -y caddy >/dev/null 2>&1
        fi
    fi
    cd "$INSTALL_DIR"
    rm -rf "$CADDY_TMP"
fi

# --- Configure Caddyfile ------------------------------------------------------
echo -e "${BLUE}  -> Configuring Caddyfile...${NC}"
mkdir -p /var/log/caddy
touch /var/log/caddy/access.log
if id caddy >/dev/null 2>&1; then
    chown -R caddy:caddy /var/log/caddy
fi
chmod 755 /var/log/caddy
chmod 640 /var/log/caddy/access.log

CADDY_OVERRIDE_DIR="/etc/systemd/system/caddy.service.d"
CADDY_OVERRIDE_FILE="$CADDY_OVERRIDE_DIR/override.conf"

if [ "$USE_SSL" = "true" ] && [ -n "$DOMAIN" ] && [ "$DOMAIN" != "$PUBLIC_IP" ]; then
    # Ensure token is sourced from .env if present (idempotent runs)
    if [ -z "$CLOUDFLARE_API_TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
        CLOUDFLARE_API_TOKEN="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    fi

    if [ "$WILDCARD_SUBDOMAINS" = "true" ] && [ -n "$CLOUDFLARE_API_TOKEN" ]; then
        # --- Full wildcard mode: domain + *.domain with Cloudflare DNS ----
        cat > /etc/caddy/Caddyfile <<CADDYEOF
# CloudNeuron Reverse Proxy  -  Auto-generated
# Domain: $DOMAIN -> HTTPS (auto Let's Encrypt)
# Wildcard: *.$DOMAIN -> HTTPS (Cloudflare DNS challenge)

$DOMAIN {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

*.$DOMAIN {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
    handle {
        rewrite * /notice
        reverse_proxy localhost:8090
    }
}

:80 {
    handle {
        rewrite * /notice
        reverse_proxy localhost:8090
    }
}
CADDYEOF

        # Set Cloudflare token in systemd environment
        mkdir -p "$CADDY_OVERRIDE_DIR"
        cat > "$CADDY_OVERRIDE_FILE" <<ENVEOF
[Service]
ExecStart=
ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile
Environment="CLOUDFLARE_API_TOKEN=$CLOUDFLARE_API_TOKEN"
ENVEOF
        chmod 600 "$CADDY_OVERRIDE_FILE"
        systemctl daemon-reload

        echo -e "${GREEN}  [OK] Caddy configured: HTTPS ($DOMAIN) + Wildcard (*.$DOMAIN) + HTTP fallback -> 8090${NC}"
    else
        # --- Standard SSL (no wildcard) ----------------------------------
        cat > /etc/caddy/Caddyfile <<CADDYEOF
# CloudNeuron Reverse Proxy  -  Auto-generated
# Domain: $DOMAIN -> HTTPS (auto Let's Encrypt)

$DOMAIN {
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

:80 {
    handle {
        rewrite * /notice
        reverse_proxy localhost:8090
    }
}
CADDYEOF
        if [ -f "$CADDY_OVERRIDE_FILE" ]; then
            rm -f "$CADDY_OVERRIDE_FILE"
            rmdir "$CADDY_OVERRIDE_DIR" 2>/dev/null || true
            systemctl daemon-reload
        fi
        echo -e "${GREEN}  [OK] Caddy configured: HTTPS ($DOMAIN) + HTTP (:80 fallback) -> 8090${NC}"
    fi
else
    cat > /etc/caddy/Caddyfile <<CADDYEOF
# CloudNeuron Reverse Proxy  -  Auto-generated
:80 {
    reverse_proxy localhost:8090
}
CADDYEOF
    if [ -f "$CADDY_OVERRIDE_FILE" ]; then
        rm -f "$CADDY_OVERRIDE_FILE"
        rmdir "$CADDY_OVERRIDE_DIR" 2>/dev/null || true
        systemctl daemon-reload
    fi
    echo -e "${GREEN}  [OK] Caddy configured for HTTP: :80 -> 8090${NC}"
fi

# --- Create caddy-config volume directory for Settings UI writes --------------
ensure_caddy_config_permissions

# --- Install caddy-watcher service (picks up UI-driven Caddyfile changes) -----
if [ -f "$INSTALL_DIR/scripts/caddy-reload.sh" ]; then
    chmod +x "$INSTALL_DIR/scripts/caddy-reload.sh"
    cat > /etc/systemd/system/caddy-watcher.service <<WATCHEREOF
[Unit]
Description=Caddy Config Watcher (SMSLY)
After=caddy.service

[Service]
Type=simple
ExecStart=$INSTALL_DIR/scripts/caddy-reload.sh /opt/smsly-hosting/caddy-config
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
WATCHEREOF
    systemctl daemon-reload
    systemctl enable caddy-watcher >/dev/null 2>&1
    systemctl restart caddy-watcher
    echo -e "${GREEN}  [OK] Caddy watcher service installed and running${NC}"
fi

# Kill non-Caddy/non-Docker processes holding port 80/443 before Caddy binds
for port in 80 443; do
    PID=$(lsof -ti :$port 2>/dev/null || ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
    if [ -n "$PID" ] && [ "$PID" != "0" ]; then
        PNAME=$(ps -p "$PID" -o comm= 2>/dev/null || echo "unknown")
        # Don't kill Caddy or Docker processes
        if [[ "$PNAME" != "caddy" ]] && [[ "$PNAME" != "docker"* ]]; then
            echo -e "${YELLOW}  -> Killing $PNAME (PID: $PID) holding port $port${NC}"
            kill -9 $PID 2>/dev/null || true
            sleep 1
        fi
    fi
done

systemctl restart caddy
systemctl enable caddy >/dev/null 2>&1

# Verify Caddy is running
sleep 2
if systemctl is-active --quiet caddy; then
    echo -e "${GREEN}  [OK] Caddy reverse proxy active${NC}"
else
    echo -e "${RED}  [ERR] Caddy failed to start. Check: journalctl -u caddy --no-pager -n 20${NC}"
    exit 1
fi

# -----------------------------------------------------------------------------
# 8. System Memory Hardening (Prevents OOM kills)
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[8/9] Hardening System Memory...${NC}"

# --- Swap: Ensure at least 2GB total swap ------------------------------------
CURRENT_SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')
if [ "$CURRENT_SWAP_MB" -lt 2000 ]; then
    NEEDED_MB=$((2048 - CURRENT_SWAP_MB))
    echo -e "${BLUE}  -> Current swap: ${CURRENT_SWAP_MB}MB. Adding ${NEEDED_MB}MB...${NC}"
    SWAPFILE="/swapfile-smsly"
    if [ ! -f "$SWAPFILE" ]; then
        fallocate -l ${NEEDED_MB}M "$SWAPFILE" 2>/dev/null || dd if=/dev/zero of="$SWAPFILE" bs=1M count=$NEEDED_MB status=none
        chmod 600 "$SWAPFILE"
        mkswap "$SWAPFILE" >/dev/null 2>&1
        swapon "$SWAPFILE" 2>/dev/null || true
        # Make permanent (idempotent)
        if ! grep -q "$SWAPFILE" /etc/fstab 2>/dev/null; then
            echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
        fi
        echo -e "${GREEN}  [OK] Swap file created and activated (${NEEDED_MB}MB)${NC}"
    else
        # Swap file exists but may not be active
        swapon "$SWAPFILE" 2>/dev/null || true
        echo -e "${GREEN}  [OK] Existing swap file activated${NC}"
    fi
else
    echo -e "${GREEN}  [OK] Swap already sufficient (${CURRENT_SWAP_MB}MB)${NC}"
fi

# --- Sysctl tuning (idempotent) ----------------------------------------------
SYSCTL_UPDATED=false

ensure_sysctl() {
    local key="$1" value="$2" desc="$3"
    CURRENT=$(sysctl -n "$key" 2>/dev/null || echo "")
    if [ "$CURRENT" != "$value" ]; then
        sysctl -w "$key=$value" >/dev/null 2>&1 || true
        # Make permanent (idempotent)
        if grep -q "^$key" /etc/sysctl.conf 2>/dev/null; then
            sed -i "s|^$key.*|$key = $value|" /etc/sysctl.conf
        else
            echo "# $desc" >> /etc/sysctl.conf
            echo "$key = $value" >> /etc/sysctl.conf
        fi
        SYSCTL_UPDATED=true
        echo -e "${GREEN}  [OK] $key = $value ($desc)${NC}"
    fi
}

ensure_sysctl "vm.overcommit_memory" "1" "Redis background save fix"
ensure_sysctl "vm.swappiness" "10" "Prefer RAM over swap"
ensure_sysctl "net.core.somaxconn" "511" "Redis connection backlog"

if [ "$SYSCTL_UPDATED" = "false" ]; then
    echo -e "${GREEN}  [OK] Sysctl settings already optimal${NC}"
fi

# --- OOM Protection for critical containers ----------------------------------
echo -e "${BLUE}  -> Setting OOM protection for critical containers...${NC}"
for CONTAINER in smsly-hosting-nginx-1 smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgbouncer-1; do
    CPID=$(docker inspect --format '{{.State.Pid}}' "$CONTAINER" 2>/dev/null || echo "")
    if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
        echo -500 > "/proc/$CPID/oom_score_adj" 2>/dev/null || true
    fi
done
echo -e "${GREEN}  [OK] OOM protection set (nginx, backend, db, pgbouncer)${NC}"

echo -e "${GREEN}  [OK] System memory hardening complete${NC}"

# -----------------------------------------------------------------------------
# 9. Verification
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[9/9] Verifying Deployment...${NC}"
VERIFY_PASS_COUNT=0
VERIFY_TOTAL=5
sleep 5

# --- Check 1: Verify nginx loaded custom config (not default) --------------
echo -e "${BLUE}  -> [1/5] Verifying nginx configuration...${NC}"
NGINX_CONFIG_CHECK=$(docker exec smsly-hosting-nginx-1 head -1 /etc/nginx/nginx.conf 2>/dev/null || echo "FAIL")
if echo "$NGINX_CONFIG_CHECK" | grep -q "events"; then
    echo -e "${GREEN}  [OK] Nginx config verified (custom proxy config loaded)${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  [WARN] Nginx may have default config  -  force-recreating...${NC}"
    docker compose -f "$COMPOSE_FILE" -f docker-compose.socket-proxy.yml up -d --force-recreate --no-deps nginx
    docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true
    sleep 3
    NGINX_CONFIG_CHECK=$(docker exec smsly-hosting-nginx-1 head -1 /etc/nginx/nginx.conf 2>/dev/null || echo "FAIL")
    if echo "$NGINX_CONFIG_CHECK" | grep -q "events"; then
        echo -e "${GREEN}  [OK] Nginx config fixed after force-recreate${NC}"
        VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    else
        echo -e "${RED}  [ERR] Nginx config still incorrect. Manual fix needed.${NC}"
    fi
fi

# --- Check 2: Health check -------------------------------------------------
echo -e "${BLUE}  -> [2/5] Running health check...${NC}"
HEALTH_OK=false
for attempt in 1 2 3 4 5; do
    if curl -sfL http://127.0.0.1/health >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    elif curl -sfL http://127.0.0.1/health >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    fi
    echo -e "${YELLOW}  -> Health check attempt $attempt/5  -  waiting...${NC}"
    if [ "$attempt" -eq 1 ]; then
        docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true
    fi
    sleep 5
done

if [ "$HEALTH_OK" = "true" ]; then
    echo -e "${GREEN}  [OK] Health Check Passed!${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  [WARN] Health check did not respond  -  services may still be starting.${NC}"
fi

# --- Check 3: All containers running --------------------------------------
echo -e "${BLUE}  -> [3/5] Checking container status...${NC}"
RUNNING_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --status running -q 2>/dev/null | wc -l)
TOTAL_COUNT=$(docker compose -f "$COMPOSE_FILE" ps -q 2>/dev/null | wc -l)
if [ "$RUNNING_COUNT" -eq "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt 0 ]; then
    echo -e "${GREEN}  [OK] All $TOTAL_COUNT containers running${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  [ERR] Only $RUNNING_COUNT/$TOTAL_COUNT containers running${NC}"
fi

# --- Check 4: Swap is sufficient ------------------------------------------
echo -e "${BLUE}  -> [4/5] Checking swap...${NC}"
SWAP_TOTAL=$(free -m | awk '/^Swap:/{print $2}')
if [ "$SWAP_TOTAL" -ge 1500 ]; then
    echo -e "${GREEN}  [OK] Swap sufficient (${SWAP_TOTAL}MB)${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  [WARN] Swap low (${SWAP_TOTAL}MB)  -  recommend 2GB+${NC}"
fi

# --- Check 5: Caddy running -----------------------------------------------
echo -e "${BLUE}  -> [5/5] Checking Caddy...${NC}"
if systemctl is-active --quiet caddy 2>/dev/null; then
    echo -e "${GREEN}  [OK] Caddy reverse proxy active${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  [ERR] Caddy is not running${NC}"
fi

# Show container status
echo -e "\n${BLUE}Container Status:${NC}"
docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
    docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true

echo -e "\n${BLUE}Verification Score: $VERIFY_PASS_COUNT/$VERIFY_TOTAL${NC}"

# --- Install Autoscaler as systemd service ----------------------------------
echo -e "${BLUE}  -> Installing smsly-autoscaler systemd service...${NC}"
cp "$INSTALL_DIR/smsly-autoscaler.py" /opt/smsly/autoscaler.py 2>/dev/null || {
    mkdir -p /opt/smsly
    cp "$INSTALL_DIR/smsly-autoscaler.py" /opt/smsly/autoscaler.py
}
chmod +x /opt/smsly/autoscaler.py

# Source .env for the token
AUTOSCALER_API_TOKEN="$(env_get_value "$INSTALL_DIR/.env" "AUTOSCALER_API_TOKEN")"

cat <<SVCEOF > /etc/systemd/system/smsly-autoscaler.service
[Unit]
Description=SMSLY VPS Autoscaler  -  Cross-Service Resource Manager
After=docker.service
Requires=docker.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/smsly/autoscaler.py
Restart=always
RestartSec=10
Environment=AUTOSCALER_API_TOKEN=${AUTOSCALER_API_TOKEN}
Environment=AUTOSCALER_API_BIND=127.0.0.1
Environment=AUTOSCALER_API_PORT=9876
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable smsly-autoscaler 2>/dev/null || true
systemctl restart smsly-autoscaler 2>/dev/null || true
echo -e "${GREEN}  [OK] smsly-autoscaler service installed and started${NC}"

# --- Remove rollback trap (installation succeeded) -------------------------
trap - EXIT

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   [OK] INSTALLATION SUCCESSFUL!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"

if [ "$USE_SSL" = "true" ]; then
    echo -e "   URL:         https://$DOMAIN"
else
    echo -e "   URL:         http://$PUBLIC_IP"
fi
echo -e "   Admin:       /admin"
echo -e "   Credentials: $CREDENTIALS_FILE"
echo -e "   Install Log: $LOG_FILE"
echo -e "   Location:    $INSTALL_DIR"
echo -e "   Memory:      $(free -m | awk '/^Mem:/{print $7}')MB available"
echo -e "   Swap:        $(free -m | awk '/^Swap:/{print $2}')MB total"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  View credentials:   cat $CREDENTIALS_FILE${NC}"
echo -e "${YELLOW}  View logs:          cat $LOG_FILE${NC}"
echo -e "${YELLOW}  Update frontend:    sudo bash install.sh --update-frontend${NC}"
echo -e "${YELLOW}  Update backend:     sudo bash install.sh --update-backend${NC}"
echo -e "${YELLOW}  Full update:        sudo bash install.sh --update${NC}"
echo -e "${YELLOW}  Runtime recovery:   sudo bash install.sh --recover${NC}"
echo -e "${YELLOW}  Debug snapshot:     sudo bash install.sh --debug${NC}"
echo -e "${YELLOW}  Wipe install:       sudo bash install.sh --wipe${NC}"

# --- Conditional Auto-Reboot (only if ALL checks passed) --------------------
if [ "$VERIFY_PASS_COUNT" -eq "$VERIFY_TOTAL" ]; then
    echo -e "\n${GREEN}  [OK] All $VERIFY_TOTAL/$VERIFY_TOTAL verification checks passed.${NC}"
    if [ -e /dev/tty ] && [ -z "${SKIP_REBOOT:-}" ]; then
        echo -e "${YELLOW}  System will reboot in 30 seconds to apply sysctl changes.${NC}"
        echo -e "${YELLOW}  Press Ctrl+C to cancel, or wait...${NC}"
        for i in $(seq 30 -1 1); do
            printf "\r${YELLOW}  Rebooting in %2d seconds... ${NC}" "$i"
            sleep 1
        done
        echo -e "\n${BLUE}  -> Rebooting now...${NC}"
        reboot
    else
        echo -e "${YELLOW}  Non-interactive mode  -  skipping auto-reboot.${NC}"
        echo -e "${YELLOW}  Run 'sudo reboot' manually to apply sysctl changes.${NC}"
    fi
else
    echo -e "\n${RED}  [WARN] Only $VERIFY_PASS_COUNT/$VERIFY_TOTAL checks passed  -  skipping auto-reboot.${NC}"
    echo -e "${YELLOW}  Fix the failed checks above, then run: sudo reboot${NC}"
    if [ "${SMSLY_STRICT_VERIFY:-0}" = "1" ]; then
        echo -e "${RED}  [ERR] Strict verification is enabled; failing installation.${NC}"
        exit 1
    fi
fi
