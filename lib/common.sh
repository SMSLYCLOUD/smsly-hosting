# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"
export NEEDRESTART_MODE="${NEEDRESTART_MODE:-a}"
# Validate and safely detect a usable IPv4 address for installer defaults.
is_valid_ipv4() {
    local ip="$1"
    local octet

    [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
    IFS='.' read -r o1 o2 o3 o4 <<< "$ip"
    for octet in "$o1" "$o2" "$o3" "$o4"; do
        [[ "$octet" =~ ^[0-9]+$ ]] || return 1
        [ "$octet" -ge 0 ] && [ "$octet" -le 255 ] || return 1
    done
    return 0
}

detect_public_ip() {
    local candidate=""
    local endpoint=""
    local endpoints=(
        "https://api.ipify.org"
        "https://ifconfig.me/ip"
        "https://ipv4.icanhazip.com"
    )

    for endpoint in "${endpoints[@]}"; do
        candidate="$(curl -4 -fsS -m 5 "$endpoint" 2>/dev/null | tr -d '\r\n' || true)"
        if is_valid_ipv4 "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done

    candidate="$(hostname -I 2>/dev/null | awk '{print $1}' | tr -d '\r\n' || true)"
    if is_valid_ipv4 "$candidate"; then
        echo "$candidate"
        return 0
    fi

    echo "127.0.0.1"
    return 0
}
ensure_local_ignores() {
    local target_dir="${INSTALL_DIR:-/opt/smsly-hosting}"
    local gitignore_path="${target_dir}/.gitignore"
    if [ -d "$target_dir" ]; then
        if [ ! -f "$gitignore_path" ]; then
            touch "$gitignore_path"
        fi
        local needs_update=false
        if ! grep -q "^builds/" "$gitignore_path"; then
            echo "" >> "$gitignore_path"
            echo "builds/" >> "$gitignore_path"
            needs_update=true
        fi
        if ! grep -q "^caddy-config/" "$gitignore_path"; then
            echo "caddy-config/" >> "$gitignore_path"
            needs_update=true
        fi
        if [ "$needs_update" = "true" ]; then
            echo -e "${BLUE}  → Added builds/ and caddy-config/ to local .gitignore to prevent Git stash hangs${NC}"
        fi
    fi
}
LOG_FILE="/var/log/smsly-install.log"
INSTALL_DIR="/opt/smsly-hosting"
CREDENTIALS_FILE="$INSTALL_DIR/.credentials"
COMPOSE_FILE="docker-compose.prod.yml"
LOCK_FILE="/tmp/smsly-install.lock"
# The production compose file already includes socket-proxy and traefik.
# Do not layer docker-compose.socket-proxy.yml on top of it or Docker Compose
# will reject the config due to duplicate services.
ROLLBACK_NEEDED=false
CADDY_LAST_GOOD="$INSTALL_DIR/caddy-config/Caddyfile.smsly-last-good"

acquire_install_lock() {
    if command -v flock >/dev/null 2>&1; then
        exec 9<>"$LOCK_FILE"
        if ! flock -n 9; then
            local pid
            pid="$(cat "$LOCK_FILE" 2>/dev/null || true)"
            echo -e "${RED}ERROR: Another installer instance${pid:+ (PID $pid)} is already running.${NC}"
            echo -e "If you are sure no other instance is running, remove $LOCK_FILE and try again."
            exit 1
        fi
        : > "$LOCK_FILE"
        echo "$$" > "$LOCK_FILE"
    else
        if [ -f "$LOCK_FILE" ]; then
            local pid
            pid="$(cat "$LOCK_FILE" 2>/dev/null || true)"
            if [ "$pid" != "$$" ] && kill -0 "$pid" 2>/dev/null; then
                echo -e "${RED}ERROR: Another installer instance (PID $pid) is already running.${NC}"
                echo -e "If you are sure no other instance is running, remove $LOCK_FILE and try again."
                exit 1
            fi
        fi
        echo "$$" > "$LOCK_FILE"
    fi
}

release_install_lock() {
    if command -v flock >/dev/null 2>&1; then
        flock -u 9 2>/dev/null || true
        exec 9>&- 2>/dev/null || true
    fi
    rm -f "$LOCK_FILE" 2>/dev/null || true
}

get_migration_database_alias() {
    local migrate_db
    local direct_url
    direct_url="$(env_get_value "${INSTALL_DIR:-.}/.env" "DIRECT_DATABASE_URL" 2>/dev/null || true)"
    if [ -z "$direct_url" ]; then
        direct_url="postgresql://${POSTGRES_USER:-smsly_admin}:${POSTGRES_PASSWORD:-}@${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-smsly_hosting}"
    fi
    migrate_db="$(
        docker compose -f "$COMPOSE_FILE" run --no-deps -T \
            -e SMSLY_DISABLE_STARTUP_TASKS=true \
            -e SMSLY_MIGRATION_MODE=true \
            -e DIRECT_DATABASE_URL="$direct_url" \
            backend python manage.py shell -c \
            "from django.conf import settings; print('direct' if 'direct' in settings.DATABASES else ('session' if 'session' in settings.DATABASES else 'default'))" \
            2>/dev/null | tail -n 1 | tr -d '\r'
    )"
    docker compose -f "$COMPOSE_FILE" rm -f backend-run 2>/dev/null || true

    case "$migrate_db" in
        direct|session|default) printf '%s\n' "$migrate_db" ;;
        *) printf '%s\n' "default" ;;
    esac
}

diagnose_migration_locks() {
    local env_file="${INSTALL_DIR:-.}/.env"
    [ -f "$env_file" ] && source "$env_file" 2>/dev/null || true

    echo -e "${YELLOW}  -> PostgreSQL activity snapshot (lock diagnosis):${NC}"
    docker compose -f "$COMPOSE_FILE" exec -T \
        -e PGPASSWORD="${POSTGRES_PASSWORD:-}" \
        db psql \
            -U "${POSTGRES_USER:-smsly_admin}" \
            -d "${POSTGRES_DB:-smsly_hosting}" \
            -v ON_ERROR_STOP=1 \
            -P pager=off \
            -c "SELECT pid, usename, application_name, state, wait_event_type, wait_event, now() - COALESCE(xact_start, query_start) AS age, left(regexp_replace(query, '\s+', ' ', 'g'), 180) AS query FROM pg_stat_activity WHERE datname = current_database() ORDER BY COALESCE(xact_start, query_start) NULLS LAST LIMIT 20;" \
        2>/dev/null || echo -e "${YELLOW}  -> Could not read pg_stat_activity.${NC}"
}

run_backend_migrations() {
    local user_args=()
    if [ "${1:-}" = "--root" ]; then
        user_args=(--user root)
    fi

    local migrate_db timeout_seconds rc
    migrate_db="$(get_migration_database_alias)"
    timeout_seconds="${MIGRATION_TIMEOUT_SECONDS:-900}"
    echo -e "${BLUE}  -> Migration database: ${migrate_db}${NC}"
    local direct_url
    direct_url="$(env_get_value "${INSTALL_DIR:-.}/.env" "DIRECT_DATABASE_URL" 2>/dev/null || true)"
    if [ -z "$direct_url" ]; then
        direct_url="postgresql://${POSTGRES_USER:-smsly_admin}:${POSTGRES_PASSWORD:-}@${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-smsly_hosting}"
    fi

    # Run migrations inside a one-shot container.
    # Capture the exit code explicitly so set -e stays in effect.
    # NOTE: --rm is omitted intentionally.  Under heavy Docker daemon load
    # (e.g. concurrent image builds), `docker compose run --rm` can hang
    # for minutes waiting for container removal.  Without --rm the container
    # exits immediately and Docker cleans it up asynchronously.
    set +e
    timeout "$((timeout_seconds + 60))" docker compose -f "$COMPOSE_FILE" run --no-deps -T \
        "${user_args[@]}" \
        -e SMSLY_DISABLE_STARTUP_TASKS=true \
        -e SMSLY_MIGRATION_MODE=true \
        -e DIRECT_DATABASE_URL="$direct_url" \
        backend timeout "$timeout_seconds" \
        python manage.py migrate --database="$migrate_db" --noinput
    rc=$?
    set -e

    # Clean up the one-shot migration container (best-effort).
    docker compose -f "$COMPOSE_FILE" rm -f backend-run 2>/dev/null || true

    if [ "$rc" -ne 0 ]; then
        if [ "$rc" -eq 124 ]; then
            echo -e "${RED}  x Migrations timed out after ${timeout_seconds}s.${NC}"
        else
            echo -e "${RED}  x Migrations exited with status ${rc}.${NC}"
        fi
        [ "$MODE_AGENT_LITE" != "true" ] && diagnose_migration_locks
        return "$rc"
    fi

    # Self-healing: fix node agent DB permissions after migrations.
    # Best-effort — wrapped in timeout so a hung Docker daemon can't
    # block the entire update pipeline (which needs to restart backend/celery).
    echo -e "${BLUE}  -> Fixing node agent database permissions...${NC}"
    timeout 60 docker compose -f "$COMPOSE_FILE" run --no-deps -T \
        "${user_args[@]}" \
        -e SMSLY_DISABLE_STARTUP_TASKS=true \
        backend python manage.py fix_node_db_permissions 2>&1 || true
    docker compose -f "$COMPOSE_FILE" rm -f backend-run 2>/dev/null || true

    return 0
}

export_caddy_cloudflare_env() {
    return 0
}

restore_last_good_caddy() {
    return 0
}

reload_caddy_preserving_previous() {
    reload_container_caddy 2>/dev/null || true
    return 0
}

ensure_selfsigned_cert() {
    # Generate a self-signed certificate for the server's public IP address.
    # Caddy's built-in tls internal doesn't support IP SANs (causes
    # ERR_SSL_PROTOCOL_ERROR), so we generate a proper cert with
    # OpenSSL that includes the IP as a Subject Alternative Name.
    local cert_dir="${INSTALL_DIR:-/opt/smsly-hosting}/caddy-config/certs"
    local cert_file="$cert_dir/ip.crt"
    local key_file="$cert_dir/ip.key"
    local public_ip="${PUBLIC_IP:-$(detect_public_ip)}"
    local ssl_config="$cert_dir/openssl.cnf"

    mkdir -p "$cert_dir"
    chmod 700 "$cert_dir" 2>/dev/null || true

    if ! command -v openssl &>/dev/null; then
        echo -e "${YELLOW}  ⚠ openssl not available; skipping self-signed cert generation${NC}"
        return 0
    fi

    # Always regenerate (cheap operation, ensures IP is current)
    echo -e "${BLUE}  → Generating self-signed cert for IP: $public_ip...${NC}"

    # Create a temporary OpenSSL config with the IP SAN
    cat > "$ssl_config" <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = $public_ip

[v3_req]
keyUsage = digitalSignature, keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
IP.1 = $public_ip
EOF

    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "$key_file" \
        -out "$cert_file" \
        -config "$ssl_config" \
        2>/dev/null || {
        echo -e "${YELLOW}  ⚠ Failed to generate self-signed cert (non-fatal)${NC}"
        rm -f "$ssl_config"
        return 0
    }
    rm -f "$ssl_config"

    # SECURITY: private key must be owner-readable only (chmod 600). Caddy
    # reads cert AND key as UID 1000; we chown the key to that user so the
    # Caddy container can open it. Cert stays world-readable (chmod 644) for
    # chain-bundle consumers; key is never world-readable.
    chmod 644 "$cert_file" 2>/dev/null || true
    chmod 600 "$key_file" 2>/dev/null || true
    # Hand ownership to Caddy (UID 1000) when run as root via sudo.
    if [ -n "${SUDO_USER:-}" ]; then
        chown "${SUDO_USER}:${SUDO_USER}" "$key_file" 2>/dev/null || chown 1000:1000 "$key_file" 2>/dev/null || true
    elif [ "$(id -u)" -eq 0 ]; then
        chown 1000:1000 "$key_file" 2>/dev/null || true
    fi
    echo -e "${GREEN}  ✓ Self-signed cert generated for $public_ip${NC}"
}

reload_container_caddy() {
    should_manage_caddy || return 0
    # Reload the Docker container Caddy (the one that handles actual traffic).
    # This is needed because the host Caddy (systemd) may not be running.
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if command -v docker &>/dev/null && docker compose -f "$compose_f" ps -q caddy 2>/dev/null | grep -q .; then
        timeout 20 docker compose -f "$compose_f" exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null || \
            timeout 20 docker compose -f "$compose_f" restart caddy 2>/dev/null || true
    fi
}

sync_active_caddyfile_to_shared() {
    return 0
}

install_caddyfile_atomically() {
    should_manage_caddy || return 0
    local candidate="$1"
    local label="${2:-Caddyfile}"
    local dest="${INSTALL_DIR:-/opt/smsly-hosting}/caddy-config/Caddyfile"

    if [ ! -f "$candidate" ]; then
        echo -e "${YELLOW}  WARN $label candidate missing: $candidate${NC}"
        return 1
    fi

    mkdir -p "$(dirname "$dest")"
    cp "$candidate" "$dest"
    chmod 664 "$dest"

    reload_container_caddy 2>/dev/null || true
    return 0
}

recreate_traefik_preserving_certs() {
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    local acme_src="/var/lib/docker/volumes/smsly-hosting_letsencrypt_data/_data/acme.json"
    local acme_backup=""

    if ! docker compose -f "$compose_f" ps -q traefik 2>/dev/null | grep -q .; then
        echo -e "${YELLOW}  WARN traefik not running; skipping one-time recreate.${NC}"
        return 1
    fi

    echo -e "${BLUE}  → Verifying socket-proxy is healthy (traefik Docker provider depends on it)...${NC}"
    local i=0
    while [ $i -lt 30 ]; do
        if docker inspect --format='{{.State.Health.Status}}' smsly-hosting-socket-proxy-1 2>/dev/null | grep -q healthy; then
            break
        fi
        sleep 2
        i=$((i + 1))
    done
    if [ $i -ge 30 ]; then
        echo -e "${RED}  x socket-proxy not healthy; aborting to avoid 503 on deployed services.${NC}"
        echo -e "${RED}    Fix: docker logs smsly-hosting-socket-proxy-1${NC}"
        return 1
    fi

    echo -e "${BLUE}  → Backing up acme.json...${NC}"
    if [ -f "$acme_src" ]; then
        acme_backup="/tmp/smsly-acme-$(date +%s).json"
        cp "$acme_src" "$acme_backup" && chmod 600 "$acme_backup"
        echo -e "${GREEN}    OK saved to $acme_backup${NC}"
    else
        echo -e "${YELLOW}    WARN no existing acme.json; new container will request fresh certs.${NC}"
    fi

    echo -e "${BLUE}  → Recording pre-recreate router count from Traefik API...${NC}"
    sleep 2
    local pre_routers=0
    if docker exec smsly-hosting-traefik-1 sh -c 'command -v wget >/dev/null 2>&1' 2>/dev/null; then
        pre_routers=$(docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/http/routers 2>/dev/null | grep -o '"name"' | wc -l)
    else
        pre_routers=$(docker exec smsly-hosting-traefik-1 curl -s http://127.0.0.1:8080/api/http/routers 2>/dev/null | grep -o '"name"' | wc -l)
    fi
    echo -e "${BLUE}    pre-recreate routers: $pre_routers${NC}"
    if [ "$pre_routers" -le 1 ]; then
        echo -e "${YELLOW}    WARN only $pre_routers router(s) before recreate (expected route-fallback + deployed services).${NC}"
        echo -e "${YELLOW}          Deployed services may already have stale labels.${NC}"
    fi

    echo -e "${BLUE}  → Recreating traefik (preserves letsencrypt_data volume + acme.json)...${NC}"
    timeout 60 docker compose -f "$compose_f" up -d --force-recreate traefik 2>&1 | sed 's/^/    /'

    echo -e "${BLUE}  → Reconnecting traefik to smsly-proxy network (recreate can drop external nets)...${NC}"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"

    if [ -n "$acme_backup" ] && [ -f "$acme_backup" ]; then
        sleep 3
        if [ -f "$acme_src" ]; then
            cp "$acme_backup" "$acme_src" && chmod 600 "$acme_src"
            echo -e "${GREEN}    OK restored acme.json perms to 0600${NC}"
        fi
        rm -f "$acme_backup"
    fi

    echo -e "${BLUE}  → Waiting for traefik healthcheck...${NC}"
    i=0
    while [ $i -lt 30 ]; do
        if docker inspect --format='{{.State.Health.Status}}' smsly-hosting-traefik-1 2>/dev/null | grep -q healthy; then
            break
        fi
        sleep 2
        i=$((i + 1))
    done
    if [ $i -ge 30 ]; then
        echo -e "${YELLOW}  WARN traefik healthcheck timeout; check 'docker logs smsly-hosting-traefik-1'${NC}"
    fi

    echo -e "${BLUE}  → Waiting for Traefik routing table to repopulate (CRITICAL — prevents 503 on deployed services)...${NC}"
    i=0
    local post_routers=0
    while [ $i -lt 60 ]; do
        if docker exec smsly-hosting-traefik-1 sh -c 'command -v wget >/dev/null 2>&1' 2>/dev/null; then
            post_routers=$(docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/http/routers 2>/dev/null | grep -o '"name"' | wc -l)
        else
            post_routers=$(docker exec smsly-hosting-traefik-1 curl -s http://127.0.0.1:8080/api/http/routers 2>/dev/null | grep -o '"name"' | wc -l)
        fi
        if [ "$post_routers" -ge "$pre_routers" ] && [ "$post_routers" -gt 0 ]; then
            echo -e "${GREEN}    OK post-recreate routers: $post_routers (matches or exceeds pre-recreate)${NC}"

            local eps
            if docker exec smsly-hosting-traefik-1 sh -c 'command -v wget >/dev/null 2>&1' 2>/dev/null; then
                eps=$(docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/entrypoints 2>/dev/null)
            else
                eps=$(docker exec smsly-hosting-traefik-1 curl -s http://127.0.0.1:8080/api/entrypoints 2>/dev/null)
            fi
            if echo "$eps" | grep -q '"name":"websecure"'; then
                echo -e "${GREEN}    OK websecure entrypoint is active${NC}"
            else
                echo -e "${YELLOW}    WARN websecure entrypoint not detected${NC}"
            fi
            if echo "$eps" | grep -q '"name":"metrics"'; then
                echo -e "${GREEN}    OK metrics entrypoint is active${NC}"
            else
                echo -e "${YELLOW}    WARN metrics entrypoint not detected${NC}"
            fi

            return 0
        fi
        sleep 2
        i=$((i + 1))
    done
    echo -e "${YELLOW}  WARN Traefik has fewer routers than before ($post_routers vs $pre_routers).${NC}"
    echo -e "${YELLOW}        Deployed services have stale Traefik labels (from before the routing fix).${NC}"
    echo -e "${YELLOW}        Redeploy them via the SMSLY dashboard to refresh labels.${NC}"
    return 1
}
ensure_update_networks() {
    # Never delete data networks/volumes in update mode. Only (re)create if missing.
    docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null 2>&1 || true
    docker network inspect smsly-proxy >/dev/null 2>&1 || docker network create smsly-proxy >/dev/null 2>&1 || true
    docker network inspect socket-proxy >/dev/null 2>&1 || docker network create --driver bridge --internal socket-proxy >/dev/null 2>&1 || true
}

get_pgcat_if_exists() {
    local compose_target="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" 2>/dev/null; then
        echo "pgcat"
    fi
}

get_db_service() {
    # Return the correct database service name for the current compose file.
    # Returns:
    #   "postgres-primary" for HA/prod compose (has pgcat)
    #   "db"                 for legacy dev compose
    local ct="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$ct" ] && grep -q "^  *pgcat:" "$ct" 2>/dev/null; then
        echo "postgres-primary"
    else
        echo "db"
    fi
}

get_redis_service() {
    # Return the correct redis service name for the current compose file.
    # Returns:
    #   "redis-primary" for HA/prod compose
    #   "redis"           for legacy dev compose
    local ct="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$ct" ] && grep -q "^  *redis-replica:" "$ct" 2>/dev/null; then
        echo "redis-primary"
    else
        echo "redis"
    fi
}

compose_stack_services() {
    local services=""
    services="$(docker compose -f "$COMPOSE_FILE" config --services)" || return $?
    if is_node_mode; then
        printf '%s\n' "$services" | grep -Ev '^(frontend|caddy)$'
    else
        printf '%s\n' "$services"
    fi
}

compose_stack_service_args() {
    compose_stack_services | tr '\n' ' '
}

compose_stack_build_service_args() {
    local candidates="pgcat backend celery celery-beat frontend celery-fast celery-deploy caddy"
    local svc=""
    if is_node_mode; then
        candidates="pgcat backend celery celery-beat celery-fast celery-deploy"
    fi
    for svc in $candidates; do
        if docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null | grep -qx "$svc"; then
            printf '%s\n' "$svc"
        fi
    done | tr '\n' ' '
}

stop_node_excluded_services() {
    is_node_mode || return 0
    docker compose -f "$COMPOSE_FILE" stop --timeout 15 frontend caddy >/dev/null 2>&1 || true
    docker compose -f "$COMPOSE_FILE" rm -f frontend caddy >/dev/null 2>&1 || true
}

prune_stopped_conflicting() {
    local pattern="$1"
    local c_id=""
    local c_name=""
    local removed=0
    for c_id in $(docker ps -a -q --filter "name=${pattern}" --filter "status=exited" --filter "status=created" 2>/dev/null || true); do
        c_name=$(docker inspect "$c_id" --format='{{.Name}}' 2>/dev/null | sed 's/^\///')
        if [ -n "$c_name" ]; then
            docker rm "$c_id" >/dev/null 2>&1 && removed=$((removed + 1))
        fi
    done
    [ "$removed" -gt 0 ] && echo -e "  \033[0;32m✓\033[0m Removed $removed stopped container(s)" || true
}

cleanup_stale_containers() {
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    timeout 30 docker compose -f "$compose_f" down --remove-orphans 2>/dev/null || true
    prune_stopped_conflicting "smsly-hosting"
    prune_stopped_conflicting "smsly-"
}

docker_login() {
    local registry="${CONTAINER_REGISTRY_URL:-127.0.0.1:5000}"
    local user="${REGISTRY_USER:-smsly-registry}"
    local pass="${REGISTRY_PASSWORD:-}"
    if [ -z "$pass" ]; then
        return 0
    fi
    echo "$pass" | docker login "$registry" -u "$user" --password-stdin >/dev/null 2>&1 || true
}

compose_stack_build() {
    docker_login
    local services=""
    if is_node_mode; then
        stop_node_excluded_services
        services="$(compose_stack_build_service_args)"
        [ -n "$services" ] || return 1
        timeout 300 docker compose -f "$COMPOSE_FILE" build "$@" $services
    else
        timeout 300 docker compose -f "$COMPOSE_FILE" build "$@"
    fi
}

compose_stack_up() {
    local services=""
    if is_node_mode; then
        stop_node_excluded_services
        services="$(compose_stack_service_args)"
        [ -n "$services" ] || return 1
        timeout 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate "$@" $services
    else
        timeout 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate "$@"
    fi
}

ensure_infrastructure_permissions() {
    local caddy_config_dir="/opt/smsly-hosting/caddy-config"
    local staticfiles_dir="/opt/smsly-hosting/backend/staticfiles"
    local builds_dir="/opt/smsly-hosting/builds"
    local prometheus_targets_dir="/opt/smsly-hosting/prometheus-targets"

    echo -e "${BLUE}  -> Ensuring infrastructure permissions...${NC}"

    # 1. Handle Bind-Mounts (Caddy Config & Staticfiles)
    mkdir -p "$caddy_config_dir"
    mkdir -p "$staticfiles_dir"
    mkdir -p "$builds_dir"
    mkdir -p "$prometheus_targets_dir"

    # UID 1000 is the "smsly" user inside the containers.
    # Note: Never chown to host username "smsly:smsly" because host UID may not be 1000.
    _chown_owner="1000:1000"
    for _dir in "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir"; do
        if [ -d "$_dir" ]; then
            if ! chown -R "$_chown_owner" "$_dir" 2>/dev/null; then
                echo -e "${YELLOW}     ⚠ Could not chown $_dir to $_chown_owner${NC}"
            fi
        fi
    done

    chmod -R u+rwX,g+rwX "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir" 2>/dev/null || true
    find "$caddy_config_dir" -type d -exec chmod 2775 {} + 2>/dev/null || true
    find "$staticfiles_dir" -type d -exec chmod 2775 {} + 2>/dev/null || true
    find "$builds_dir" -type d -exec chmod 2775 {} + 2>/dev/null || true
    find "$prometheus_targets_dir" -type d -exec chmod 2777 {} + 2>/dev/null || true
    # Ensure the directory itself has the right permissions (not just children)
    chmod 2777 "$prometheus_targets_dir" 2>/dev/null || true

    # Caddy-specific file permissions
    [ -f "$caddy_config_dir/Caddyfile" ] && chmod 664 "$caddy_config_dir/Caddyfile" 2>/dev/null || true
    [ -f "$caddy_config_dir/.reload" ] && chmod 664 "$caddy_config_dir/.reload" 2>/dev/null || true

    # 2. Handle Named Volumes (backups_data)
    # We use a one-off container to safely chown existing named volumes.
    if command -v docker >/dev/null 2>&1; then
        for vol in backups_data; do
            if docker volume inspect "$vol" >/dev/null 2>&1; then
                echo -e "${BLUE}     ↳ Setting permissions for volume: $vol...${NC}"
                docker run --rm -v "${vol}:/data" alpine chown -R 1000:1000 /data 2>/dev/null || true
            fi
        done
    fi

    # Write probe for all bind-mount directories
    local probe_failed=0
    for probe_dir in "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir"; do
        if ! echo "perm-ok" > "$probe_dir/.perm_probe" 2>/dev/null; then
            echo -e "${YELLOW}  ⚠ Write probe failed for $probe_dir — retrying with chown...${NC}"
            chown -R 1000:1000 "$probe_dir" 2>/dev/null || true
            chmod -R u+rwX,g+rwX "$probe_dir" 2>/dev/null || true
            if echo "perm-ok" > "$probe_dir/.perm_probe" 2>/dev/null; then
                echo -e "${GREEN}    ✓ Fixed${NC}"
            else
                echo -e "${RED}    ✗ Still cannot write to $probe_dir — check host permissions${NC}"
                probe_failed=1
            fi
        fi
        rm -f "$probe_dir/.perm_probe" 2>/dev/null || true
    done
    # Probe the .env file (mounted as a file, not a dir)
    if [ -f "/opt/smsly-hosting/.env" ] && ! touch "/opt/smsly-hosting/.env" 2>/dev/null; then
        echo -e "${YELLOW}  ⚠ .env not writable — fixing...${NC}"
        chown 1000:1000 "/opt/smsly-hosting/.env" 2>/dev/null || true
        chmod 640 "/opt/smsly-hosting/.env" 2>/dev/null || true
    fi
    if [ "$probe_failed" -ne 0 ]; then
        echo -e "${RED}  ✗ Some bind-mount directories are not writable — containers may fail${NC}"
    fi
}

resolve_container_target() {
    local target="$1"

    [ -z "$target" ] && return 0

    # 1. If target is already a valid container ID or name inspectable by docker, return it
    if docker container inspect "$target" >/dev/null 2>&1; then
        echo "$target"
        return 0
    fi

    # 2. Try to map target to a docker compose service.
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$compose_f" ]; then
        local services
        services="$(docker compose -f "$compose_f" config --services 2>/dev/null)"
        if [ -n "$services" ]; then
            for svc in $services; do
                if [[ "$target" == *"-${svc}-"* || "$target" == *"_${svc}_"* || "$target" == *"-${svc}" || "$target" == *"_${svc}" || "$target" == "$svc" ]]; then
                    local cid
                    cid="$(docker compose -f "$compose_f" ps -q "$svc" 2>/dev/null | head -n 1 || true)"
                    if [ -n "$cid" ]; then
                        echo "$cid"
                        return 0
                    fi
                fi
            done
        fi
    fi

    # 3. Fallback: maybe target is a service name itself?
    local cid_svc
    cid_svc="$(docker compose -f "$compose_f" ps -q "$target" 2>/dev/null | head -n 1 || true)"
    if [ -n "$cid_svc" ]; then
        echo "$cid_svc"
        return 0
    fi

    # 4. Fallback: search for container matching substring wildcard
    local cid_fuzzy
    local fuzzy_pattern
    fuzzy_pattern="${target//-/*}"
    fuzzy_pattern="${fuzzy_pattern//_/*}"
    cid_fuzzy="$(docker ps -a --filter "name=${fuzzy_pattern}" -q 2>/dev/null | head -n 1 || true)"
    if [ -n "$cid_fuzzy" ]; then
        echo "$cid_fuzzy"
        return 0
    fi

    # 5. Last resort fallback to the original target string
    echo "$target"
}

ensure_container_on_network() {
    local network_name="$1"
    local raw_target="$2"

    [ -z "$network_name" ] && return 0
    [ -z "$raw_target" ] && return 0

    local container_name
    container_name="$(resolve_container_target "$raw_target")"

    docker container inspect "$container_name" >/dev/null 2>&1 || return 0
    docker network inspect "$network_name" >/dev/null 2>&1 || return 0
    docker network connect "$network_name" "$container_name" >/dev/null 2>&1 || true
}

# ─── Shared Caddy Safety Function ────────────────────────────────────────────
# Called from: recover_runtime_stack, update flow, restart_edge_stack.
# Generates a safe fallback Caddyfile when the current one is broken or risky.
# - Discovers domain from DB first, falls back to .env
# - Skips HTTPS blocks for IP addresses (certs can't be issued)
# - Adds individual Caddy blocks for each deployed service (HTTP-01 SSL)
# - Detects dns cloudflare + missing systemd override (validates passes, runtime crashes)
generate_safe_caddyfile() {
    local reason="${1:-unknown}"
    local candidate="/tmp/Caddyfile.safe.$$"
    echo -e "${YELLOW}  ⚠ Generating safe fallback Caddyfile (reason: $reason)...${NC}"

    # 1. Discover domain: DB first, .env fallback
    local domain=""
    domain="$(timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
" 2>/dev/null | tr -d '[:space:]' || true)"
    if [ -z "$domain" ]; then
        domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    fi

    # 2. Discover ALL deployed service domains from DB (public + custom)
    local svc_blocks=""
    svc_blocks="$(timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
import os
upstream = os.environ.get('SMSLY_SERVICE_PROXY_UPSTREAM', 'traefik:80')
from apps.deployments.models import Service
for svc in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain=''):
    d = svc.public_domain.strip()
    if d:
        print(f'{d} {{\n    reverse_proxy {upstream}\n    encode gzip\n}}\n')
    for cd in (svc.custom_domains or []):
        cd = cd.strip()
        if cd:
            print(f'{cd} {{\n    reverse_proxy {upstream}\n    encode gzip\n}}\n')
" 2>/dev/null | tr -d '\r' || true)"

    # 3. Check if domain is a real hostname (not an IP address)
    local is_real_domain=false
    if [ -n "$domain" ] && [ "$domain" != "localhost" ]; then
        if ! echo "$domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
            is_real_domain=true
        fi
    fi

    # 4. Build the Caddyfile — IP-aware
    local domain_block_label="$domain"
    local safe_ip
    safe_ip="$(detect_public_ip)"
    if ! echo "$domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' && [ "$is_real_domain" = "false" ]; then
        domain_block_label="http://${domain}"
    fi

    cat > "$candidate" <<SAFECADDY
# Auto-generated safe fallback (reason: $reason)
{
    on_demand_tls {
        ask http://backend:8000/api/v1/services/check-domain/
    }
}

${domain_block_label} {
    reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

${safe_ip} {
    tls internal
    redir http://${safe_ip}{uri} 308
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

${svc_blocks}
SAFECADDY
    if install_caddyfile_atomically "$candidate" "safe fallback Caddyfile"; then
        rm -f "$candidate"
        echo -e "${YELLOW}  Safe fallback Caddyfile applied.${NC}"
        return 0
    fi
    rm -f "$candidate"
    return 1
}

# Returns 0 if Caddy config needs fixing, 1 if it's fine.
caddy_needs_fix() {
    should_manage_caddy || return 1
    local dest="${INSTALL_DIR:-/opt/smsly-hosting}/caddy-config/Caddyfile"
    if ! timeout 15 docker compose -f "$COMPOSE_FILE" exec -T caddy caddy validate --config /etc/caddy/Caddyfile 2>/dev/null; then
        return 0  # Syntax error
    fi
    if grep -q 'dns cloudflare' "$dest" 2>/dev/null; then
        local _env_token="${CLOUDFLARE_API_TOKEN:-}"
        if [ -z "$_env_token" ] && [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
            _env_token="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "${INSTALL_DIR:-/opt/smsly-hosting}/.env" 2>/dev/null | cut -d= -f2- || true)"
        fi
        if [ -z "$_env_token" ] || [ "$_env_token" = "fake" ]; then
            return 0  # dns cloudflare without token = runtime crash
        fi
    fi
    return 1  # Config is fine
}

is_real_domain_name() {
    local host="${1:-}"
    [ -n "$host" ] \
        && [ "$host" != "localhost" ] \
        && ! echo "$host" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'
}

https_listener_active() {
    if command -v ss >/dev/null 2>&1; then
        ss -H -tln 2>/dev/null | awk '{print $4}' | grep -Eq ':443$'
    else
        lsof -iTCP:443 -sTCP:LISTEN >/dev/null 2>&1
    fi
}

ensure_caddy_https_listener() {
    return 0
}

restart_caddy_watcher_safely() {
    return 0
}

install_caddy_health_guard() {
    return 0
}

bust_core_build_cache() {
    echo -e "${BLUE}  -> Busting frontend/backend build cache (safe mode)...${NC}"

    # Define core services for cache busting
    local core_svcs="frontend backend celery celery-deploy celery-fast celery-beat"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        core_svcs="backend celery-worker"
    elif [ "$MODE_NODE" = "true" ]; then
        core_svcs="backend celery celery-deploy celery-fast celery-beat"
    fi

    # Remove old app image layers for deterministic rebuilds (no DB/data touched).
    for svc in $core_svcs; do
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
    local edge_services="socket-proxy traefik"
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        edge_services="socket-proxy traefik route-fallback"
    fi

    echo -e "${BLUE}  -> Refreshing edge proxy stack (traefik/socket-proxy/route-fallback)...${NC}"
    # First ensure socket-proxy and route-fallback are running (no recreate).
    # Only Traefik is force-recreated below to avoid disruption to the Docker
    # event stream that socket-proxy provides to Traefik.
    local non_traefik_services="socket-proxy"
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        non_traefik_services="socket-proxy route-fallback"
    fi
    echo -e "${BLUE}    [1/5] Ensuring socket-proxy + route-fallback running...${NC}"
    local all_running=true
    for svc in $non_traefik_services; do
        if ! docker compose -f "$COMPOSE_FILE" ps "$svc" 2>/dev/null | grep -q "Up"; then
            all_running=false
            break
        fi
    done
    if [ "$all_running" = true ]; then
        echo -e "${GREEN}      edge services already running, skipping restart${NC}"
    else
        timeout 30 docker compose -f "$COMPOSE_FILE" up -d --no-deps $non_traefik_services >/dev/null 2>&1 || \
            timeout 30 docker compose -f "$COMPOSE_FILE" up -d $non_traefik_services >/dev/null 2>&1 || true
    fi

    # Force-recreate ONLY Traefik (not socket-proxy) to trigger full container
    # re-discovery. Traefik v3.x removed pollInterval; a fresh start against a
    # stable socket-proxy is the only way to guarantee complete provider re-scan
    # after network topology changes.
    # Brief downtime: ~2-5s while Traefik restarts. Caddy retries through it.
    # NOTE(Zero-Downtime): We removed --force-recreate. Traefik dynamically listens to
    # Docker events and does not need to be restarted. This eliminates the 2-5s downtime
    # for deployed user services during an update.
    echo -e "${BLUE}    [2/5] Ensuring traefik running...${NC}"
    if docker compose -f "$COMPOSE_FILE" ps traefik 2>/dev/null | grep -q "Up"; then
        echo -e "${GREEN}      traefik already running, skipping restart${NC}"
    else
        timeout 30 docker compose -f "$COMPOSE_FILE" up -d traefik >/dev/null 2>&1 || true
    fi

    # Re-attach expected external networks AFTER Traefik restart so it
    # discovers containers with stable network topology (idempotent).
    # If run before 'up -d', Docker Compose will forcefully strip 'smsly-proxy' 
    # since it's not defined in the compose file's networks block.
    echo -e "${BLUE}    [3/5] Re-attaching external networks...${NC}"
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    fi
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    # Validate Caddy config before restart (H1 fix)
    # Use Docker-based Caddy, not host-level binary
    echo -e "${BLUE}    [4/5] Validating Caddy config...${NC}"
    if should_manage_caddy && docker compose -f "$COMPOSE_FILE" ps caddy 2>/dev/null | grep -q "Up"; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "restart_edge_stack validation"
        fi
        echo -e "${BLUE}    [5/5] Reloading Caddy...${NC}"
        reload_container_caddy 2>/dev/null || true
    fi
    echo -e "${GREEN}  OK Edge stack refreshed${NC}"
}

wait_for_traefik_api() {
    local max_wait="${1:-30}"
    local waited=0
    local interval=2
    echo -e "${BLUE}  → Waiting for Traefik API to be ready...${NC}"
    while [ "$waited" -lt "$max_wait" ]; do
        if curl -sf --max-time 3 http://127.0.0.1:8082/api/version >/dev/null 2>&1; then
            echo -e "${GREEN}  ✓ Traefik API ready (${waited}s)${NC}"
            return 0
        fi
        sleep "$interval"
        waited=$((waited + interval))
    done
    echo -e "${YELLOW}  ⚠ Traefik API not ready after ${max_wait}s — services may be unreachable${NC}"
    return 1
}

refresh_runtime_services() {
    # Ensure Docker mirror is configured (Option B)
    configure_docker_mirror

    local app_services_requested=(
        pgcat
        backend
        celery
        celery-deploy
        celery-fast
        celery-beat
        frontend
        frps
    )
    local edge_services_requested=(
        socket-proxy
        route-fallback
        traefik
    )
    local app_services=()
    local edge_services=()
    local runtime_services=()
    local failed_services=()
    local svc=""
    local container_name=""
    local timeout_seconds=120

    echo -e "${BLUE}  -> Performing clean runtime refresh (non-data services only)...${NC}"
    ensure_update_networks
    ensure_infrastructure_permissions
    stop_node_excluded_services

    for svc in "${app_services_requested[@]}"; do
        if is_node_mode && [ "$svc" = "frontend" ]; then
            continue
        fi
        if docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null | grep -qx "$svc"; then
            app_services+=("$svc")
        fi
    done

    for svc in "${edge_services_requested[@]}"; do
        if docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null | grep -qx "$svc"; then
            edge_services+=("$svc")
        fi
    done

    runtime_services=("${app_services[@]}" "${edge_services[@]}")

    if [ "${#runtime_services[@]}" -eq 0 ]; then
        echo -e "${YELLOW}  ⚠ No runtime services found to refresh${NC}"
        return 0
    fi

    if [ "${#app_services[@]}" -gt 0 ]; then
        timeout 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "${app_services[@]}" >/dev/null 2>&1 || \
            timeout 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate "${app_services[@]}" >/dev/null 2>&1 || true
    fi

    ensure_container_on_network "smsly-net" "smsly-hosting-pgcat-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-backend-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-celery-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-celery-beat-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-celery-deploy-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-celery-fast-1"
    if [ "$MODE_NODE" != "true" ]; then
        ensure_container_on_network "smsly-net" "smsly-hosting-frontend-1"
    fi
    ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-frps-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    for svc in "${app_services[@]}"; do
        container_name="smsly-hosting-${svc}-1"
        case "$svc" in
            backend|frontend)
                timeout_seconds=180
                ;;
            *)
                timeout_seconds=120
                ;;
        esac
        if ! wait_for_container_ready "$container_name" "$timeout_seconds"; then
            failed_services+=("$svc")
        fi
    done

    if [ "${#failed_services[@]}" -eq 0 ] && [ "${#edge_services[@]}" -gt 0 ]; then
        timeout 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "${edge_services[@]}" >/dev/null 2>&1 || \
            timeout 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate "${edge_services[@]}" >/dev/null 2>&1 || true

        ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
        ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
        ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
        ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

        for svc in "${edge_services[@]}"; do
            container_name="smsly-hosting-${svc}-1"
            if ! wait_for_container_ready "$container_name" 120; then
                failed_services+=("$svc")
            fi
        done
    fi

    if [ "${#failed_services[@]}" -gt 0 ]; then
        echo -e "${YELLOW}  WARN Runtime refresh left services unready: ${failed_services[*]}${NC}"
        docker compose -f "$COMPOSE_FILE" ps "${failed_services[@]}" 2>/dev/null || true
        docker compose -f "$COMPOSE_FILE" logs --tail=80 "${failed_services[@]}" 2>/dev/null || true
        return 1
    fi

    if should_manage_caddy; then
        install_caddy_health_guard "${DOMAIN:-}"
        reload_container_caddy 2>/dev/null || true
    fi

    if [ "$MODE_AGENT_LITE" != "true" ]; then
        echo -e "${BLUE}  → Refreshing Observability Stack...${NC}"
        if [ -f "infrastructure/docker/docker-compose.observability.yml" ]; then
            docker compose -f infrastructure/docker/docker-compose.observability.yml pull >/dev/null 2>&1 || true
            docker compose -f infrastructure/docker/docker-compose.observability.yml up -d >/dev/null 2>&1 || true
            for obs_ctr in smsly-loki smsly-promtail smsly-prometheus smsly-cadvisor smsly-node-exporter smsly-grafana; do
                i=0
                while [ $i -lt 30 ]; do
                    if docker inspect --format='{{.State.Health.Status}}' "$obs_ctr" 2>/dev/null | grep -qE 'healthy|^$'; then
                        break
                    fi
                    sleep 2
                    i=$((i + 1))
                done
            done
        fi
    fi

    systemctl restart smsly-autoscaler >/dev/null 2>&1 || true
    echo -e "${GREEN}  OK Clean runtime refresh complete${NC}"
}

safe_refresh_runtime_services() {
    if refresh_runtime_services; then
        return 0
    fi

    echo -e "${YELLOW}  -> Runtime refresh incomplete. Running one recovery pass...${NC}"
    recover_runtime_stack || true
    refresh_runtime_services
}

ensure_celery_workers_running() {
    local celery_services=()
    local down_services=()
    for svc in celery celery-deploy celery-fast celery-beat; do
        if docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null | grep -qx "$svc"; then
            celery_services+=("$svc")
        fi
    done
    if [ "${#celery_services[@]}" -eq 0 ]; then
        echo -e "${BLUE}  → No celery services configured, skipping celery check${NC}"
        return 0
    fi
    for svc in "${celery_services[@]}"; do
        if ! docker compose -f "$COMPOSE_FILE" ps "$svc" 2>/dev/null | grep -q "Up"; then
            down_services+=("$svc")
        fi
    done
    if [ "${#down_services[@]}" -eq 0 ]; then
        echo -e "${GREEN}  ✓ All celery workers are running${NC}"
        return 0
    fi
    echo -e "${YELLOW}  ⚠ Celery workers down: ${down_services[*]}. Restarting...${NC}"
    timeout 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "${down_services[@]}" >/dev/null 2>&1 || \
        timeout 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate "${down_services[@]}" >/dev/null 2>&1 || true
    local all_ok=true
    for svc in "${down_services[@]}"; do
        if wait_for_container_ready "smsly-hosting-${svc}-1" 120; then
            echo -e "${GREEN}    ✓ $svc is running${NC}"
        else
            echo -e "${RED}    ✗ $svc failed to start${NC}"
            all_ok=false
        fi
    done
    if [ "$all_ok" = true ]; then
        echo -e "${GREEN}  ✓ All celery workers recovered${NC}"
    fi
}

wait_for_container_ready() {
    local raw_target="$1"
    local timeout_seconds="${2:-180}"
    local elapsed=0
    local state=""

    [ -z "$raw_target" ] && return 1

    local container_name
    container_name="$(resolve_container_target "$raw_target")"

    while [ "$elapsed" -lt "$timeout_seconds" ]; do
        state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name" 2>/dev/null || echo "missing")"
        if [ "$state" = "healthy" ] || [ "$state" = "running" ]; then
            echo -e "${GREEN}  OK $raw_target is $state${NC}"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done

    echo -e "${YELLOW}  WARN $raw_target not ready after ${timeout_seconds}s (state=$state)${NC}"
    return 1
}

sync_agent_lite_rabbitmq_password() {
    [ "$MODE_AGENT_LITE" = "true" ] || return 0

    local env_file="$INSTALL_DIR/.env"
    local rabbitmq_user rabbitmq_password

    rabbitmq_user="$(env_get_value "$env_file" "RABBITMQ_DEFAULT_USER" 2>/dev/null || true)"
    rabbitmq_user="${rabbitmq_user:-smsly_user}"
    rabbitmq_password="$(env_get_value "$env_file" "RABBITMQ_PASSWORD" 2>/dev/null || true)"
    rabbitmq_password="${rabbitmq_password:-$(env_get_value "$env_file" "RABBITMQ_DEFAULT_PASS" 2>/dev/null || true)}"

    if [ -z "$rabbitmq_password" ]; then
        echo -e "${RED}  ERROR RABBITMQ_PASSWORD is empty after agent-lite env generation${NC}"
        exit 1
    fi

    docker compose -f "$COMPOSE_FILE" up -d rabbitmq >/dev/null 2>&1 || true
    wait_for_container_ready "smsly-hosting-rabbitmq-1" 120 || {
        docker compose -f "$COMPOSE_FILE" logs --tail=80 rabbitmq 2>/dev/null || true
        exit 1
    }

    if docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl authenticate_user "$rabbitmq_user" "$rabbitmq_password" >/dev/null 2>&1; then
        echo -e "${GREEN}  OK Lite Agent RabbitMQ password already matches .env${NC}"
        return 0
    fi

    echo -e "${BLUE}  -> Syncing Lite Agent RabbitMQ password for ${rabbitmq_user}...${NC}"
    docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl add_user "$rabbitmq_user" "$rabbitmq_password" >/dev/null 2>&1 || true
    docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl change_password "$rabbitmq_user" "$rabbitmq_password" >/dev/null
    docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl set_user_tags "$rabbitmq_user" administrator >/dev/null
    docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl set_permissions -p / "$rabbitmq_user" ".*" ".*" ".*" >/dev/null

    if docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl authenticate_user "$rabbitmq_user" "$rabbitmq_password" >/dev/null 2>&1; then
        echo -e "${GREEN}  OK Lite Agent RabbitMQ password synced${NC}"
        return 0
    fi

    echo -e "${RED}  ERROR Lite Agent RabbitMQ password sync failed${NC}"
    return 1
}

ensure_security_tools() {
    export PATH="/usr/local/bin:$PATH"
    if ! command -v trivy >/dev/null 2>&1 && [ ! -x "/usr/local/bin/trivy" ]; then
        echo -e "${BLUE}  → Installing Trivy vulnerability scanner...${NC}"
        curl -sfL --connect-timeout 15 --max-time 120 https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin 2>/dev/null || true
    fi
    if ! command -v cosign >/dev/null 2>&1 && [ ! -x "/usr/local/bin/cosign" ]; then
        echo -e "${BLUE}  → Installing Cosign image attestation utility...${NC}"
        local cosign_arch
        cosign_arch="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
        curl -sfL --connect-timeout 15 --max-time 120 -o /usr/local/bin/cosign "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-${cosign_arch}" 2>/dev/null && chmod +x /usr/local/bin/cosign || true
    fi
    return 0
}

