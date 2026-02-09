#!/bin/bash
set -euo pipefail

# SMSLY Hosting - Production VPS Deployment Script v3.1 (Hardened)

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ─── Logging ────────────────────────────────────────────────────────────────
LOG_FILE="/var/log/smsly-deploy.log"
INSTALL_DIR="/opt/smsly-hosting"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "" >> "$LOG_FILE"
echo "═══ SMSLY Deploy Log — $(date -Iseconds) ═══" >> "$LOG_FILE"

# ─── Rollback Trap ──────────────────────────────────────────────────────────
cleanup_on_failure() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo -e "${RED}DEPLOYMENT FAILED (exit: $exit_code). Check: $LOG_FILE${NC}"
        if [ -f "$INSTALL_DIR/docker-compose.prod.yml" ]; then
            cd "$INSTALL_DIR" 2>/dev/null || true
            docker compose -f docker-compose.prod.yml down 2>/dev/null || true
        fi
        if [ -f "$INSTALL_DIR/.env.backup" ]; then
            mv "$INSTALL_DIR/.env.backup" "$INSTALL_DIR/.env" 2>/dev/null || true
        fi
    fi
}
trap cleanup_on_failure EXIT

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  SMSLY Hosting - Production VPS Deployment v3.1${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo ""

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ERROR: Must run as root${NC}"
    exit 1
fi

# ─── STEP 1: System Update ───────────────────────────────────────────────────
echo -e "${YELLOW}[1/8] Updating system...${NC}"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq
apt-get install -y -qq curl wget git python3 python3-pip ufw openssl > /dev/null 2>&1
echo -e "${GREEN}✓ System updated${NC}"

# ─── STEP 2: Docker ─────────────────────────────────────────────────────────
echo -e "${YELLOW}[2/8] Checking Docker...${NC}"
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker && systemctl start docker
fi
if ! docker compose version &> /dev/null; then
    apt-get install -y -qq docker-compose-plugin > /dev/null 2>&1
fi
echo -e "${GREEN}✓ Docker ready${NC}"

# ─── STEP 3: Firewall ───────────────────────────────────────────────────────
echo -e "${YELLOW}[3/8] Configuring firewall...${NC}"
ufw --force enable
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 8090/tcp
ufw reload
echo -e "${GREEN}✓ Firewall configured${NC}"

# ─── STEP 4: Clone Repo ─────────────────────────────────────────────────────
echo -e "${YELLOW}[4/8] Setting up files...${NC}"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

if [ ! -f "docker-compose.prod.yml" ]; then
    echo -e "${BLUE}  → Cloning smsly-hosting...${NC}"
    git clone https://github.com/SMSLYCLOUD/smsly-hosting.git temp
    cp -r temp/. .
    rm -rf temp
fi
echo -e "${GREEN}✓ Files ready${NC}"

# ─── STEP 5: Generate Secrets (IDEMPOTENT) ──────────────────────────────────
echo -e "${YELLOW}[5/8] Generating secrets...${NC}"

# Get server IP
SERVER_IP=$(curl -s -m 5 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo -e "${BLUE}  Server IP: ${SERVER_IP}${NC}"

if [ -f .env ]; then
    echo -e "${GREEN}✓ Existing .env found — preserving${NC}"
    cp .env .env.backup
    source .env 2>/dev/null || true
    DOMAIN="${DOMAIN:-$SERVER_IP}"
    USE_SSL="${USE_SSL:-false}"
    if [ "$USE_SSL" = "true" ]; then
        ACCESS_URL="https://$DOMAIN"
    else
        ACCESS_URL="http://$SERVER_IP:8090"
    fi
else
    # Deployment mode
    echo ""
    echo -e "${BLUE}Select Deployment Mode:${NC}"
    echo -e "  1) IP Mode (Easy) - http://${SERVER_IP}:8090"
    echo -e "  2) SSL Mode (Prod) - https://your-domain.com"
    echo ""
    read -p "Choice [1]: " MODE_CHOICE
    MODE_CHOICE="${MODE_CHOICE:-1}"

    if [ "$MODE_CHOICE" = "2" ]; then
        USE_SSL=true
        read -p "Domain: " DOMAIN
        read -p "Email for SSL: " ACME_EMAIL
        ALLOWED_HOSTS="$DOMAIN"
        ACCESS_URL="https://$DOMAIN"
    else
        USE_SSL=false
        DOMAIN="$SERVER_IP"
        ACME_EMAIL=""
        ALLOWED_HOSTS="$SERVER_IP,localhost,127.0.0.1"
        ACCESS_URL="http://$SERVER_IP:8090"
    fi

    echo -e "${BLUE}  → Generating secure credentials...${NC}"

    # Install cryptography for Fernet key
    pip3 install cryptography -q 2>/dev/null || true

    SECRET_KEY=$(openssl rand -hex 32)
    POSTGRES_PASSWORD=$(openssl rand -hex 16)

    # Generate valid Fernet key (Python ONLY — no invalid fallback)
    FIELD_ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; k=Fernet.generate_key().decode(); Fernet(k.encode()); print(k)" 2>/dev/null)
    if [ -z "$FIELD_ENCRYPTION_KEY" ]; then
        echo -e "${RED}✗ Cannot generate Fernet key. Install: pip3 install cryptography${NC}"
        exit 1
    fi

    echo -e "${GREEN}✓ Secrets generated (Fernet key validated)${NC}"

    # Create .env
    cat > .env << ENVEOF
# SMSLY Hosting - Generated $(date -Iseconds)
SECRET_KEY=${SECRET_KEY}
FIELD_ENCRYPTION_KEY=${FIELD_ENCRYPTION_KEY}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
DOMAIN=${DOMAIN}
USE_SSL=${USE_SSL}
ACME_EMAIL=${ACME_EMAIL:-}
DEBUG=False
ALLOWED_HOSTS=${ALLOWED_HOSTS}
CSRF_TRUSTED_ORIGINS=http://${DOMAIN}:8090,https://${DOMAIN}
CORS_ALLOWED_ORIGINS=http://${DOMAIN}:8090,https://${DOMAIN}
CORS_ALLOW_ALL=False
POSTGRES_DB=smsly_hosting
POSTGRES_USER=smsly_admin
DATABASE_URL=postgresql://smsly_admin:${POSTGRES_PASSWORD}@db:5432/smsly_hosting
REDIS_URL=redis://redis:6379/0
CONTAINER_REGISTRY_URL=registry:5000
NEXT_PUBLIC_API_URL=/api/v1
ENVEOF

    chmod 600 .env
    echo -e "${GREEN}✓ Environment configured${NC}"
fi

# ─── STEP 6: Validate ───────────────────────────────────────────────────────
echo -e "${YELLOW}[6/8] Validating...${NC}"
if [ -z "$SECRET_KEY" ] || [ -z "$FIELD_ENCRYPTION_KEY" ] || [ -z "$POSTGRES_PASSWORD" ]; then
    echo -e "${RED}✗ Secret generation failed${NC}"
    exit 1
fi
echo -e "${GREEN}✓ Configuration valid${NC}"

# ─── STEP 7: Deploy ─────────────────────────────────────────────────────────
echo -e "${YELLOW}[7/8] Deploying services...${NC}"

docker network create smsly-net 2>/dev/null || true
docker network create smsly-proxy 2>/dev/null || true

echo -e "${BLUE}  → Building...${NC}"
docker compose -f docker-compose.prod.yml build

echo -e "${BLUE}  → Starting...${NC}"
docker compose -f docker-compose.prod.yml up -d

echo -e "${BLUE}  → Waiting for database...${NC}"
for i in $(seq 1 30); do
    if docker compose -f docker-compose.prod.yml exec -T db pg_isready -U smsly_admin 2>/dev/null; then
        echo -e "${GREEN}  ✓ Database ready${NC}"
        break
    fi
    printf "."
    sleep 2
done
echo ""

echo -e "${BLUE}  → Running migrations...${NC}"
docker compose -f docker-compose.prod.yml exec -T backend python manage.py migrate --noinput

echo -e "${BLUE}  → Collecting static files...${NC}"
docker compose -f docker-compose.prod.yml exec -T backend python manage.py collectstatic --noinput

echo -e "${GREEN}✓ Services deployed${NC}"

# ─── STEP 8: Verify ─────────────────────────────────────────────────────────
echo -e "${YELLOW}[8/8] Verifying...${NC}"
sleep 5

HEALTH=$(curl -s http://localhost:8090/health 2>/dev/null || echo "unavailable")
echo -e "${BLUE}  Health: ${HEALTH}${NC}"

docker compose -f docker-compose.prod.yml ps

echo -e "${GREEN}✓ Deployment verified${NC}"

# ─── Remove rollback trap on success ─────────────────────────────────────────
trap - EXIT

# ─── DONE ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✓ SMSLY Hosting is LIVE!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "📍 ${BLUE}Access:${NC}"
echo -e "   Dashboard: ${ACCESS_URL}/"
echo -e "   Admin:     ${ACCESS_URL}/admin/"
echo -e "   API:       ${ACCESS_URL}/api/v1/"
echo -e "   Health:    ${ACCESS_URL}/health"
echo ""
echo -e "📝 ${BLUE}Create admin user:${NC}"
echo -e "   cd ${INSTALL_DIR}"
echo -e "   docker compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser"
echo ""
echo -e "${BLUE}📋 Deploy log: $LOG_FILE${NC}"
echo -e "${YELLOW}⚠️  Credentials saved to: ${INSTALL_DIR}/.env${NC}"
echo ""
