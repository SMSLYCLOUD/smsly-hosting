# -----------------------------------------------------------------------------
# 3. Configuration & Secrets (IDEMPOTENT)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "config_generated"; then
    echo -e "\n${YELLOW}[3/9] Configuration...${NC}"

mkdir -p "$INSTALL_DIR"

# Ensure we are in the install directory with correct files
if [ "$(pwd)" != "$INSTALL_DIR" ]; then
    echo -e "${BLUE}  → Setting up installation in $INSTALL_DIR${NC}"
    if [ -f "docker-compose.prod.yml" ]; then
        if [ "${SMSLY_FORCE_SOURCE_SYNC:-0}" = "1" ]; then
            cp -rf . "$INSTALL_DIR/"
        else
            cp -rn . "$INSTALL_DIR/"  || cp -r . "$INSTALL_DIR/"
        fi
    else
        if [ -d "$INSTALL_DIR/.git" ]; then
             echo -e "${BLUE}  → Updating existing repository...${NC}"
             cd "$INSTALL_DIR"
             if ! git pull origin "$SMSLY_BRANCH" ; then
                 echo -e "${RED}  ✗ Git pull failed for $SMSLY_BRANCH. SSL verification is always enforced.${NC}"
             fi
        else
             echo -e "${BLUE}  → Cloning repository...${NC}"
             if [ -f "$INSTALL_DIR/.env" ]; then
                 cp "$INSTALL_DIR/.env" /tmp/smsly-env-backup  || true
             fi
             rm -rf "$INSTALL_DIR"
             if ! git clone -b "$SMSLY_BRANCH" "${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}" "$INSTALL_DIR"; then
                 echo -e "${RED}  ✗ Git clone failed for $SMSLY_BRANCH. SSL verification is always enforced.${NC}"
             fi
             cd "$INSTALL_DIR"
             if [ -f /tmp/smsly-env-backup ]; then
                 cp /tmp/smsly-env-backup "$INSTALL_DIR/.env"
                 rm -f /tmp/smsly-env-backup
                 echo -e "${GREEN}  ✓ Restored existing .env${NC}"
             fi
        fi
    fi
fi
cd "$INSTALL_DIR"

# ─── Git Initialization (for bundled installs) ──────────────────────────────
if [ ! -d ".git" ] && [ -n "${SMSLY_GIT_REMOTE:-}" ]; then
    echo -e "${BLUE}  -> Initializing Git repository...${NC}"
    git init -q
    git checkout -b "$SMSLY_BRANCH"  || true
    git remote add origin "$SMSLY_GIT_REMOTE"
    if ! git fetch origin "$SMSLY_BRANCH" -q --depth=1; then
        echo -e "${YELLOW}  ⚠ Git fetch failed — repository will be unlinked from remote (SSL verification enforced)${NC}"
    fi
    git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH"  || true
    # We don't reset --hard here to avoid losing the bundled files we just copied,
    # but the repo is now linked for future updates.
    echo -e "${GREEN}  ✓ Git origin set to ${SMSLY_GIT_REMOTE}${NC}"
fi

# ─── BLINDSPOT FIX: Validate required deployment files ──────────────────────
echo -e "${BLUE}  → Validating deployment files...${NC}"
MISSING_FILES=()
if [ "$MODE_AGENT_LITE" = "true" ]; then
    REQUIRED_FILES=("$COMPOSE_FILE" "backend/Dockerfile" "backend/entrypoint.sh" "backend/requirements.txt")
elif [ "$MODE_NODE" = "true" ]; then
    REQUIRED_FILES=("$COMPOSE_FILE" "backend/Dockerfile" "backend/entrypoint.sh" "backend/requirements.txt")
else
    REQUIRED_FILES=("$COMPOSE_FILE" "backend/Dockerfile" "frontend/Dockerfile" "backend/entrypoint.sh")
fi
for required_file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$required_file" ]; then
        MISSING_FILES+=("$required_file")
    fi
done

if [ ${#MISSING_FILES[@]} -gt 0 ]; then
    echo -e "${RED}✗ Missing required files:${NC}"
    for f in "${MISSING_FILES[@]}"; do
        echo -e "${RED}    - $f${NC}"
    done
    exit 1
fi
echo -e "${GREEN}  ✓ All required deployment files present${NC}"

# ─── BLINDSPOT FIX: Ensure correct compose file is used ─────────────────────
# Check if any containers are running with the wrong compose file (dev instead of prod)
wrong_project=false
for c_id in $(docker ps --filter "name=smsly-hosting" -q  || true); do
    config_file=$(docker inspect "$c_id" --format='{{index .Config.Labels "com.docker.compose.project.config_files"}}'  || true)
    compose_base=$(basename "$COMPOSE_FILE")
    if [ -n "$config_file" ] && [[ "$config_file" != *"$compose_base"* ]]; then
        wrong_project=true
        break
    fi
done

if [ "$wrong_project" = "true" ]; then
    echo -e "${YELLOW}  ⚠ Found containers running from a different compose project configuration. Stopping...${NC}"
    for c_id in $(docker ps --filter "name=smsly-hosting" -q  || true); do
        config_file=$(docker inspect "$c_id" --format='{{index .Config.Labels "com.docker.compose.project.config_files"}}'  || true)
        compose_base=$(basename "$COMPOSE_FILE")
        if [ -n "$config_file" ] && [[ "$config_file" != *"$compose_base"* ]]; then
            docker stop "$c_id"  || true
            docker rm "$c_id"  || true
        fi
    done
fi

# ─── IDEMPOTENCY: Skip secret generation if .env already exists ─────────────
if [ -f "$INSTALL_DIR/.env" ]; then
    echo -e "${GREEN}  ✓ Existing .env found — preserving configuration${NC}"
    echo -e "${BLUE}  → Backing up existing .env to .env.backup${NC}"
    cp "$INSTALL_DIR/.env" "$INSTALL_DIR/.env.backup"

    # Backfill newer required keys and validate before deployment.
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    apply_agent_lite_env_overrides "$INSTALL_DIR/.env"
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x Existing .env is invalid. Fix it or restore .env.backup and rerun.${NC}"
        exit 1
    fi

    # Source existing values for summary output.
    set -a
    source "$INSTALL_DIR/.env"  || true
    set +a
    DOMAIN="${DOMAIN:-localhost}"
    USE_SSL="${USE_SSL:-false}"
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-false}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
    PUBLIC_IP="$(detect_public_ip)"


else
    # ─── Configuration Summary ──────────────────────────────────────────────
    PUBLIC_IP="${PUBLIC_IP:-$(detect_public_ip)}"
    DOMAIN="${DOMAIN:-$PUBLIC_IP}"
    # SEC-002: IP-mode SSL guard — force USE_SSL=false if DOMAIN is a raw IP
    if echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        USE_SSL="${USE_SSL:-false}"
        if [ "${USE_SSL:-false}" = "true" ]; then
            echo -e "${YELLOW}  ⚠ WARNING: USE_SSL=true ignored — DOMAIN is a raw IP. Forcing USE_SSL=false.${NC}"
        fi
        USE_SSL="false"
    else
        USE_SSL="${USE_SSL:-false}"
    fi
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-false}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
    ACME_EMAIL="${ACME_EMAIL:-}"

    # ─── Generate Secrets (scripts/generate_env_secrets.py — single source of truth) ──
    echo -e "${BLUE}  → Generating secure credentials...${NC}"

    # Ensure cryptography is installed (required for Fernet key generation).
    # Retry with and without --break-system-packages for different Ubuntu versions.
    pip3 install cryptography -q --break-system-packages  || \
        pip3 install cryptography -q  || \
        (echo -e "${YELLOW}  → Retrying cryptography install...${NC}" && \
         pip3 install cryptography 2>&1 | tail -3) || true

    # Verify cryptography is importable before proceeding
    if ! python3 -c "from cryptography.fernet import Fernet; print('ok')" ; then
        echo -e "${RED}  ✗ CRITICAL: cryptography package is not installable.${NC}"
        echo -e "${RED}    The 'cryptography' package is required to generate a Fernet encryption key.${NC}"
        echo -e "${RED}    Install it manually: pip3 install cryptography${NC}"
        exit 1
    fi

    # Use the dedicated secrets generation script (single source of truth).
    # SECURITY: stream secrets directly into shell variables via process
    # substitution so the plaintext never touches the filesystem. The previous
    # implementation wrote to $INSTALL_DIR/.secrets.tmp which could leak on
    # early failure (rm -f only ran on the success path).
    SECRETS_GENERATED=false
    while IFS='=' read -r _smsly_secrets_key _smsly_secrets_val; do
        case "$_smsly_secrets_key" in
            SECRET_KEY|FIELD_ENCRYPTION_KEY|POSTGRES_PASSWORD|REDIS_PASSWORD|RABBITMQ_PASSWORD|GATEWAY_SECRET|GITHUB_WEBHOOK_SECRET|AUTOSCALER_API_TOKEN|FRP_AUTH_TOKEN|PGCAT_ADMIN_PASSWORD|REPLICATION_PASSWORD|SENTINEL_PASSWORD|REGISTRY_HTTP_SECRET|CROWDSEC_BOUNCER_KEY)
                printf -v "$_smsly_secrets_key" '%s' "$_smsly_secrets_val"
                ;;
        esac
    done < <(python3 "$INSTALL_DIR/scripts/generate_env_secrets.py" --shell  | grep -E '^[A-Z_]+=' || true)
    unset _smsly_secrets_key _smsly_secrets_val
    if [ -n "${SECRET_KEY:-}" ] && [ -n "${FIELD_ENCRYPTION_KEY:-}" ]; then
        SECRETS_GENERATED=true
        echo -e "${GREEN}  ✓ Secrets generated (Fernet key validated)${NC}"
    else
        echo -e "${YELLOW}  ⚠ Secrets script ran but Fernet key is missing — generating inline...${NC}"
    fi

    # Fallback: if the script didn't produce a valid Fernet key, generate it inline
    # (cryptography is guaranteed importable at this point from the check above).
    if [ -z "${FIELD_ENCRYPTION_KEY:-}" ]; then
        FIELD_ENCRYPTION_KEY="${MASTER_FIELD_ENCRYPTION_KEY:-$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  || true)}"
    fi
    # Ensure all other secrets have fallback values just in case
    [ -n "${SECRET_KEY:-}" ] || SECRET_KEY="$(python3 -c "import secrets,string; chars=string.ascii_letters+string.digits; print(''.join(secrets.choice(chars) for _ in range(50)))"  || true)"
    [ -n "${POSTGRES_PASSWORD:-}" ] || POSTGRES_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${REDIS_PASSWORD:-}" ] || REDIS_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${RABBITMQ_PASSWORD:-}" ] || RABBITMQ_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${GATEWAY_SECRET:-}" ] || GATEWAY_SECRET="$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
    [ -n "${GITHUB_WEBHOOK_SECRET:-}" ] || GITHUB_WEBHOOK_SECRET="$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
    [ -n "${AUTOSCALER_API_TOKEN:-}" ] || AUTOSCALER_API_TOKEN="$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
    [ -n "${FRP_AUTH_TOKEN:-}" ] || FRP_AUTH_TOKEN="$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
    [ -n "${PGCAT_ADMIN_PASSWORD:-}" ] || PGCAT_ADMIN_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(48))"  || true)"
    [ -n "${GRAFANA_PASSWORD:-}" ] || GRAFANA_PASSWORD="$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits+'-_') for _ in range(40)))"  || openssl rand -base64 30 | tr -d '+/=' )"
    [ -n "${BACKUP_ENCRYPTION_KEY:-}" ] || BACKUP_ENCRYPTION_KEY="$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  || openssl rand -base64 32)"
    [ -n "${CROWDSEC_BOUNCER_KEY:-}" ] || CROWDSEC_BOUNCER_KEY="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${REGISTRY_HTTP_SECRET:-}" ] || REGISTRY_HTTP_SECRET="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${BACKUP_REQUIRE_ENCRYPTION:-}" ] || BACKUP_REQUIRE_ENCRYPTION="true"
    # SECURITY: SSH strict host-key checking. Defaults to false (accept-first)
    # for convenience during initial provisioning. Operators managing trusted
    # environments with pre-populated known_hosts should set this to "true".
    [ -n "${SMSLY_STRICT_SSH_HOST_KEY_CHECK:-}" ] || SMSLY_STRICT_SSH_HOST_KEY_CHECK="false"
    # Read-replica plumbing (used by pgcat for replica routing).
    # Initialize empty defaults so set -u doesn't trip on them later.
    [ -n "${REPLICATION_PASSWORD:-}" ] || REPLICATION_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${SENTINEL_PASSWORD:-}" ] || SENTINEL_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${DB_REPLICA_HOSTS:-}" ] || DB_REPLICA_HOSTS=""

    # Validate Fernet key format
    if ! echo "$FIELD_ENCRYPTION_KEY" | python3 -c "
import sys
from cryptography.fernet import Fernet
try:
    Fernet(sys.stdin.read().strip().encode())
    print('valid')
except Exception:
    print('invalid')
"  | grep -q valid; then
        echo -e "${RED}  ✗ CRITICAL: Failed to generate a valid Fernet encryption key.${NC}"
        echo -e "${RED}    Ensure the 'cryptography' package is installed and retry.${NC}"
        echo -e "${RED}    pip3 install cryptography${NC}"
        exit 1
    fi

    echo -e "${GREEN}  ✓ All secrets generated successfully${NC}"

    # ─── Cosign keypair (image signing) ────────────────────────────────────
    # Generate a password-protected cosign keypair so the platform's own
    # builds can produce verifiable signatures.  Without this, every local
    # build falls through to keyless Sigstore signing which only works with
    # GitHub Actions OIDC — private-key signing is a hard requirement on
    # self-hosted / air-gapped nodes.
    echo -e "${BLUE}  → Bootstrapping Cosign signing keypair...${NC}"
    mkdir -p "$INSTALL_DIR/cosign-keys"
    COSIGN_PASSWORD="${COSIGN_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_hex(32))"  || openssl rand -hex 32  || echo 'cosign-placeholder')}"
    COSIGN_PRIVATE_KEY_PATH="$INSTALL_DIR/cosign-keys/cosign.key"
    COSIGN_PUBLIC_KEY_PATH="$INSTALL_DIR/cosign-keys/cosign.pub"
    if [ ! -f "$COSIGN_PRIVATE_KEY_PATH" ] || [ ! -f "$COSIGN_PUBLIC_KEY_PATH" ]; then
        if command -v cosign ; then
            COSIGN_PASSWORD="$COSIGN_PASSWORD" cosign generate-key-pair  || true
            # cosign writes to cosign.key / cosign.pub in cwd
            if [ -f cosign.key ]; then
                mv cosign.key "$COSIGN_PRIVATE_KEY_PATH"
                mv cosign.pub "$COSIGN_PUBLIC_KEY_PATH"
                chmod 600 "$COSIGN_PRIVATE_KEY_PATH"
                chmod 644 "$COSIGN_PUBLIC_KEY_PATH"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PASSWORD" "$COSIGN_PASSWORD"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PRIVATE_KEY_PATH" "$COSIGN_PRIVATE_KEY_PATH"
                echo -e "${GREEN}    ✓ Cosign keypair created at $INSTALL_DIR/cosign-keys/${NC}"
            else
                echo -e "${YELLOW}    ⚠ cosign generate-key-pair ran but no keyfile produced — keyless only${NC}"
            fi
        else
            echo -e "${YELLOW}    ⚠ cosign not installed — image signing will be keyless (requires GitHub OIDC)${NC}"
            echo -e "${YELLOW}    Install with: 'sudo bash install.sh --update' or download cosign manually${NC}"
        fi
    else
        echo -e "${GREEN}    ✓ Cosign keypair already exists — skipping generation${NC}"
    fi


    # Agent-lite nodes must use the master's DB password, not a locally generated one.
    # SSH into the master to fetch the correct POSTGRES_PASSWORD.
    if is_agent_lite_mode && [ -n "${MASTER_IP:-}" ] && [ "$MASTER_IP" != "127.0.0.1" ]; then
        echo -e "${BLUE}  → Fetching master DB password via SSH (master: ${MASTER_IP})...${NC}"
        _master_db_pw="$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes root@${MASTER_IP} \
            "grep '^POSTGRES_PASSWORD=' /opt/smsly-hosting/.env  | head -1 | cut -d= -f2"  || true)"
        if [ -n "${_master_db_pw:-}" ]; then
            POSTGRES_PASSWORD="$_master_db_pw"
            echo -e "${GREEN}  ✓ Retrieved master DB password${NC}"
        else
            echo -e "${YELLOW}  ⚠ Could not retrieve master DB password via SSH. DATABASE_URL may not connect.${NC}"
            echo -e "${YELLOW}    Tip: Pass MASTER_DB_PASSWORD=... to the install script.${NC}"
        fi
    fi

    # Create .env (Atomic)
    ENV_TMP="$INSTALL_DIR/.env.tmp"
    ENV_MODE_VALUE="$(mode_env_value)"
    ENV_NODE_TYPE="$INSTALL_MODE"
    ENV_TRAEFIK_HTTP_BIND="127.0.0.1:8081"
    ENV_TRAEFIK_HTTPS_BIND="127.0.0.1:8443"
    ENV_STARTUP_CADDY_SYNC="true"
    if is_agent_lite_mode; then
        ENV_NODE_TYPE="agent-lite"
        ENV_STARTUP_CADDY_SYNC="false"
    elif is_node_mode; then
        ENV_NODE_TYPE="node"
        ENV_TRAEFIK_HTTP_BIND="0.0.0.0:80"
        ENV_TRAEFIK_HTTPS_BIND="0.0.0.0:443"
        ENV_STARTUP_CADDY_SYNC="false"
    fi
    # Auto-detect Redis Sentinel containers before writing .env.
    if [ -z "${SENTINEL_HOSTS:-}" ]; then
        _detected_sentinels=""
        for _si in 1 2 3; do
            if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "smsly-redis-sentinel-${_si}$"; then
                [ -n "$_detected_sentinels" ] && _detected_sentinels="${_detected_sentinels},"
                _detected_sentinels="${_detected_sentinels}smsly-redis-sentinel-${_si}:26379"
            fi
        done
        [ -n "$_detected_sentinels" ] && SENTINEL_HOSTS="$_detected_sentinels"
    fi
    cat <<EOF > "$ENV_TMP"
# SMSLY Hosting Configuration — Generated $(date -Iseconds)
ENVIRONMENT=production
NODE_TYPE=$ENV_NODE_TYPE
MODE=$ENV_MODE_VALUE
# Compose file used by 'install.sh --update' and other orchestrator scripts.
# NOTE: inside an unquoted heredoc (cat <<EOF), bash still expands
# command substitution on comment lines too. Do NOT put unescaped
# dollar-paren or backtick sequences in heredoc comments.
# Master mode: docker-compose.yml (base file with traefik + caddy inlined).
# Agent-lite mode: overridden below to infrastructure/docker/docker-compose.agent-lite.yml.
COMPOSE_FILE=$INSTALL_DIR/docker-compose.prod.yml
DEBUG=False
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$FIELD_ENCRYPTION_KEY
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_USER=smsly_admin
POSTGRES_DB=smsly_hosting
POSTGRES_HOST=postgres-primary
DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@pgcat:5432/smsly_hosting
DATABASE_CONNECT_TIMEOUT=5

# ── Database HA mode ─────────────────────────────────────────────────
# local-ha | patroni | external (see .env.example for semantics).
# Docker Compose natively honors COMPOSE_PROFILES from this file, so
# every `docker compose` call picks the right DB stack with no flags.
DB_HA_ENABLED=${DB_HA_ENABLED:-local-ha}
COMPOSE_PROFILES=$DB_HA_ENABLED
# PgCat upstream. patroni mode routes through HAProxy write/read ports.
PGCAT_DB_HOST=${PGCAT_DB_HOST:-postgres-primary}
PGCAT_DB_PORT=${PGCAT_DB_PORT:-5432}

REDIS_PASSWORD=$REDIS_PASSWORD
RABBITMQ_PASSWORD=$RABBITMQ_PASSWORD
RABBITMQ_DEFAULT_USER=smsly_user
RABBITMQ_DEFAULT_PASS=$RABBITMQ_PASSWORD
REDIS_HOST=redis-primary
REDIS_URL=redis://:$REDIS_PASSWORD@redis-primary:6379/0
REDIS_MIN_REPLICAS_TO_WRITE=1
REDIS_MIN_REPLICAS_MAX_LAG=10
# CELERY_ prefix is required for celery-redbeat to read this (see
# backend/config/settings.py: CELERY_REDBEAT_REDIS_URL). Without the prefix
# redbeat falls back to CELERY_BROKER_URL (RabbitMQ AMQP) and redis-py
# crashes with "Redis URL must specify one of the following schemes".
CELERY_REDBEAT_REDIS_URL=redis://:$REDIS_PASSWORD@redis-primary:6379/3

# ── Redis Sentinel (HA) ──────────────────────────────────────────────
# When SENTINEL_HOSTS is set, all Redis connections route through
# Sentinel for automatic master failover.  Set by the HA Redis overlay
# (docker-compose.ha-redis.yml).  Leave empty for standalone Redis.
SENTINEL_HOSTS=${SENTINEL_HOSTS:-}
SENTINEL_SERVICE_NAME=${SENTINEL_SERVICE_NAME:-mymaster}
SENTINEL_PASSWORD=${SENTINEL_PASSWORD:-}

# ── PostgreSQL streaming replication ──────────────────────────────────
REPLICATION_PASSWORD=${REPLICATION_PASSWORD:-}
DB_REPLICA_HOSTS=${DB_REPLICA_HOSTS:-}
REGISTRY_HTTP_SECRET=${REGISTRY_HTTP_SECRET:-}

# ── PostgreSQL durability ─────────────────────────────────────────────
PG_SYNCHRONOUS_COMMIT=on
# PG_SYNCHRONOUS_STANDBY_NAMES=  (unset = async replication)

REDIS_SOCKET_TIMEOUT=5
CELERY_BROKER_URL=amqp://smsly_user:$RABBITMQ_PASSWORD@rabbitmq:5672//

DOMAIN=$DOMAIN
ACME_EMAIL=${ACME_EMAIL:-}
USE_SSL=$USE_SSL

# Inter-service HMAC authentication secret
GATEWAY_SECRET=$GATEWAY_SECRET

# CrowdSec Bouncer Key
CROWDSEC_BOUNCER_KEY=$CROWDSEC_BOUNCER_KEY

# GitHub webhook signature verification
GITHUB_WEBHOOK_SECRET=$GITHUB_WEBHOOK_SECRET

# Security
ALLOWED_HOSTS=$DOMAIN,localhost,127.0.0.1
EOF

    # Build scheme-appropriate origins (avoid https://IP which breaks CORS/CSRF)
    if echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || [ "$USE_SSL" != "true" ]; then
        DOMAIN_ORIGINS="http://$DOMAIN"
    else
        DOMAIN_ORIGINS="https://$DOMAIN"
    fi
    cat >> "$ENV_TMP" <<EOF
CSRF_TRUSTED_ORIGINS=http://$PUBLIC_IP:8090,$DOMAIN_ORIGINS,http://localhost:8090,http://$PUBLIC_IP
CORS_ALLOWED_ORIGINS=http://$PUBLIC_IP:8090,$DOMAIN_ORIGINS,http://$PUBLIC_IP

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

# FRP Tunnel Relay Authentication Token
FRP_AUTH_TOKEN=$FRP_AUTH_TOKEN

# PgCat administration password
PGCAT_ADMIN_PASSWORD=$PGCAT_ADMIN_PASSWORD

# Grafana admin password (used by the standalone observability stack)
GRAFANA_PASSWORD=${GRAFANA_PASSWORD:-}

# Grafana external URL for browser embeds (auto-derived from domain)
GRAFANA_EXTERNAL_URL=${DOMAIN_ORIGINS}/grafana

# Direct database connection for migrations (bypasses PgCat pooler)
DIRECT_DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@postgres-primary:5432/smsly_hosting

# Private Docker registry (push/pull deployment images)
CONTAINER_REGISTRY_URL=registry:5000
REGISTRY_USER=smsly-registry

# The installer runs first-boot Django setup explicitly after the stack starts.
# Keep the web container from doing the same work while Compose is waiting on health.
SMSLY_RUN_ENTRYPOINT_TASKS=false

# AppConfig.ready() must stay side-effect free during installs and management commands.
# Edge/proxy sync is performed explicitly by the installer and watcher services.
SMSLY_ENABLE_STARTUP_CADDY_SYNC=$ENV_STARTUP_CADDY_SYNC
TRAEFIK_HTTP_BIND=$ENV_TRAEFIK_HTTP_BIND
TRAEFIK_HTTPS_BIND=$ENV_TRAEFIK_HTTPS_BIND
EOF

    # ─── Dynamic Build Resource Allocation ──────────────────────────────
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        echo -e "${BLUE}  → Lite Agent mode: frontend build is not part of this node.${NC}"
    elif [ "$MODE_NODE" = "true" ]; then
        echo -e "${BLUE}  → Node mode: frontend build is not part of this node.${NC}"
    else
        # Detect physical RAM for optimized build limits
        current_ram_mb=$(free -m | awk '/^Mem:/{print $2}')
        build_mem=2048
        if [ "$current_ram_mb" -ge 16384 ]; then
            build_mem=8192
        elif [ "$current_ram_mb" -ge 8192 ]; then
            build_mem=4096
        fi
        echo "FRONTEND_BUILD_MEMORY_MB=$build_mem" >> "$ENV_TMP"
        echo -e "${BLUE}  → Allocated ${build_mem}MB for frontend build (System RAM: ${current_ram_mb}MB)${NC}"
    fi

    # Derive expected tunnel domain
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ] && ! echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EXPECTED_TUNNEL_DOMAIN="tunnel.${DOMAIN}"
    elif [ -n "$PUBLIC_IP" ] && ! echo "$PUBLIC_IP" | grep -qE '^(127\.0\.0\.1|0\.0\.0\.0)$'; then
        EXPECTED_TUNNEL_DOMAIN="tunnel.${PUBLIC_IP}.sslip.io"
    else
        EXPECTED_TUNNEL_DOMAIN="tunnel.localhost"
    fi
    echo "TUNNEL_DOMAIN=$EXPECTED_TUNNEL_DOMAIN" >> "$ENV_TMP"

    # ── Agent Lite Overrides ──────────────────────────────────────
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        apply_agent_lite_env_overrides "$ENV_TMP"
    fi

    # Atomic move and validation
    if validate_env_file "$ENV_TMP"; then
        mv "$ENV_TMP" "$INSTALL_DIR/.env"
        # Sync the backup so rollback doesn't restore stale/empty .env.backup
        cp "$INSTALL_DIR/.env" "$INSTALL_DIR/.env.backup"
        # 664 so the backend container (runs as UID 1000) can read AND write it.
        # This allows the domain-config signal to persist DOMAIN/USE_SSL back to
        # .env when the user updates settings via the web UI — no SSH needed.
        chown root:1000 "$INSTALL_DIR/.env"
        chmod 640 "$INSTALL_DIR/.env"
        # Docker Compose v2+ resolves .env from the compose file's parent directory,
        # not the CWD. Create a symlink so all compose files can find it.
        _compose_env_link="$INSTALL_DIR/infrastructure/docker/.env"
        rm -f "$_compose_env_link"  || true
        ln -sf ../../.env "$_compose_env_link"  || true
        echo -e "${GREEN}  ✓ Configuration saved to .env${NC}"
    else
        echo -e "${RED}  x Generated .env failed validation. Aborting install.${NC}"
        rm -f "$ENV_TMP"
        exit 1
    fi
fi
# Sync the backup so rollback doesn't restore a stale/empty .env.backup (e.g.
# when the harden phase created a stub .env with only CONTAINER_RUNTIME before
# config_generated backfilled the real secrets into it).
if [ -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env" "$INSTALL_DIR/.env.backup"
fi
    set_checkpoint "config_generated"
fi
if [ -f "$INSTALL_DIR/.env" ]; then
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    apply_agent_lite_env_overrides "$INSTALL_DIR/.env"
    # Ensure .env symlink exists for Docker Compose v2+ .env resolution
    _compose_env_link="$INSTALL_DIR/infrastructure/docker/.env"
    rm -f "$_compose_env_link"  || true
    ln -sf ../../.env "$_compose_env_link"  || true
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x Existing .env is invalid after runtime-default reconciliation.${NC}"
        exit 1
    fi
fi
load_install_env_defaults "$INSTALL_DIR/.env"

# Ensure all variables in .env are exported to the environment so they are inherited by docker compose
if [ -f "$INSTALL_DIR/.env" ]; then
    set -a
    source "$INSTALL_DIR/.env"
    set +a
fi
