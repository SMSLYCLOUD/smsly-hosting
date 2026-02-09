#!/bin/bash

# =============================================================================
# SMSLY Hosting - Universal Installer v2.0 (Production Hardened)
# =============================================================================
# Supports: Ubuntu 20.04/22.04/24.04 LTS
# Modes:
#   1. IP Mode (HTTP :8090) - Quick start, no domain needed.
#   2. SSL Mode (HTTPS)     - Production ready, requires domain + DNS.
#
# Features:
#   - Idempotent: safe to re-run without data loss
#   - Full installation logging to /var/log/smsly-install.log
#   - Rollback on failure via trap handler
#   - Secure credential storage (no plaintext to terminal)
# =============================================================================

set -euo pipefail

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_FILE="/var/log/smsly-install.log"
INSTALL_DIR="/opt/smsly-hosting"
CREDENTIALS_FILE="$INSTALL_DIR/.credentials"
ROLLBACK_NEEDED=false

# Log all output to file AND terminal
exec > >(tee -a "$LOG_FILE") 2>&1
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  SMSLY Hosting Install Log — $(date -Iseconds)"
echo "═══════════════════════════════════════════════════════════════"

# ─── Rollback Trap ──────────────────────────────────────────────────────────
cleanup_on_failure() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo -e "\n${RED}════════════════════════════════════════════════════════════${NC}"
        echo -e "${RED}  INSTALLATION FAILED (exit code: $exit_code)${NC}"
        echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
        echo -e "${YELLOW}  → Rolling back...${NC}"

        # Stop any containers that were started
        if [ -f "$INSTALL_DIR/docker-compose.prod.yml" ]; then
            cd "$INSTALL_DIR" 2>/dev/null || true
            docker compose -f docker-compose.prod.yml down 2>/dev/null || true
        fi

        # Restore backup .env if one was created
        if [ -f "$INSTALL_DIR/.env.backup" ]; then
            echo -e "${YELLOW}  → Restoring previous .env from backup${NC}"
            mv "$INSTALL_DIR/.env.backup" "$INSTALL_DIR/.env" 2>/dev/null || true
        fi

        echo -e "${YELLOW}  Full log: $LOG_FILE${NC}"
        echo -e "${RED}  Please review the log and re-run the installer.${NC}"
    fi
}
trap cleanup_on_failure EXIT

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   SMSLY Hosting - Production Installer v2.0${NC}"
echo -e "${BLUE}   Target: Ubuntu LTS (Fresh Install Recommended)${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}\n"

# -----------------------------------------------------------------------------
# 1. Pre-flight Checks
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[1/7] Checking system requirements...${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}✗ Please run as root (sudo bash install.sh)${NC}"
    exit 1
fi

# Check OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo -e "${BLUE}  Detected: $NAME $VERSION_ID${NC}"
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
        echo -e "${YELLOW}⚠ Warning: This script is optimized for Ubuntu/Debian.${NC}"
        if [ -t 0 ]; then
             echo -e "${YELLOW}  Press ENTER to continue anyway, or Ctrl+C to abort.${NC}"
             read -r
        else
             echo -e "${YELLOW}  ⚠ Non-interactive mode: Continuing automatically...${NC}"
        fi
    fi
fi
echo -e "${GREEN}  ✓ Pre-flight checks passed${NC}"

# -----------------------------------------------------------------------------
# 2. Dependency Management & cleanup
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[2/7] Installing dependencies...${NC}"

# Stop conflicting services if present
for svc in nginx apache2; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo -e "${YELLOW}  ⚠ Stopping conflicting service: $svc${NC}"
        systemctl stop "$svc" || true
        systemctl disable "$svc" || true
    fi
done

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
echo -e "${GREEN}  ✓ Dependencies installed${NC}"

# -----------------------------------------------------------------------------
# 3. Configuration & Secrets (IDEMPOTENT)
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[3/7] Configuration...${NC}"

mkdir -p "$INSTALL_DIR"

# Ensure we are in the install directory with correct files
if [ "$(pwd)" != "$INSTALL_DIR" ]; then
    echo -e "${BLUE}  → Setting up installation in $INSTALL_DIR${NC}"
    if [ -f "docker-compose.prod.yml" ]; then
        cp -rn . "$INSTALL_DIR/" 2>/dev/null || cp -r . "$INSTALL_DIR/"
    else
        if [ -d "$INSTALL_DIR/.git" ]; then
             cd "$INSTALL_DIR"
             git pull origin main
        else
             git clone https://github.com/SMSLYCLOUD/smsly-hosting.git "$INSTALL_DIR"
        fi
    fi
fi
cd "$INSTALL_DIR"

# ─── IDEMPOTENCY: Skip secret generation if .env already exists ─────────────
if [ -f "$INSTALL_DIR/.env" ]; then
    echo -e "${GREEN}  ✓ Existing .env found — preserving configuration${NC}"
    echo -e "${BLUE}  → Backing up existing .env to .env.backup${NC}"
    cp "$INSTALL_DIR/.env" "$INSTALL_DIR/.env.backup"

    # Source existing values for the summary at the end
    source "$INSTALL_DIR/.env" 2>/dev/null || true
    DOMAIN="${DOMAIN:-localhost}"
    USE_SSL="${USE_SSL:-false}"
    PUBLIC_IP=$(curl -s -m 5 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
else
    # ─── Fresh install: generate secrets ────────────────────────────────────
    PUBLIC_IP=$(curl -s -m 5 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')

    echo -e "\n${BLUE}Select Deployment Mode:${NC}"
    echo -e "  1) ${GREEN}IP Mode${NC} (Easy) - http://$PUBLIC_IP:8090"
    echo -e "  2) ${GREEN}SSL Mode${NC} (Prod) - https://your-domain.com (Requires DNS A Record pointing to $PUBLIC_IP)"

    if [ -t 0 ]; then
        read -p "Enter choice [1]: " MODE_CHOICE
        MODE_CHOICE=${MODE_CHOICE:-1}
    else
        echo -e "${YELLOW}  ⚠ Non-interactive mode detected. Defaulting to IP Mode.${NC}"
        MODE_CHOICE=1
    fi

    DOMAIN=""
    ACME_EMAIL=""
    USE_SSL="false"

    if [ "$MODE_CHOICE" -eq "2" ]; then
        USE_SSL="true"
        while [ -z "$DOMAIN" ]; do
            read -p "Enter your Domain (e.g., app.example.com): " DOMAIN
        done

        while [ -z "$ACME_EMAIL" ]; do
            read -p "Enter Email for SSL (e.g., admin@example.com): " ACME_EMAIL
        done

        echo -e "${BLUE}  → Verifying DNS for $DOMAIN...${NC}"
        if command -v host &> /dev/null; then
            DETECTED_IP=$(host -t A "$DOMAIN" 2>/dev/null | awk '{print $NF}' | tail -n 1)
            if [[ "$DETECTED_IP" != "$PUBLIC_IP" && "$DETECTED_IP" != "127.0.0.1" ]]; then
                echo -e "${YELLOW}  ⚠ WARNING: DNS for $DOMAIN ($DETECTED_IP) does not match this server ($PUBLIC_IP).${NC}"
                echo -e "${YELLOW}  SSL generation may fail. Ensure your DNS A record is set.${NC}"
                read -p "  Continue anyway? (y/n) " -n 1 -r
                echo
                if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 1; fi
            else
                echo -e "${GREEN}  ✓ DNS looks correct.${NC}"
            fi
        fi
    else
        DOMAIN="$PUBLIC_IP"
        echo -e "${BLUE}  → Using IP Mode: $PUBLIC_IP${NC}"
    fi

    # ─── Generate Secrets (Python-only, NO invalid fallback) ────────────────
    echo -e "${BLUE}  → Generating secure credentials...${NC}"

    # Install cryptography lib
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

# Validate the Fernet key before outputting
Fernet(fernet_key.encode())

print(f'SECRET_KEY={secret_key}')
print(f'FIELD_ENCRYPTION_KEY={fernet_key}')
print(f'POSTGRES_PASSWORD={pg_pass}')
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

    # Create .env
    cat <<EOF > "$INSTALL_DIR/.env"
# SMSLY Hosting Configuration — Generated $(date -Iseconds)
ENVIRONMENT=production
DEBUG=False
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$FIELD_ENCRYPTION_KEY
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_USER=smsly_admin
POSTGRES_DB=smsly_hosting
DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@db:5432/smsly_hosting

REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0

DOMAIN=$DOMAIN
ACME_EMAIL=${ACME_EMAIL:-}
USE_SSL=$USE_SSL

# Security
ALLOWED_HOSTS=$DOMAIN,$PUBLIC_IP,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://$PUBLIC_IP:8090,https://$DOMAIN,http://localhost:8090
CORS_ALLOWED_ORIGINS=http://$PUBLIC_IP:8090,https://$DOMAIN
EOF

    chmod 600 "$INSTALL_DIR/.env"
    echo -e "${GREEN}  ✓ Configuration saved to .env (chmod 600)${NC}"
fi

# -----------------------------------------------------------------------------
# 4. Deployment
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[4/7] Deploying Container Stack...${NC}"

# Ensure networks exist
docker network create smsly-net 2>/dev/null || true
docker network create smsly-proxy 2>/dev/null || true

if [ "$USE_SSL" = "true" ]; then
    echo -e "${BLUE}  → Starting Traefik (SSL Proxy)...${NC}"
    docker compose -f docker-compose.traefik.yml up -d

    echo -e "${BLUE}  → Starting App Stack (Attached to Proxy)...${NC}"
    docker compose -f docker-compose.prod.yml -f docker-compose.traefik-adapter.yml up -d --build
else
    echo -e "${BLUE}  → Starting App Stack (Standard)...${NC}"
    docker compose -f docker-compose.prod.yml up -d --build
fi

# -----------------------------------------------------------------------------
# 5. Database Setup
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[5/7] Initializing Database...${NC}"

echo -e "${BLUE}  → Waiting for Database...${NC}"
DB_READY=false
for i in $(seq 1 24); do
    if docker compose -f docker-compose.prod.yml exec -T db pg_isready -U smsly_admin >/dev/null 2>&1; then
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
    echo -e "${YELLOW}  Check: docker compose -f docker-compose.prod.yml logs db${NC}"
    exit 1
fi

echo -e "${BLUE}  → Running Migrations...${NC}"
docker compose -f docker-compose.prod.yml exec -T backend python manage.py migrate --noinput

echo -e "${BLUE}  → Collecting Static Files...${NC}"
docker compose -f docker-compose.prod.yml exec -T backend python manage.py collectstatic --noinput

# -----------------------------------------------------------------------------
# 6. Admin User (IDEMPOTENT — skips if admin already exists)
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[6/7] Creating Admin User...${NC}"
ADMIN_PASS=$(openssl rand -hex 8)
ADMIN_CREATED=$(echo "from django.contrib.auth import get_user_model; User = get_user_model(); existed = User.objects.filter(username='admin').exists(); print('EXISTS' if existed else 'CREATED'); existed or User.objects.create_superuser('admin', 'admin@smsly.cloud', '$ADMIN_PASS')" | docker compose -f docker-compose.prod.yml exec -T backend python manage.py shell 2>/dev/null | tail -1)

if [[ "$ADMIN_CREATED" == *"EXISTS"* ]]; then
    echo -e "${GREEN}  ✓ Admin user already exists — skipping${NC}"
    ADMIN_PASS="<unchanged — see previous install>"
else
    echo -e "${GREEN}  ✓ Admin user created${NC}"
fi

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
# 7. Verification
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[7/7] Verifying Deployment...${NC}"
sleep 5

HEALTH_OK=false
for attempt in 1 2 3; do
    if curl -sf http://localhost:8090/health >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    elif curl -sf -k https://localhost/health >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    fi
    sleep 3
done

if [ "$HEALTH_OK" = "true" ]; then
    echo -e "${GREEN}  ✓ Health Check Passed!${NC}"
else
    echo -e "${YELLOW}  ⚠ Health check did not respond — services may still be starting.${NC}"
    echo -e "${YELLOW}    Check: docker compose -f docker-compose.prod.yml ps${NC}"
fi

# Show container status
docker compose -f docker-compose.prod.yml ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || true

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
    echo -e "   URL:         http://$PUBLIC_IP:8090"
fi
echo -e "   Admin:       /admin"
echo -e "   Credentials: $CREDENTIALS_FILE"
echo -e "   Install Log: $LOG_FILE"
echo -e "   Location:    $INSTALL_DIR"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  View credentials: cat $CREDENTIALS_FILE${NC}"
echo -e "${YELLOW}  View logs:        cat $LOG_FILE${NC}"
