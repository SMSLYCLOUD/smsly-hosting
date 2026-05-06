# =============================================================================
# FRESH INSTALL — Full setup from scratch
# =============================================================================

# -----------------------------------------------------------------------------
# 1. Pre-flight Checks
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[1/9] Checking system requirements...${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}✗ Please run as root (sudo bash install.sh)${NC}"
    exit 1
fi

check_internet
check_hardware

# Check OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo -e "${BLUE}  Detected: $NAME $VERSION_ID${NC}"
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
        echo -e "${YELLOW}⚠ Warning: This script is optimized for Ubuntu/Debian.${NC}"
        if [ -e /dev/tty ] && [ "$NON_INTERACTIVE" != "true" ]; then
             echo -e "${YELLOW}  Press ENTER to continue anyway, or Ctrl+C to abort.${NC}"
             read -r < /dev/tty
        else
             echo -e "${YELLOW}  ⚠ Automated mode: Continuing automatically...${NC}"
        fi
    fi
fi

# ─── Disk space check (prevents mid-build OOM / no-space failures) ──────────
DISK_AVAIL_MB=$(df -BM / | tail -1 | awk '{print $4}' | tr -d 'M')
echo -e "${BLUE}  Disk space available: ${DISK_AVAIL_MB}MB${NC}"
if [ "$DISK_AVAIL_MB" -lt 3000 ]; then
    echo -e "${YELLOW}  ⚠ Low disk space (${DISK_AVAIL_MB}MB). Recommended: 3GB+${NC}"
    echo -e "${YELLOW}    Attempting Docker cache cleanup...${NC}"
    docker system prune -f 2>/dev/null || true
    docker builder prune -f 2>/dev/null || true
    DISK_AVAIL_MB=$(df -BM / | tail -1 | awk '{print $4}' | tr -d 'M')
    if [ "$DISK_AVAIL_MB" -lt 1500 ]; then
        echo -e "${RED}  ✗ Insufficient disk space (${DISK_AVAIL_MB}MB). Need at least 1.5GB for fresh install.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ After cleanup: ${DISK_AVAIL_MB}MB available${NC}"
fi

# ─── Git Initialization & Sync ──────────────────────────────────────────────
SMSLY_BRANCH="${SMSLY_BRANCH:-main}"
SMSLY_GIT_REMOTE="${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"

if [ -d "$INSTALL_DIR/.git" ]; then
    echo -e "${BLUE}  → Updating existing repository ($SMSLY_BRANCH)...${NC}"
    cd "$INSTALL_DIR"
    if ! git fetch origin "$SMSLY_BRANCH" >/dev/null 2>&1 || ! git reset --hard "origin/$SMSLY_BRANCH" >/dev/null 2>&1; then
        echo -e "${YELLOW}  ⚠️ Git update failed. If this is a private repo, ensure your token is valid.${NC}"
    fi
else
    echo -e "${BLUE}  → Cloning repository ($SMSLY_BRANCH)...${NC}"
    CLONE_SUCCESS=false
    if [ -d "$INSTALL_DIR" ] && [ "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
        echo -e "${YELLOW}  → Destination not empty. Initializing git...${NC}"
        cd "$INSTALL_DIR"
        git init -q
        git remote add origin "$SMSLY_GIT_REMOTE"
        if git fetch origin "$SMSLY_BRANCH" -q >/dev/null 2>&1 && git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" >/dev/null 2>&1; then
            git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
            CLONE_SUCCESS=true
        fi
    else
        if git clone -b "$SMSLY_BRANCH" "$SMSLY_GIT_REMOTE" "$INSTALL_DIR"; then
            CLONE_SUCCESS=true
        fi
    fi
    
    if [ "$CLONE_SUCCESS" = "false" ]; then
        echo -e "${YELLOW}  ⚠️ Git clone/fetch failed.${NC}"
        if [ -n "${SMSLY_INSTALL_WORKDIR:-}" ] && [ -d "${SMSLY_INSTALL_WORKDIR}" ]; then
            echo -e "${BLUE}  → Fallback: Initializing from pre-uploaded source bundle...${NC}"
            mkdir -p "$INSTALL_DIR"
            cp -rv "${SMSLY_INSTALL_WORKDIR}/"* "$INSTALL_DIR/" 2>/dev/null || true
            cd "$INSTALL_DIR"
            if [ ! -d ".git" ]; then
                git init -q
                git remote add origin "$SMSLY_GIT_REMOTE"
            fi
            echo -e "${GREEN}  ✓ Fallback initialization complete.${NC}"
        fi
    fi
fi

echo -e "${GREEN}  ✓ Pre-flight checks passed${NC}"
set_checkpoint "requirements_checked"

# -----------------------------------------------------------------------------
# 2. Dependency Management & cleanup
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "dependencies_installed"; then
    echo -e "\n${YELLOW}[2/9] Installing dependencies...${NC}"

# Stop conflicting services if present (anything that holds port 80/443)
# NOTE: Don't stop Caddy here — we install/configure it in step 7.
# Stopping it on re-installs breaks the reverse proxy unnecessarily.
for svc in nginx apache2; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo -e "${YELLOW}  ⚠ Stopping conflicting service: $svc${NC}"
        systemctl stop "$svc" || true
        systemctl disable "$svc" || true
    fi
done

# ─── NUCLEAR CLEANUP: Remove ALL stale SMSLY containers, volumes, networks ──
# This prevents: port conflicts, stale DB password volumes, orphan containers
echo -e "${BLUE}  → Cleaning up previous SMSLY installation artifacts...${NC}"

# Stop and remove stale smsly-hosting platform containers (NOT user-deployed services)
SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly-hosting-" -q 2>/dev/null || true)
if [ -n "$SMSLY_CONTAINERS" ]; then
    echo -e "${YELLOW}  → Stopping smsly-hosting platform container(s)...${NC}"
    docker stop $SMSLY_CONTAINERS 2>/dev/null || true
    docker rm -f $SMSLY_CONTAINERS 2>/dev/null || true
fi

# Remove stale Docker volumes (postgres data with old passwords, etc.)
SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly" -q 2>/dev/null || true)
if [ -n "$SMSLY_VOLUMES" ]; then
    if [ "${SMSLY_ALLOW_DESTRUCTIVE_FRESH:-0}" = "1" ]; then
        echo -e "${YELLOW}  → Removing stale SMSLY volumes (SMSLY_ALLOW_DESTRUCTIVE_FRESH=1)...${NC}"
        for vol in $SMSLY_VOLUMES; do
            docker volume rm "$vol" 2>/dev/null || true
        done
    else
        echo -e "${YELLOW}  ⚠ Existing SMSLY volumes detected; preserving data by default.${NC}"
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

echo -e "${GREEN}  ✓ Previous artifacts cleaned${NC}"

apt-get update -qq
apt-get install -y curl wget git python3 python3-pip python3-venv openssl ca-certificates gnupg lsb-release dnsutils

# Install Docker if missing
if ! command -v docker &> /dev/null; then
    echo -e "${BLUE}  → Installing Docker...${NC}"
    mkdir -m 0755 -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
    echo -e "${GREEN}  ✓ Docker already installed ($(docker --version | head -c 40))${NC}"
fi

# Ensure docker compose is available
if ! docker compose version >/dev/null 2>&1; then
    echo -e "${BLUE}  → Installing Docker Compose plugin...${NC}"
    apt-get install -y docker-compose-plugin || true
fi

# Apply mirror config if applicable (Only if docker is now present)
if command -v docker &> /dev/null; then
    configure_docker_mirror
fi

echo -e "${GREEN}  ✓ Dependencies installed${NC}"
    set_checkpoint "dependencies_installed"
fi

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
            cp -rn . "$INSTALL_DIR/" 2>/dev/null || cp -r . "$INSTALL_DIR/"
        fi
    else
        if [ -d "$INSTALL_DIR/.git" ]; then
             echo -e "${BLUE}  → Updating existing repository...${NC}"
             cd "$INSTALL_DIR"
             if [ -n "${SMSLY_GIT_REMOTE:-}" ]; then
                 git remote set-url origin "$SMSLY_GIT_REMOTE" 2>/dev/null || true
             fi
             git fetch origin "$SMSLY_BRANCH" >/dev/null 2>&1 || true
             git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" >/dev/null 2>&1 || true
             git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
        else
             echo -e "${BLUE}  → Cloning repository...${NC}"
             if [ -d "$INSTALL_DIR" ] && [ "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
                 echo -e "${YELLOW}  → Destination not empty. Initializing git and pulling...${NC}"
                 cd "$INSTALL_DIR"
                 git init -q
                 git remote add origin "${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"
                 git fetch origin "$SMSLY_BRANCH" -q >/dev/null 2>&1 || true
                 git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" >/dev/null 2>&1 || true
                 git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
             else
                 git clone -b "$SMSLY_BRANCH" "${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}" "$INSTALL_DIR"
                 cd "$INSTALL_DIR"
                 git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
             fi
        fi
    fi
fi
cd "$INSTALL_DIR"

# ─── Git Initialization (for bundled installs) ──────────────────────────────
if [ ! -d ".git" ] && [ -n "${SMSLY_GIT_REMOTE:-}" ]; then
    echo -e "${BLUE}  -> Initializing Git repository...${NC}"
    git init -q
    git checkout -b "$SMSLY_BRANCH" >/dev/null 2>&1 || true
    git remote add origin "$SMSLY_GIT_REMOTE"
    git fetch origin "$SMSLY_BRANCH" -q --depth=1 || true
    git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
    # We don't reset --hard here to avoid losing the bundled files we just copied,
    # but the repo is now linked for future updates.
    echo -e "${GREEN}  ✓ Git origin set to ${SMSLY_GIT_REMOTE}${NC}"
fi

# ─── BLINDSPOT FIX: Validate required deployment files ──────────────────────
echo -e "${BLUE}  → Validating deployment files...${NC}"
MISSING_FILES=()
for required_file in "$COMPOSE_FILE" "nginx.conf" "backend/Dockerfile" "frontend/Dockerfile" "backend/entrypoint.sh"; do
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
if docker compose ps --format "table {{.Name}}" 2>/dev/null | grep -q "smsly-hosting"; then
    echo -e "${YELLOW}  ⚠ Found containers running from docker-compose.yml (dev). Stopping...${NC}"
    docker compose down 2>/dev/null || true
fi

# ─── IDEMPOTENCY: Skip secret generation if .env already exists ─────────────
if [ -f "$INSTALL_DIR/.env" ]; then
    echo -e "${GREEN}  ✓ Existing .env found — preserving configuration${NC}"
    echo -e "${BLUE}  → Backing up existing .env to .env.backup${NC}"
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
    # ─── Fresh install: generate secrets ────────────────────────────────────
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

    # If any mode was pre-selected (even IP mode), skip prompting even in interactive shells.
    if [ -n "${PRESET_USE_SSL}" ]; then
        if [ "${PRESET_USE_SSL}" = "true" ] && [ -n "${PRESET_DOMAIN}" ] && [ -n "${PRESET_ACME_EMAIL}" ]; then
            echo -e "${BLUE}  → Preset detected. Using SSL Mode for ${PRESET_DOMAIN}.${NC}"
            MODE_CHOICE=2
        elif [ "${PRESET_USE_SSL}" = "false" ]; then
            echo -e "${BLUE}  → Preset detected. Using IP Mode.${NC}"
            MODE_CHOICE=1
        else
            # Pre-seeded but incomplete? Ask anyway.
            if [ -e /dev/tty ]; then
                read -p "Enter choice [1]: " MODE_CHOICE < /dev/tty
                MODE_CHOICE=${MODE_CHOICE:-1}
            else
                MODE_CHOICE=1
            fi
        fi
    elif [ -e /dev/tty ] && [ "$NON_INTERACTIVE" != "true" ]; then
        read -p "Enter choice [1]: " MODE_CHOICE < /dev/tty
        MODE_CHOICE=${MODE_CHOICE:-1}
    else
        echo -e "${YELLOW}  ⚠ Automated mode detected. Defaulting to IP Mode.${NC}"
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

        echo -e "${BLUE}  → Verifying DNS for $DOMAIN...${NC}"
        if command -v host &> /dev/null; then
            DETECTED_IP=$(host -t A "$DOMAIN" 2>/dev/null | awk '{print $NF}' | tail -n 1)
            if [[ "$DETECTED_IP" != "$PUBLIC_IP" && "$DETECTED_IP" != "127.0.0.1" ]]; then
                echo -e "${YELLOW}  ⚠ WARNING: DNS for $DOMAIN ($DETECTED_IP) does not match this server ($PUBLIC_IP).${NC}"
                echo -e "${YELLOW}  SSL generation may fail. Ensure your DNS A record is set.${NC}"
                if [ -e /dev/tty ] && [ "$NON_INTERACTIVE" != "true" ]; then
                    read -p "  Continue anyway? (y/n) " -n 1 -r < /dev/tty
                    echo
                    if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 1; fi
                else
                    echo -e "${YELLOW}  ⚠ Automated mode: Ignoring DNS mismatch and continuing...${NC}"
                fi
            else
                echo -e "${GREEN}  ✓ DNS looks correct.${NC}"
            fi
        fi
    else
        DOMAIN="$PUBLIC_IP"
        echo -e "${BLUE}  → Using IP Mode: $PUBLIC_IP${NC}"
    fi

    # ─── Wildcard Subdomain & Cloudflare Setup (SSL mode only) ────────────
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
            echo -e "${BLUE}  → Preset detected: wildcard=$WILDCARD_SUBDOMAINS${NC}"
        elif [ -e /dev/tty ]; then
            read -p "  Enable wildcard subdomains? (y/n) [n]: " WILDCARD_CHOICE < /dev/tty
            WILDCARD_CHOICE=${WILDCARD_CHOICE:-n}
            if [[ $WILDCARD_CHOICE =~ ^[Yy]$ ]]; then
                WILDCARD_SUBDOMAINS="true"
                while [ -z "$CLOUDFLARE_API_TOKEN" ]; do
                    read -sp "  Enter Cloudflare API Token (DNS:Edit): " CLOUDFLARE_API_TOKEN < /dev/tty
                    echo
                done
                echo -e "${GREEN}  ✓ Wildcard subdomains enabled.${NC}"
            fi
        fi
    fi

    # ─── Generate Secrets (Python-only, NO invalid fallback) ────────────────
    echo -e "${BLUE}  → Generating secure credentials...${NC}"

    # Install cryptography lib (--break-system-packages for Python 3.12+ on Ubuntu 24.04)
    pip3 install cryptography -q --break-system-packages 2>/dev/null || \
        pip3 install cryptography -q 2>/dev/null || true

    # Generate secrets — Python is the ONLY source of truth for Fernet keys
    SECRETS_GENERATED=false
    if python3 -c "
import secrets, string
from cryptography.fernet import Fernet

chars = string.ascii_letters + string.digits
secret_key = ''.join(secrets.choice(chars) for _ in range(50))
fernet_key = Fernet.generate_key().decode()
pg_pass = secrets.token_hex(16)
redis_pass = secrets.token_hex(16)
rabbitmq_pass = secrets.token_hex(16)
gateway_secret = secrets.token_hex(32)
webhook_secret = secrets.token_hex(32)
autoscaler_token = secrets.token_hex(32)
frp_token = secrets.token_hex(32)

# Validate the Fernet key before outputting
Fernet(fernet_key.encode())

print(f'SECRET_KEY={secret_key}')
print(f'FIELD_ENCRYPTION_KEY={fernet_key}')
print(f'POSTGRES_PASSWORD={pg_pass}')
print(f'REDIS_PASSWORD={redis_pass}')
print(f'RABBITMQ_PASSWORD={rabbitmq_pass}')
print(f'GATEWAY_SECRET={gateway_secret}')
print(f'GITHUB_WEBHOOK_SECRET={webhook_secret}')
print(f'AUTOSCALER_API_TOKEN={autoscaler_token}')
print(f'FRP_AUTH_TOKEN={frp_token}')
" > "$INSTALL_DIR/.secrets.tmp" 2>/dev/null; then
        source "$INSTALL_DIR/.secrets.tmp"
        rm -f "$INSTALL_DIR/.secrets.tmp"
        SECRETS_GENERATED=true
        echo -e "${GREEN}  ✓ Secrets generated (Fernet key validated)${NC}"
    fi

    if [ "$SECRETS_GENERATED" != "true" ]; then
        echo -e "${RED}  ✗ CRITICAL: Cannot generate valid Fernet encryption key.${NC}"
        echo -e "${RED}    Install Python 3 and the 'cryptography' package, then re-run.${NC}"
        echo -e "${RED}    pip3 install cryptography${NC}"
        exit 1
    fi

    # Create .env (Atomic)
    local ENV_TMP="$INSTALL_DIR/.env.tmp"
    cat <<EOF > "$ENV_TMP"
# SMSLY Hosting Configuration — Generated $(date -Iseconds)
ENVIRONMENT=production
DEBUG=False
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$FIELD_ENCRYPTION_KEY
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_USER=smsly_admin
POSTGRES_DB=smsly_hosting
DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@pgcat:5432/smsly_hosting

REDIS_PASSWORD=$REDIS_PASSWORD
RABBITMQ_PASSWORD=$RABBITMQ_PASSWORD
REDIS_URL=redis://:$REDIS_PASSWORD@redis:6379/0
CELERY_BROKER_URL=amqp://smsly_user:$RABBITMQ_PASSWORD@rabbitmq:5672//

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

# FRP Tunnel Relay Authentication Token
FRP_AUTH_TOKEN=$FRP_AUTH_TOKEN
EOF

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
        echo "MODE=agent" >> "$ENV_TMP"
        echo "MASTER_IP=$MASTER_IP" >> "$ENV_TMP"
        # Force Agent to use Master VPS for DB/Redis/RabbitMQ
        sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql://smsly_admin:${MASTER_DB_PASSWORD}@${MASTER_IP}:5432/smsly_hosting|" "$ENV_TMP"
        sed -i "s|^CELERY_BROKER_URL=.*|CELERY_BROKER_URL=amqp://smsly_user:${MASTER_MQ_PASSWORD}@${MASTER_IP}:5672//|" "$ENV_TMP"
        sed -i "s|^REDIS_URL=.*|REDIS_URL=redis://${MASTER_IP}:6379/1|" "$ENV_TMP"
        # Disable local DB/Registry requirements in the app
        echo "SMSLY_DISABLE_LOCAL_SERVICES=true" >> "$ENV_TMP"
    else
        echo "MODE=master" >> "$ENV_TMP"
    fi

    # Atomic move and validation
    if validate_env_file "$ENV_TMP"; then
        mv "$ENV_TMP" "$INSTALL_DIR/.env"
        chmod 600 "$INSTALL_DIR/.env"
        echo -e "${GREEN}  ✓ Configuration saved to .env (chmod 600)${NC}"
    else
        echo -e "${RED}  x Generated .env failed validation. Aborting install.${NC}"
        rm -f "$ENV_TMP"
        exit 1
    fi
    set_checkpoint "config_generated"
fi

# -----------------------------------------------------------------------------
# 4. Deployment
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "stack_deployed"; then
    echo -e "\n${YELLOW}[4/9] Deploying Container Stack...${NC}"

# Ensure networks exist
docker network create smsly-net 2>/dev/null || true
docker network create smsly-proxy 2>/dev/null || true

# ─── BLINDSPOT FIX: Ensure entrypoint.sh has execute permissions ────────────
# Windows git can strip +x bits. Fix before building.
#
# NOTE: backend/Dockerfile already runs `chmod +x entrypoint.sh` inside the image.
# Avoid mutating the git working tree on the host (file mode flips can block `git pull`).
#

# Both IP and SSL modes use the same compose stack.
# Caddy (step 7) handles public-facing HTTP/HTTPS termination.
# Traefik is NOT used — Caddy natively handles Let's Encrypt SSL.
# Ensure bind-mounted config paths exist before `docker compose up`.
ensure_infrastructure_permissions
echo -e "${BLUE}  → Starting App Stack...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d --build --force-recreate --remove-orphans
    set_checkpoint "stack_deployed"
fi

# -----------------------------------------------------------------------------
# 5. Database Setup
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "database_initialized"; then
    echo -e "\n${YELLOW}[5/9] Initializing Database...${NC}"

echo -e "${BLUE}  → Waiting for Database...${NC}"
DB_READY=false
for i in $(seq 1 24); do
    if docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U smsly_admin >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ Database is ready (attempt $i).${NC}"
        DB_READY=true
        break
    fi
    printf "."
    sleep 5
done
echo ""

if [ "$DB_READY" != "true" ]; then
    echo -e "${RED}  ✗ Database failed to become ready after 2 minutes.${NC}"
    echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs db${NC}"
    exit 1
fi

# ─── Sync DB password to match .env (handles volume from previous install) ──
# The DB volume persists with the password from FIRST init.
# Always reset the password inside PostgreSQL to match the current .env.
source "$INSTALL_DIR/.env" 2>/dev/null || true
echo -e "${BLUE}  → Syncing database password...${NC}"

# Try local trust auth first (Docker default), then try with PGPASSWORD
if docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U postgres -c "ALTER USER smsly_admin WITH PASSWORD '${POSTGRES_PASSWORD}';" \
    >/dev/null 2>&1; then
    echo -e "${GREEN}  ✓ Database password synced${NC}"
elif docker compose -f "$COMPOSE_FILE" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
    psql -U smsly_admin -d smsly_hosting -c "SELECT 1;" >/dev/null 2>&1; then
    echo -e "${GREEN}  ✓ Database password already matches${NC}"
else
    echo -e "${YELLOW}  ⚠ Password mismatch — resetting via postgres superuser...${NC}"
    # Last resort: the Docker postgres container always accepts local postgres user
    docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U postgres -c "ALTER USER smsly_admin WITH PASSWORD '${POSTGRES_PASSWORD}';" \
        2>&1 || echo -e "${RED}  ✗ Could not sync password. Check pg_hba.conf${NC}"
fi

# ─── Restart backend so it picks up the correct DB credentials ──────────────
echo -e "${BLUE}  → Restarting backend with synced credentials...${NC}"
docker compose -f "$COMPOSE_FILE" restart backend >/dev/null 2>&1
sleep 5

if [ "$RUST_TWIN_MODE" != "true" ]; then
    echo -e "${BLUE}  → Running Migrations...${NC}"
    # Note: Do NOT run makemigrations — migrations are committed in the repo.
    # Running makemigrations generates files inside the container that conflict on redeploy.
    MIGRATE_OK=false
    for attempt in 1 2 3; do
        if docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py migrate --noinput 2>&1; then
            MIGRATE_OK=true
            break
        fi
        WAIT=$((attempt * 10))
        echo -e "${YELLOW}  ⚠ Migration attempt $attempt/3 failed — retrying in ${WAIT}s...${NC}"
        docker compose -f "$COMPOSE_FILE" restart backend >/dev/null 2>&1
        sleep "$WAIT"
    done

    if [ "$MIGRATE_OK" != "true" ]; then
        echo -e "${RED}  ✗ Migrations failed after 3 attempts.${NC}"
        echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs backend${NC}"
        exit 1
    fi
else
    echo -e "${BLUE}  → Rust Twin: Skipping Django manage.py migrations (handled via SeaORM/CLI in future steps)...${NC}"
fi

if [ "$RUST_TWIN_MODE" != "true" ]; then
    echo -e "${BLUE}  → Collecting Static Files...${NC}"
    # Fix volume ownership — Docker creates named volumes as root
    docker compose -f "$COMPOSE_FILE" exec -T --user root backend chown -R 1000:1000 /app/staticfiles /app/media /app/backups 2>/dev/null || true
    docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py collectstatic --noinput

    sync_platform_domain_state "$INSTALL_DIR/.env"
else
    echo -e "${BLUE}  → Rust Twin: Skipping static file collection (handled by Trunk WASM bundler)...${NC}"
fi
    set_checkpoint "database_initialized"
fi

# -----------------------------------------------------------------------------
# 6. Admin User (IDEMPOTENT — skips if admin already exists)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "admin_created"; then
    echo -e "\n${YELLOW}[6/9] Creating Admin User...${NC}"

if [ "$RUST_TWIN_MODE" = "true" ]; then
    echo -e "${BLUE}  → Rust Twin: Skipping Python admin user creation (Use 'docker compose exec cli createsuperuser')...${NC}"
    ADMIN_EXISTS=1
else
    ADMIN_EXISTS=$(echo "from django.contrib.auth import get_user_model; User = get_user_model(); print('1' if User.objects.filter(username='admin').exists() else '0')" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1)
fi

if [ "${ADMIN_EXISTS:-0}" = "1" ]; then
    echo -e "${GREEN}  ✓ Admin user check bypassed or already exists — skipping${NC}"
    if [ -f "$CREDENTIALS_FILE" ]; then
        echo -e "${GREEN}  ✓ Credentials file exists — leaving unchanged${NC}"
    else
        # Best effort: don't overwrite an unknown existing password.
        cat > "$CREDENTIALS_FILE" <<CREDS
# SMSLY Hosting Admin Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE
Username: admin
Password: <existing — not changed by installer>
CREDS
        chmod 600 "$CREDENTIALS_FILE"
    fi
else
    # Production hardening: never ship with a default admin password.
    # Use a shell-safe hex password (avoids quoting issues in manage.py shell).
    if [ "$MODE_AGENT_LITE" = "false" ]; then
        ADMIN_PASS="$(gen_hex_secret 16)"
        echo "
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
User = get_user_model()
admin = User.objects.create_superuser('admin', 'admin@smsly.cloud', '$ADMIN_PASS')
token = Token.objects.create(user=admin)
print(token.key)
" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1 > "$INSTALL_DIR/.token"
        echo -e "${GREEN}  ✓ Admin user created with API Token${NC}"
        chmod 600 "$INSTALL_DIR/.token"

        # ─── Save credentials to secure file (NOT echoed to terminal) ───────────────
        cat > "$CREDENTIALS_FILE" <<CREDS
# SMSLY Hosting Admin Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE
Username: admin
Password: $ADMIN_PASS
CREDS
        chmod 600 "$CREDENTIALS_FILE"

        # -----------------------------------------------------------------------------
        # 6b. Ensure Local Cloud Provider exists (required for deployments)
        # -----------------------------------------------------------------------------
        echo -e "${BLUE}  → Ensuring Local Docker cloud provider exists...${NC}"
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
        echo -e "${GREEN}  ✓ Local Docker cloud provider ready${NC}"
    fi
fi
    set_checkpoint "admin_created"
fi

# -----------------------------------------------------------------------------
# 7. Caddy Reverse Proxy (Public Access)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "caddy_configured"; then
    echo -e "\n${YELLOW}[7/9] Setting up Caddy Reverse Proxy...${NC}"

if [ "$RUST_TWIN_MODE" = "true" ]; then
    echo -e "${BLUE}  → Formatting Rust Twin Caddyfile...${NC}"
    cd rust_twin && export DOMAIN && export ACME_EMAIL && caddy fmt --overwrite Caddyfile 2>/dev/null || true
    cd ..
    # Swap the default Caddyfile path to point to the Rust Twin version
    cp rust_twin/Caddyfile /etc/caddy/Caddyfile 2>/dev/null || true
fi

# ─── Build Caddy with Cloudflare DNS plugin ───────────────────────────────────
# Always build custom Caddy with Cloudflare DNS support, even in IP mode.
# This ensures users can enable SSL + wildcard from the web UI later without SSH.
if caddy list-modules 2>/dev/null | grep -q 'dns.providers.cloudflare'; then
    echo -e "${GREEN}  ✓ Caddy already has cloudflare DNS module${NC}"
elif command -v caddy &> /dev/null; then
    echo -e "${BLUE}  → Caddy found but missing Cloudflare DNS plugin — rebuilding...${NC}"
    _BUILD_CADDY=true
else
    echo -e "${BLUE}  → Installing Caddy with Cloudflare DNS plugin...${NC}"
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
            echo -e "${BLUE}  → Installing Go 1.22 (xcaddy requires Go 1.21+)...${NC}"
            GO_TAR="go1.22.10.linux-amd64.tar.gz"
            curl -fsSL "https://go.dev/dl/$GO_TAR" -o "/tmp/$GO_TAR"
            rm -rf /usr/local/go
            tar -C /usr/local -xzf "/tmp/$GO_TAR"
            rm -f "/tmp/$GO_TAR"
            export PATH="/usr/local/go/bin:$PATH"
            echo -e "${GREEN}  ✓ Go $(go version | awk '{print $3}') installed${NC}"
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
        echo -e "${GREEN}  ✓ Custom Caddy built with Cloudflare DNS plugin${NC}"
    else
        echo -e "${YELLOW}  ⚠ Custom Caddy build failed — trying pre-built download...${NC}"
        # Fallback 1: Download pre-built Caddy with Cloudflare DNS from Caddy's download API
        if curl -fsSL -o /usr/bin/caddy \
            "https://caddyserver.com/api/download?os=linux&arch=amd64&p=github.com/caddy-dns/cloudflare" 2>/dev/null; then
            chmod +x /usr/bin/caddy
            echo -e "${GREEN}  ✓ Pre-built Caddy with Cloudflare DNS downloaded${NC}"
        elif ! command -v caddy &> /dev/null; then
            # Fallback 2: Install stock Caddy from apt (no wildcard SSL, but basic HTTPS works)
            echo -e "${YELLOW}  ⚠ Download also failed — installing stock Caddy (no wildcard SSL)...${NC}"
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

if ! systemctl list-unit-files caddy.service >/dev/null 2>&1; then
    echo -e "${BLUE}  → Installing Caddy systemd service...${NC}"
    # Add a dedicated caddy user and group
    groupadd --system caddy 2>/dev/null || true
    useradd --system --gid caddy --create-home --home-dir /var/lib/caddy \
        --shell /usr/sbin/nologin --comment "Caddy web server" caddy 2>/dev/null || true

    cat > /etc/systemd/system/caddy.service <<'CADDYSRV'
[Unit]
Description=Caddy
Documentation=https://caddyserver.com/docs/
After=network.target network-online.target
Requires=network-online.target

[Service]
Type=exec
User=caddy
Group=caddy
ExecStart=/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
LimitNPROC=512
PrivateTmp=true
ProtectSystem=full
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
CADDYSRV
    systemctl daemon-reload
    echo -e "${GREEN}  ✓ Caddy systemd service installed${NC}"
fi

# ─── Configure Caddyfile ──────────────────────────────────────────────────────
echo -e "${BLUE}  → Configuring Caddyfile...${NC}"
mkdir -p /var/log/caddy
mkdir -p /etc/caddy
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
        # ─── Full wildcard mode: domain + *.domain with Cloudflare DNS ────
        cat > /etc/caddy/Caddyfile <<CADDYEOF
# CloudNeuron Reverse Proxy — Auto-generated
# Domain: $DOMAIN → HTTPS (auto Let's Encrypt)
# Wildcard: *.$DOMAIN → HTTPS (Cloudflare DNS challenge)

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

        echo -e "${GREEN}  ✓ Caddy configured: HTTPS ($DOMAIN) + Wildcard (*.$DOMAIN) + HTTP fallback → 8090${NC}"
    else
        # ─── Standard SSL (no wildcard) ──────────────────────────────────
        cat > /etc/caddy/Caddyfile.tmp <<CADDYEOF
# CloudNeuron Reverse Proxy — Auto-generated
# Domain: $DOMAIN → HTTPS (auto Let's Encrypt)

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
        mv /etc/caddy/Caddyfile.tmp /etc/caddy/Caddyfile
        echo -e "${GREEN}  ✓ Caddy configured: HTTPS ($DOMAIN) + HTTP (:80 fallback) → 8090${NC}"
    fi
else
    cat > /etc/caddy/Caddyfile.tmp <<CADDYEOF
# CloudNeuron Reverse Proxy — Auto-generated
:80 {
    reverse_proxy localhost:8090
}
CADDYEOF
    if [ -f "$CADDY_OVERRIDE_FILE" ]; then
        rm -f "$CADDY_OVERRIDE_FILE"
        rmdir "$CADDY_OVERRIDE_DIR" 2>/dev/null || true
        systemctl daemon-reload
    fi
    mv /etc/caddy/Caddyfile.tmp /etc/caddy/Caddyfile
    echo -e "${GREEN}  ✓ Caddy configured for HTTP: :80 → 8090${NC}"
fi

# ─── Create caddy-config volume directory for Settings UI writes ──────────────
ensure_infrastructure_permissions

# ─── Install caddy-watcher service (picks up UI-driven Caddyfile changes) ─────
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
    echo -e "${GREEN}  ✓ Caddy watcher service installed and running${NC}"
fi

# ─── Install update-watcher service (picks up UI-driven platform updates) ─────
if [ -f "$INSTALL_DIR/scripts/platform-update.sh" ]; then
    chmod +x "$INSTALL_DIR/scripts/platform-update.sh"
    cat > /etc/systemd/system/smsly-update-watcher.service <<UPDATEWATCHEREOF
[Unit]
Description=Platform Update Watcher (SMSLY)
After=network.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/scripts/platform-update.sh /opt/smsly-hosting/caddy-config
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UPDATEWATCHEREOF
    systemctl daemon-reload
    systemctl enable smsly-update-watcher >/dev/null 2>&1
    systemctl restart smsly-update-watcher
    echo -e "${GREEN}  ✓ Platform update watcher service installed and running${NC}"
fi

# Kill non-Caddy/non-Docker processes holding port 80/443 before Caddy binds
for port in 80 443; do
    PID=$(lsof -ti :$port 2>/dev/null || ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
    if [ -n "$PID" ] && [ "$PID" != "0" ]; then
        PNAME=$(ps -p "$PID" -o comm= 2>/dev/null || echo "unknown")
        # Don't kill Caddy or Docker processes
        if [[ "$PNAME" != "caddy" ]] && [[ "$PNAME" != "docker"* ]]; then
            echo -e "${YELLOW}  → Killing $PNAME (PID: $PID) holding port $port${NC}"
            kill -9 $PID 2>/dev/null || true
            sleep 1
        fi
    fi
done

if systemctl is-active --quiet caddy; then
    systemctl reload caddy
else
    systemctl restart caddy
fi
systemctl enable caddy >/dev/null 2>&1

# Verify Caddy is running
sleep 2
if systemctl is-active --quiet caddy; then
    echo -e "${GREEN}  ✓ Caddy reverse proxy active${NC}"
    fi
    
    safe_refresh_runtime_services
    set_checkpoint "caddy_configured"
fi

# -----------------------------------------------------------------------------
# 8. System Memory Hardening (Prevents OOM kills)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "memory_hardened"; then
    echo -e "\n${YELLOW}[8/9] Hardening System Memory...${NC}"

# ─── Swap: Ensure swap is at least 2x RAM ────────────────────────────────────
CURRENT_RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
TARGET_SWAP_MB=$((CURRENT_RAM_MB * 2))
CURRENT_SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')

if [ "$CURRENT_SWAP_MB" -lt "$TARGET_SWAP_MB" ]; then
    NEEDED_MB=$((TARGET_SWAP_MB - CURRENT_SWAP_MB))
    echo -e "${BLUE}  → Current swap: ${CURRENT_SWAP_MB}MB. Adding ${NEEDED_MB}MB to reach 2x RAM (${TARGET_SWAP_MB}MB)...${NC}"
    SWAPFILE="/swapfile-smsly"

    # If the file already exists but is too small, we need to recreate it
    if [ -f "$SWAPFILE" ]; then
        swapoff "$SWAPFILE" 2>/dev/null || true
        rm -f "$SWAPFILE"
        # Since we removed the old file, we need to create the full target amount
        NEEDED_MB=$TARGET_SWAP_MB
    fi

    fallocate -l ${NEEDED_MB}M "$SWAPFILE" 2>/dev/null || dd if=/dev/zero of="$SWAPFILE" bs=1M count=$NEEDED_MB status=none
    chmod 600 "$SWAPFILE"
    mkswap "$SWAPFILE" >/dev/null 2>&1
    swapon "$SWAPFILE" 2>/dev/null || true
    # Make permanent (idempotent)
    if ! grep -q "$SWAPFILE" /etc/fstab 2>/dev/null; then
        echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
    fi
    echo -e "${GREEN}  ✓ Swap file created and activated (${NEEDED_MB}MB)${NC}"
else
    echo -e "${GREEN}  ✓ Swap already sufficient (${CURRENT_SWAP_MB}MB, >= 2x RAM)${NC}"
fi

# ─── Auto-Maintenance: Install OOM Swap Adjuster ─────────────────────────────
OOM_SCRIPT="/opt/smsly/scripts/oom-swap-adjuster.sh"
mkdir -p /opt/smsly/scripts
cat << 'EOF' > "$OOM_SCRIPT"
#!/usr/bin/env bash
# oom-swap-adjuster.sh
#
# Monitors the system for Out Of Memory (OOM) kills. If one is detected within the last
# X minutes, it automatically increases the swap space by 200MB up to a maximum of 4x RAM.
# This serves as an auto-maintenance feature to prevent recurring build crashes.

set -euo pipefail

LOG_FILE="/var/log/smsly-oom-adjuster.log"
MINUTES_BACK=10
SWAPFILE_PREFIX="/swapfile-smsly-auto"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Check for OOM events in the last N minutes using journalctl
OOM_COUNT=$(journalctl -k --since "${MINUTES_BACK} minutes ago" | grep -i "out of memory" | wc -l || true)

if [ "$OOM_COUNT" -eq 0 ]; then
    # No OOM detected recently, exit quietly.
    exit 0
fi

log "Detected $OOM_COUNT OOM events in the last $MINUTES_BACK minutes. Evaluating swap size."

# Get RAM size in MB
RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
CURRENT_SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')

# Maximum allowed swap is 4x RAM
MAX_SWAP_MB=$((RAM_MB * 4))

if [ "$CURRENT_SWAP_MB" -ge "$MAX_SWAP_MB" ]; then
    log "Swap is already at or above the maximum allowed limit (4x RAM = ${MAX_SWAP_MB}MB). No further auto-adjustment will be made."
    exit 0
fi

# Calculate new swap chunk to add (200MB)
ADD_SWAP_MB=200
NEW_TOTAL_MB=$((CURRENT_SWAP_MB + ADD_SWAP_MB))

# Cap at max if we would overshoot
if [ "$NEW_TOTAL_MB" -gt "$MAX_SWAP_MB" ]; then
    ADD_SWAP_MB=$((MAX_SWAP_MB - CURRENT_SWAP_MB))
    NEW_TOTAL_MB=$MAX_SWAP_MB
fi

if [ "$ADD_SWAP_MB" -le 0 ]; then
    exit 0
fi

NEW_SWAPFILE="${SWAPFILE_PREFIX}-$(date '+%s')"
log "Increasing swap by ${ADD_SWAP_MB}MB. Creating ${NEW_SWAPFILE}..."

# Create the new swap file
if fallocate -l ${ADD_SWAP_MB}M "$NEW_SWAPFILE" 2>/dev/null; then
    chmod 600 "$NEW_SWAPFILE"
    mkswap "$NEW_SWAPFILE" >/dev/null 2>&1
    swapon "$NEW_SWAPFILE" 2>/dev/null || true

    # Make it permanent
    if ! grep -q "$NEW_SWAPFILE" /etc/fstab 2>/dev/null; then
        echo "$NEW_SWAPFILE none swap sw 0 0" >> /etc/fstab
    fi

    log "Successfully added ${ADD_SWAP_MB}MB of swap. Total swap is now approx ${NEW_TOTAL_MB}MB."
else
    # Fallback to dd if fallocate fails (e.g. some filesystems don't support it)
    log "fallocate failed, trying dd..."
    if dd if=/dev/zero of="$NEW_SWAPFILE" bs=1M count=$ADD_SWAP_MB status=none; then
        chmod 600 "$NEW_SWAPFILE"
        mkswap "$NEW_SWAPFILE" >/dev/null 2>&1
        swapon "$NEW_SWAPFILE" 2>/dev/null || true

        if ! grep -q "$NEW_SWAPFILE" /etc/fstab 2>/dev/null; then
            echo "$NEW_SWAPFILE none swap sw 0 0" >> /etc/fstab
        fi

        log "Successfully added ${ADD_SWAP_MB}MB of swap via dd. Total swap is now approx ${NEW_TOTAL_MB}MB."
    else
        log "Failed to create swap file."
        rm -f "$NEW_SWAPFILE"
        exit 1
    fi
fi
EOF
chmod +x "$OOM_SCRIPT"

# Add cron job to run the script every 5 minutes
CRON_JOB="*/5 * * * * root $OOM_SCRIPT"
if ! grep -q "$OOM_SCRIPT" /etc/crontab 2>/dev/null; then
    echo "$CRON_JOB" >> /etc/crontab
    echo -e "${GREEN}  ✓ OOM Auto-Adjuster installed and scheduled via cron${NC}"
else
    echo -e "${GREEN}  ✓ OOM Auto-Adjuster already scheduled${NC}"
fi

# ─── Sysctl tuning (idempotent) ──────────────────────────────────────────────
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
        echo -e "${GREEN}  ✓ $key = $value ($desc)${NC}"
    fi
}

ensure_sysctl "vm.overcommit_memory" "1" "Redis background save fix"
ensure_sysctl "vm.swappiness" "10" "Prefer RAM over swap"
ensure_sysctl "net.core.somaxconn" "511" "Redis connection backlog"
# Security Hardening
ensure_sysctl "net.ipv4.conf.all.rp_filter" "1" "IP spoofing protection"
ensure_sysctl "net.ipv4.conf.default.rp_filter" "1" "IP spoofing protection"
ensure_sysctl "net.ipv4.icmp_echo_ignore_broadcasts" "1" "ICMP flood protection"
ensure_sysctl "net.ipv4.conf.all.accept_source_route" "0" "Disable source routing"
ensure_sysctl "net.ipv4.tcp_syncookies" "1" "SYN flood protection"

if [ "$SYSCTL_UPDATED" = "false" ]; then
    echo -e "${GREEN}  ✓ Sysctl settings already optimal${NC}"
fi

# ─── OOM Protection for critical containers ──────────────────────────────────
echo -e "${BLUE}  → Setting OOM protection for critical containers...${NC}"
for CONTAINER in smsly-hosting-nginx-1 smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgcat-1; do
    CPID=$(docker inspect --format '{{.State.Pid}}' "$CONTAINER" 2>/dev/null || echo "")
    if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
        echo -500 > "/proc/$CPID/oom_score_adj" 2>/dev/null || true
    fi
done
echo -e "${GREEN}  ✓ OOM protection set (nginx, backend, db, pgcat)${NC}"

# ─── Firewall Hardening (UFW) ────────────────────────────────────────────────
if command -v ufw >/dev/null 2>&1; then
    echo -e "${BLUE}  → Configuring UFW firewall...${NC}"
    ufw default deny incoming >/dev/null 2>&1 || true
    ufw default allow outgoing >/dev/null 2>&1 || true
    ufw allow ssh >/dev/null 2>&1 || true
    ufw allow 80/tcp >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    # Allow FRP if active
    if [ -f "$INSTALL_DIR/.env" ] && grep -q "FRP_AUTH_TOKEN" "$INSTALL_DIR/.env"; then
        ufw allow 7000/tcp >/dev/null 2>&1 || true
    fi
    # Allow Docker Mirror (Option B) if this is the Master/Leader
    if [ -z "${MASTER_IP:-}" ] || [ "$MASTER_IP" = "127.0.0.1" ] || [ "$MASTER_IP" = "$(detect_public_ip)" ]; then
        ufw allow 5001/tcp >/dev/null 2>&1 || true
    fi
    echo "y" | ufw enable >/dev/null 2>&1 || true
    echo -e "${GREEN}  ✓ Firewall hardened (Inbound blocked, SSH/Web permitted)${NC}"
fi

echo -e "${GREEN}  ✓ System security hardening complete${NC}"
    set_checkpoint "memory_hardened"
fi

# -----------------------------------------------------------------------------
# 9. Verification
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[9/9] Verifying Deployment...${NC}"
VERIFY_PASS_COUNT=0
VERIFY_TOTAL=5
sleep 5

# ─── Check 1: Verify nginx loaded custom config (not default) ──────────────
echo -e "${BLUE}  → [1/5] Verifying nginx configuration...${NC}"
NGINX_CONFIG_CHECK=$(docker exec smsly-hosting-nginx-1 head -1 /etc/nginx/nginx.conf 2>/dev/null || echo "FAIL")
if echo "$NGINX_CONFIG_CHECK" | grep -q "events"; then
    echo -e "${GREEN}  ✓ Nginx config verified (custom proxy config loaded)${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  ⚠ Nginx may have default config — force-recreating...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps nginx
    docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true
    sleep 3
    NGINX_CONFIG_CHECK=$(docker exec smsly-hosting-nginx-1 head -1 /etc/nginx/nginx.conf 2>/dev/null || echo "FAIL")
    if echo "$NGINX_CONFIG_CHECK" | grep -q "events"; then
        echo -e "${GREEN}  ✓ Nginx config fixed after force-recreate${NC}"
        VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Nginx config still incorrect. Manual fix needed.${NC}"
    fi
fi

# ─── Check 2: Health check ─────────────────────────────────────────────────
echo -e "${BLUE}  → [2/5] Running health check...${NC}"
HEALTH_OK=false
# ZH-012 HARDENING: Increased from 12 (1m) to 36 attempts (3m) for slow VPS I/O
MAX_ATTEMPTS=36
for attempt in $(seq 1 $MAX_ATTEMPTS); do
    if curl -sfL http://127.0.0.1/health >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    fi
    echo -ne "\r${YELLOW}  → Health check attempt $attempt/$MAX_ATTEMPTS — waiting...${NC}"
    if [ "$attempt" -eq 5 ]; then
        echo -e "\n${BLUE}  → Restarting Nginx to ensure upstream binding...${NC}"
        docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true
    fi
    sleep 5
done
echo ""

if [ "$HEALTH_OK" = "true" ]; then
    echo -e "${GREEN}  ✓ Health Check Passed!${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Health check failed after $MAX_ATTEMPTS attempts.${NC}"
    dump_diagnostic_logs
fi

# ─── Check 3: All containers running ──────────────────────────────────────
echo -e "${BLUE}  → [3/5] Checking container status...${NC}"
RUNNING_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --status running -q 2>/dev/null | wc -l)
TOTAL_COUNT=$(docker compose -f "$COMPOSE_FILE" ps -q 2>/dev/null | wc -l)
if [ "$RUNNING_COUNT" -eq "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt 0 ]; then
    echo -e "${GREEN}  ✓ All $TOTAL_COUNT containers running${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Only $RUNNING_COUNT/$TOTAL_COUNT containers running${NC}"
fi

# ─── Check 4: Swap is sufficient ──────────────────────────────────────────
echo -e "${BLUE}  → [4/5] Checking swap...${NC}"
SWAP_TOTAL=$(free -m | awk '/^Swap:/{print $2}')
if [ "$SWAP_TOTAL" -ge 1500 ]; then
    echo -e "${GREEN}  ✓ Swap sufficient (${SWAP_TOTAL}MB)${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  ⚠ Swap low (${SWAP_TOTAL}MB) — recommend 2GB+${NC}"
fi

# ─── Check 5: Caddy running ───────────────────────────────────────────────
echo -e "${BLUE}  → [5/5] Checking Caddy...${NC}"
if systemctl is-active --quiet caddy 2>/dev/null; then
    echo -e "${GREEN}  ✓ Caddy reverse proxy active${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Caddy is not running${NC}"
fi

# Show container status
echo -e "\n${BLUE}Container Status:${NC}"
docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
    docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true

echo -e "\n${BLUE}Verification Score: $VERIFY_PASS_COUNT/$VERIFY_TOTAL${NC}"

# ─── Install Autoscaler as systemd service ──────────────────────────────────
echo -e "${BLUE}  → Installing smsly-autoscaler systemd service...${NC}"
cp "$INSTALL_DIR/scripts/smsly-autoscaler.py" /opt/smsly/autoscaler.py 2>/dev/null || {
    mkdir -p /opt/smsly
    cp "$INSTALL_DIR/scripts/smsly-autoscaler.py" /opt/smsly/autoscaler.py
}
chmod +x /opt/smsly/autoscaler.py

# Source .env for the token
AUTOSCALER_API_TOKEN="$(env_get_value "$INSTALL_DIR/.env" "AUTOSCALER_API_TOKEN")"

cat <<SVCEOF > /etc/systemd/system/smsly-autoscaler.service
[Unit]
Description=SMSLY VPS Autoscaler — Cross-Service Resource Manager
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
echo -e "${GREEN}  ✓ smsly-autoscaler service installed and started${NC}"

# -----------------------------------------------------------------------------
# 10. CLI Integration
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[10/10] Integrating SMSLY CLI...${NC}"

if [ -d "$INSTALL_DIR/cli" ]; then
    echo -e "${BLUE}  → Installing 'smsly' CLI command globally...${NC}"
    # Use --break-system-packages for modern Python (Ubuntu 24.04+)
    pip3 install -q --break-system-packages "$INSTALL_DIR/cli" 2>/dev/null || \
        pip3 install -q "$INSTALL_DIR/cli" 2>/dev/null || true
    
    # Ensure binary is in path (pip usually puts it in /usr/local/bin)
    if command -v smsly &> /dev/null; then
        echo -e "${GREEN}  ✓ CLI installed: run 'smsly login' or 'smsly --help'${NC}"
        
        # Auto-configuration for local host
        if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ]; then
            URL_SCHEME="https" && [ "$USE_SSL" != "true" ] && URL_SCHEME="http"
            API_URL="${URL_SCHEME}://${DOMAIN}"
        else
            API_URL="http://127.0.0.1:8090"
        fi
        
        # Best effort: don't auto-login yet (token is in creds file), 
        # but let the user know their URL is pre-linked.
        echo -e "${BLUE}  → Your local API URL: $API_URL${NC}"
    else
        echo -e "${YELLOW}  ⚠ CLI installation partially failed (could not find 'smsly' in PATH).${NC}"
    fi
else
    echo -e "${YELLOW}  ⚠ CLI directory not found — skipping integration.${NC}"
fi

# ─── Final Verification Sync ──────────────────────────────────────────────────
if command -v smsly &> /dev/null; then
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    VERIFY_TOTAL=$((VERIFY_TOTAL + 1))
fi

# ─── Remove rollback trap (installation succeeded) ─────────────────────────
trap - EXIT

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   ✓ INSTALLATION SUCCESSFUL!${NC}"
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
echo -e "   CLI:         'smsly services list'${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  View credentials:   cat $CREDENTIALS_FILE${NC}"
echo -e "${YELLOW}  View logs:          cat $LOG_FILE${NC}"
echo -e "${YELLOW}  Update frontend:    sudo bash install.sh --update-frontend${NC}"
echo -e "${YELLOW}  Update backend:     sudo bash install.sh --update-backend${NC}"
echo -e "${YELLOW}  Full update:        sudo bash install.sh --update${NC}"
echo -e "${YELLOW}  Runtime refresh:    sudo bash install.sh --refresh${NC}"
echo -e "${YELLOW}  Runtime recovery:   sudo bash install.sh --recover${NC}"
echo -e "${YELLOW}  Debug snapshot:     sudo bash install.sh --debug${NC}"
echo -e "${YELLOW}  Wipe install:       sudo bash install.sh --wipe${NC}"

# ─── Conditional Auto-Reboot (only if ALL checks passed) ────────────────────
if [ "$VERIFY_PASS_COUNT" -eq "$VERIFY_TOTAL" ]; then
    echo -e "\n${GREEN}  ✓ All $VERIFY_TOTAL/$VERIFY_TOTAL verification checks passed.${NC}"
    # Normalize NON_INTERACTIVE to true/false for easier shell testing
    _IS_NON_INTERACTIVE=false
    if [[ "${NON_INTERACTIVE:-}" =~ ^(1|true|yes)$ ]]; then _IS_NON_INTERACTIVE=true; fi

    if [ -e /dev/tty ] && [ -z "${SKIP_REBOOT:-}" ] && [ "$_IS_NON_INTERACTIVE" != "true" ]; then
        echo -e "${YELLOW}  System will reboot in 30 seconds to apply sysctl changes.${NC}"
        echo -e "${YELLOW}  Press Ctrl+C to cancel, or wait...${NC}"
        for i in $(seq 30 -1 1); do
            printf "\r${YELLOW}  Rebooting in %2d seconds... ${NC}" "$i"
            sleep 1
        done
        echo -e "\n${BLUE}  → Rebooting now...${NC}"
        reboot
    else
        echo -e "${YELLOW}  Non-interactive mode — skipping auto-reboot.${NC}"
        echo -e "${YELLOW}  Run 'sudo reboot' manually to apply sysctl changes.${NC}"
    fi
else
    echo -e "\n${RED}  ⚠ Only $VERIFY_PASS_COUNT/$VERIFY_TOTAL checks passed — skipping auto-reboot.${NC}"
    echo -e "${YELLOW}  Fix the failed checks above, then run: sudo reboot${NC}"
    if [ "${SMSLY_STRICT_VERIFY:-0}" = "1" ]; then
        echo -e "${RED}  ✗ Strict verification is enabled; failing installation.${NC}"
        exit 1
    fi
fi
