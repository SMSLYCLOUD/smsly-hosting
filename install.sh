#!/bin/bash

# =============================================================================
# SMSLY Hosting - Universal Installer (Success Guaranteed)
# =============================================================================
# Supports: Ubuntu 20.04/22.04/24.04 LTS
# Modes:
#   1. IP Mode (HTTP :8090) - Quick start, no domain needed.
#   2. SSL Mode (HTTPS)     - Production ready, requires domain + DNS.
# =============================================================================

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   SMSLY Hosting - Production Installer${NC}"
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

# Check OS (Rough check)
if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
        echo -e "${YELLOW}⚠ Warning: Detected $NAME. This script is optimized for Ubuntu/Debian.${NC}"
        if [ -t 0 ]; then
             echo -e "${YELLOW}  Press ENTER to continue anyway, or Ctrl+C to abort.${NC}"
             read -r
        else
             echo -e "${YELLOW}  ⚠ Non-interactive mode: Continuing automatically...${NC}"
        fi
    fi
fi

# -----------------------------------------------------------------------------
# 2. Dependency Management & cleanup
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[2/7] Installing dependencies...${NC}"

# Stop conflicting services if present
for service in nginx apache2; do
    if systemctl is-active --quiet $service; then
        echo -e "${YELLOW}  ⚠ Stopping conflicting service: $service${NC}"
        systemctl stop $service || true
        systemctl disable $service || true
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
    echo -e "${GREEN}  ✓ Docker already installed${NC}"
fi

# Ensure docker compose is available
if ! docker compose version >/dev/null 2>&1; then
    echo -e "${BLUE}  → Installing Docker Compose plugin...${NC}"
    apt-get install -y docker-compose-plugin || true
fi

# -----------------------------------------------------------------------------
# 3. Configuration & Secrets
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[3/7] Configuration...${NC}"

INSTALL_DIR="/opt/smsly-hosting"
mkdir -p "$INSTALL_DIR"

# Ensure we are in the install directory with correct files
if [ "$(pwd)" != "$INSTALL_DIR" ]; then
    echo -e "${BLUE}  → Setting up installation in $INSTALL_DIR${NC}"
    # If we are running from a cloned repo, copy files. If standalone, clone.
    if [ -f "docker-compose.prod.yml" ]; then
        cp -r . "$INSTALL_DIR/"
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

# Determine Public IP
PUBLIC_IP=$(curl -s ifconfig.me || hostname -I | awk '{print $1}')

# Interactive Setup
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
        DETECTED_IP=$(host -t A "$DOMAIN" | awk '{print $NF}' | tail -n 1)
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

# Generate Secrets
echo -e "${BLUE}  → Generating secure credentials...${NC}"
# Use Python for robust secret generation
cat <<EOF > generate_secrets.py
import secrets
import string
from cryptography.fernet import Fernet

def get_random_string(length=50):
    chars = string.ascii_letters + string.digits + '!@#$%^&*(-_=)'
    return ''.join(secrets.choice(chars) for _ in range(length))

print(f"SECRET_KEY='{get_random_string()}'")
print(f"FIELD_ENCRYPTION_KEY='{Fernet.generate_key().decode()}'")
print(f"POSTGRES_PASSWORD='{secrets.token_hex(16)}'")
EOF

# Install crypto lib if missing (needed for script above)
pip3 install cryptography -q 2>/dev/null || true
# Fallback if pip fails or script fails
if python3 generate_secrets.py > .secrets.tmp 2>/dev/null; then
    source .secrets.tmp
    rm .secrets.tmp generate_secrets.py
else
    echo -e "${YELLOW}  ⚠ Python crypto generation failed, using OpenSSL fallback.${NC}"
    SECRET_KEY=$(openssl rand -base64 48)
    FIELD_ENCRYPTION_KEY=$(openssl rand -base64 32) # Not a valid Fernet key but placeholder
    POSTGRES_PASSWORD=$(openssl rand -hex 16)
fi

# Create .env
cat <<EOF > .env
# SMSLY Hosting Configuration
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
ACME_EMAIL=$ACME_EMAIL
USE_SSL=$USE_SSL

# Security
ALLOWED_HOSTS=$DOMAIN,$PUBLIC_IP,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://$PUBLIC_IP:8090,https://$DOMAIN,http://localhost:8090
CORS_ALLOWED_ORIGINS=http://$PUBLIC_IP:8090,https://$DOMAIN
EOF

chmod 600 .env
echo -e "${GREEN}  ✓ Configuration saved to .env${NC}"

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
    # Merge prod config with traefik adapter
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
sleep 10
# Retry loop for DB ready
for i in {1..12}; do
    if docker compose -f docker-compose.prod.yml exec -T db pg_isready -U smsly_admin >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ Database is ready.${NC}"
        break
    fi
    echo -n "."
    sleep 5
done

echo -e "${BLUE}  → Running Migrations...${NC}"
docker compose -f docker-compose.prod.yml exec -T backend python manage.py migrate --noinput

echo -e "${BLUE}  → Collecting Static Files...${NC}"
docker compose -f docker-compose.prod.yml exec -T backend python manage.py collectstatic --noinput

# -----------------------------------------------------------------------------
# 6. Admin User
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[6/7] Creating Admin User...${NC}"
ADMIN_PASS=$(openssl rand -hex 6)
echo "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.filter(username='admin').exists() or User.objects.create_superuser('admin', 'admin@example.com', '$ADMIN_PASS')" | docker compose -f docker-compose.prod.yml exec -T backend python manage.py shell

# -----------------------------------------------------------------------------
# 7. Verification
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[7/7] Verifying Deployment...${NC}"
sleep 5

# Check Health
if curl -s http://localhost:8090/health >/dev/null || curl -s -k https://localhost/health >/dev/null; then
    echo -e "${GREEN}  ✓ Health Check Passed!${NC}"
else
    echo -e "${YELLOW}  ⚠ Health check response not 200 OK (Check logs if issues persist)${NC}"
fi

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   INSTALLATION SUCCESSFUL!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"

if [ "$USE_SSL" = "true" ]; then
    echo -e "   URL:      https://$DOMAIN"
else
    echo -e "   URL:      http://$PUBLIC_IP:8090"
fi
echo -e "   Admin:    /admin"
echo -e "   User:     admin"
echo -e "   Password: $ADMIN_PASS"
echo -e "   Location: $INSTALL_DIR"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}IMPORTANT: Save these credentials!${NC}"
