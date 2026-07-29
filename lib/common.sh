_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$_SCRIPT_DIR/logging.sh"
source "$_SCRIPT_DIR/validation.sh"
source "$_SCRIPT_DIR/network.sh"
source "$_SCRIPT_DIR/docker.sh"

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
COMPOSE_FILE="$INSTALL_DIR/docker-compose.prod.yml"
LOCK_FILE="/tmp/smsly-install.lock"
ROLLBACK_NEEDED=false
CADDY_LAST_GOOD="$INSTALL_DIR/caddy-config/Caddyfile.smsly-last-good"

acquire_install_lock() {
    if command -v flock ; then
        exec 9<>"$LOCK_FILE"
        if ! flock -n 9; then
            local pid
            pid="$(cat "$LOCK_FILE"  || true)"
            echo -e "${RED}ERROR: Another installer instance${pid:+ (PID $pid)} is already running.${NC}"
            echo -e "If you are sure no other instance is running, remove $LOCK_FILE and try again."
            exit 1
        fi
        : > "$LOCK_FILE"
        echo "$$" > "$LOCK_FILE"
    else
        if [ -f "$LOCK_FILE" ]; then
            local pid
            pid="$(cat "$LOCK_FILE"  || true)"
            if [ "$pid" != "$$" ] && kill -0 "$pid" ; then
                echo -e "${RED}ERROR: Another installer instance (PID $pid) is already running.${NC}"
                echo -e "If you are sure no other instance is running, remove $LOCK_FILE and try again."
                exit 1
            fi
        fi
        echo "$$" > "$LOCK_FILE"
    fi
}

release_install_lock() {
    if command -v flock ; then
        flock -u 9  || true
        exec 9>&-  || true
    fi
    rm -f "$LOCK_FILE"  || true
}

get_migration_database_alias() {
    local migrate_db
    local direct_url
    direct_url="$(env_get_value "${INSTALL_DIR:-.}/.env" "DIRECT_DATABASE_URL"  || true)"
    if [ -z "$direct_url" ]; then
        direct_url="postgresql://${POSTGRES_USER:-smsly_admin}:${POSTGRES_PASSWORD:-}@${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-smsly_hosting}"
    fi
    migrate_db="$(
        docker run --rm --network smsly-net \
            --user 1000 \
            --env-file "${INSTALL_DIR:-/opt/smsly-hosting}/.env" \
            -e SMSLY_DISABLE_STARTUP_TASKS=true \
            -e SMSLY_MIGRATION_MODE=true \
            -e DIRECT_DATABASE_URL="$direct_url" \
            smsly-hosting-backend:latest \
            python manage.py shell -c \
            "from django.conf import settings; print('direct' if 'direct' in settings.DATABASES else ('session' if 'session' in settings.DATABASES else 'default'))" \
             | tail -n 1 | tr -d '\r'
    )"

    case "$migrate_db" in
        direct|session|default) printf '%s\n' "$migrate_db" ;;
        *) printf '%s\n' "default" ;;
    esac
}

diagnose_migration_locks() {
    local env_file="${INSTALL_DIR:-.}/.env"
    [ -f "$env_file" ] && source "$env_file"  || true

    echo -e "${YELLOW}  -> PostgreSQL activity snapshot (lock diagnosis):${NC}"
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T \
        -e PGPASSWORD="${POSTGRES_PASSWORD:-}" \
        db psql \
            -U "${POSTGRES_USER:-smsly_admin}" \
            -d "${POSTGRES_DB:-smsly_hosting}" \
            -v ON_ERROR_STOP=1 \
            -P pager=off \
            -c "SELECT pid, usename, application_name, state, wait_event_type, wait_event, now() - COALESCE(xact_start, query_start) AS age, left(regexp_replace(query, '\s+', ' ', 'g'), 180) AS query FROM pg_stat_activity WHERE datname = current_database() ORDER BY COALESCE(xact_start, query_start) NULLS LAST LIMIT 20;" \
         || echo -e "${YELLOW}  -> Could not read pg_stat_activity.${NC}"
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
    direct_url="$(env_get_value "${INSTALL_DIR:-.}/.env" "DIRECT_DATABASE_URL"  || true)"
    if [ -z "$direct_url" ]; then
        direct_url="postgresql://${POSTGRES_USER:-smsly_admin}:${POSTGRES_PASSWORD:-}@${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-smsly_hosting}"
    fi

    set +e
    timeout "$((timeout_seconds + 60))" docker run --rm --network smsly-net \
        --user 1000 \
        --env-file "${INSTALL_DIR:-/opt/smsly-hosting}/.env" \
        -e SMSLY_DISABLE_STARTUP_TASKS=true \
        -e SMSLY_MIGRATION_MODE=true \
        -e DIRECT_DATABASE_URL="$direct_url" \
        smsly-hosting-backend:latest \
        timeout "$timeout_seconds" \
        python manage.py migrate --database="$migrate_db" --noinput
    rc=$?
    set -e

    if [ "$rc" -ne 0 ]; then
        if [ "$rc" -eq 124 ]; then
            echo -e "${RED}  x Migrations timed out after ${timeout_seconds}s.${NC}"
        else
            echo -e "${RED}  x Migrations exited with status ${rc}.${NC}"
        fi
        [ "$MODE_AGENT_LITE" != "true" ] && diagnose_migration_locks
        return "$rc"
    fi

    echo -e "${BLUE}  -> Fixing node agent database permissions...${NC}"
    timeout -k 5 60 docker run --rm --network smsly-net \
        --user 1000 \
        --env-file "${INSTALL_DIR:-/opt/smsly-hosting}/.env" \
        -e SMSLY_DISABLE_STARTUP_TASKS=true \
        smsly-hosting-backend:latest \
        python manage.py fix_node_db_permissions  || echo -e "${YELLOW}    ⚠ fix_node_db_permissions failed${NC}"

    if [ "$MODE_AGENT_LITE" != "true" ] && [ -n "$(get_pgcat_if_exists)" ] && docker compose -f "$COMPOSE_FILE" ps pgcat  | grep -q "Up"; then
        echo -e "${BLUE}  -> Reloading PgCat to pick up node agent pools...${NC}"
        timeout -k 5 20 docker compose -f "$COMPOSE_FILE" restart pgcat || echo -e "${YELLOW}    ⚠ PgCat restart failed${NC}"
        sleep 5
        echo -e "${GREEN}  ✓ PgCat reloaded${NC}"
    fi

    return 0
}

export_caddy_cloudflare_env() {
    return 0
}

restore_last_good_caddy() {
    return 0
}

reload_caddy_preserving_previous() {
    reload_container_caddy  || true
    return 0
}

ensure_selfsigned_cert() {
    local cert_dir="${INSTALL_DIR:-/opt/smsly-hosting}/caddy-config/certs"
    local cert_file="$cert_dir/ip.crt"
    local key_file="$cert_dir/ip.key"
    local public_ip="${PUBLIC_IP:-$(detect_public_ip)}"
    local ssl_config="$cert_dir/openssl.cnf"

    mkdir -p "$cert_dir"
    chmod 700 "$cert_dir"  || true

    if ! command -v openssl ; then
        echo -e "${YELLOW}  ⚠ openssl not available; skipping self-signed cert generation${NC}"
        return 0
    fi

    echo -e "${BLUE}  → Generating self-signed cert for IP: $public_ip...${NC}"

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
         || {
        echo -e "${YELLOW}  ⚠ Failed to generate self-signed cert (non-fatal)${NC}"
        rm -f "$ssl_config"
        return 0
    }
    rm -f "$ssl_config"

    chmod 644 "$cert_file"  || true
    chmod 600 "$key_file"  || true
    if [ -n "${SUDO_USER:-}" ]; then
        chown "${SUDO_USER}:${SUDO_USER}" "$key_file"  || chown 1000:1000 "$key_file"  || true
    elif [ "$(id -u)" -eq 0 ]; then
        chown 1000:1000 "$key_file"  || true
    fi
    echo -e "${GREEN}  ✓ Self-signed cert generated for $public_ip${NC}"
}

reload_container_caddy() {
    should_manage_caddy || return 0
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if command -v docker  && docker compose -f "$compose_f" ps -q caddy  | grep -q .; then
        timeout -k 5 20 docker compose -f "$compose_f" exec -T caddy caddy reload --config /etc/caddy/Caddyfile || \
            timeout -k 5 20 docker compose -f "$compose_f" restart caddy || \
            echo -e "${YELLOW}    ⚠ Caddy reload failed${NC}"
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

    reload_container_caddy  || true
    return 0
}

generate_safe_caddyfile() {
    local reason="${1:-unknown}"
    local candidate="/tmp/Caddyfile.safe.$$"
    echo -e "${YELLOW}  ⚠ Generating safe fallback Caddyfile (reason: $reason)...${NC}"

    local domain=""
    domain="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
"  | tr -d '[:space:]' || true)"
    if [ -z "$domain" ]; then
        domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- || true)"
    fi

    local svc_blocks=""
    svc_blocks="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
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
"  | tr -d '\r' || true)"

    local is_real_domain=false
    if [ -n "$domain" ] && [ "$domain" != "localhost" ]; then
        if ! echo "$domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
            is_real_domain=true
        fi
    fi

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

caddy_needs_fix() {
    should_manage_caddy || return 1
    local dest="${INSTALL_DIR:-/opt/smsly-hosting}/caddy-config/Caddyfile"
    if ! timeout -k 5 15 docker compose -f "$COMPOSE_FILE" exec -T caddy caddy validate --config /etc/caddy/Caddyfile ; then
        return 0
    fi
    if grep -q 'dns cloudflare' "$dest" ; then
        local _env_token="${CLOUDFLARE_API_TOKEN:-}"
        if [ -z "$_env_token" ] && [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
            _env_token="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "${INSTALL_DIR:-/opt/smsly-hosting}/.env"  | cut -d= -f2- || true)"
        fi
        if [ -z "$_env_token" ] || [ "$_env_token" = "fake" ]; then
            return 0
        fi
    fi
    return 1
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

sync_agent_lite_rabbitmq_password() {
    [ "$MODE_AGENT_LITE" = "true" ] || return 0

    local env_file="$INSTALL_DIR/.env"
    local rabbitmq_user rabbitmq_password

    rabbitmq_user="$(env_get_value "$env_file" "RABBITMQ_DEFAULT_USER"  || true)"
    rabbitmq_user="${rabbitmq_user:-smsly_user}"
    rabbitmq_password="$(env_get_value "$env_file" "RABBITMQ_PASSWORD"  || true)"
    rabbitmq_password="${rabbitmq_password:-$(env_get_value "$env_file" "RABBITMQ_DEFAULT_PASS"  || true)}"

    if [ -z "$rabbitmq_password" ]; then
        echo -e "${RED}  ERROR RABBITMQ_PASSWORD is empty after agent-lite env generation${NC}"
        exit 1
    fi

    docker compose -f "$COMPOSE_FILE" up -d rabbitmq || echo -e "${YELLOW}    ⚠ RabbitMQ start failed${NC}"
    wait_for_container_ready "smsly-hosting-rabbitmq-1" 120 || {
        docker compose -f "$COMPOSE_FILE" logs --tail=80 rabbitmq  || true
        exit 1
    }

    if timeout 30 docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl authenticate_user "$rabbitmq_user" "$rabbitmq_password" ; then
        echo -e "${GREEN}  OK Lite Agent RabbitMQ password already matches .env${NC}"
        return 0
    fi

    echo -e "${BLUE}  -> Syncing Lite Agent RabbitMQ password for ${rabbitmq_user}...${NC}"
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl add_user "$rabbitmq_user" "$rabbitmq_password" || echo -e "${YELLOW}    ⚠ RabbitMQ add_user failed${NC}"
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl change_password "$rabbitmq_user" "$rabbitmq_password" || true
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl set_user_tags "$rabbitmq_user" administrator || true
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl set_permissions -p / "$rabbitmq_user" ".*" ".*" ".*" || true

    if timeout 30 docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl authenticate_user "$rabbitmq_user" "$rabbitmq_password" ; then
        echo -e "${GREEN}  OK Lite Agent RabbitMQ password synced${NC}"
        return 0
    fi

    echo -e "${RED}  ERROR Lite Agent RabbitMQ password sync failed${NC}"
    return 1
}

ensure_security_tools() {
    export PATH="/usr/local/bin:$PATH"
    if ! command -v trivy  && [ ! -x "/usr/local/bin/trivy" ]; then
        echo -e "${BLUE}  → Installing Trivy vulnerability scanner...${NC}"
        curl -sfL --connect-timeout 15 --max-time 120 https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin  || true
    fi
    if ! command -v cosign  && [ ! -x "/usr/local/bin/cosign" ]; then
        echo -e "${BLUE}  → Installing Cosign image attestation utility...${NC}"
        local cosign_arch
        cosign_arch="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
        curl -sfL --connect-timeout 15 --max-time 120 -o /usr/local/bin/cosign "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-${cosign_arch}"  && chmod +x /usr/local/bin/cosign || true
    fi
    return 0
}
