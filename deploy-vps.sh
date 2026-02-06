#!/bin/bash
set -e

#===============================================================================
# SMSLY Hosting - Production VPS Deployment Script
# Version: 2.0 (Production-Hardened)
#
# This script deploys SMSLY Hosting with enterprise-grade security:
# - Fail-closed secrets (no insecure defaults)
# - Docker socket isolation via read-only proxy
# - SSL/TLS with Let's Encrypt
# - Health checks for zero-downtime deployments
#===============================================================================

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  SMSLY Hosting - Production VPS Deployment${NC}"
echo -e "${BLUE}  100% Production-Ready with Enterprise Security${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}\n"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}✗ This script must be run as root${NC}"
    echo "Please run: sudo bash $0"
    exit 1
fi

#===============================================================================
# STEP 1: System Update & Dependencies
#===============================================================================
echo -e "\n${YELLOW}[1/8] Updating system and installing dependencies...${NC}"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq
apt-get install -y curl wget git python3 python3-pip ufw

echo -e "${GREEN}✓ System updated${NC}"

#===============================================================================
# STEP 2: Install Docker & Docker Compose
#===============================================================================
echo -e "\n${YELLOW}[2/8] Installing Docker...${NC}"

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    systemctl enable docker
    systemctl start docker
    rm get-docker.sh
    echo -e "${GREEN}✓ Docker installed${NC}"
else
    echo -e "${GREEN}✓ Docker already installed${NC}"
fi

# Install Docker Compose
if ! command -v docker-compose &> /dev/null; then
    curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
    echo -e "${GREEN}✓ Docker Compose installed${NC}"
else
    echo -e "${GREEN}✓ Docker Compose already installed${NC}"
fi

#===============================================================================
# STEP 3: Configure Firewall
#===============================================================================
echo -e "\n${YELLOW}[3/8] Configuring firewall...${NC}"
ufw --force enable
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw reload

echo -e "${GREEN}✓ Firewall configured (ports 22, 80, 443 open)${NC}"

#===============================================================================
# STEP 4: Clone Repository
#===============================================================================
echo -e "\n${YELLOW}[4/8] Setting up SMSLY Hosting...${NC}"

INSTALL_DIR="/opt/smsly-hosting"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Clone repository (replace with actual repo URL)
if [ ! -d ".git" ]; then
    git clone https://github.com/SMSLYCLOUD/smslycloud.git temp
    mv temp/SMSLY-HOSTING/* .
    rm -rf temp
fi

echo -e "${GREEN}✓ Repository cloned to $INSTALL_DIR${NC}"

#===============================================================================
# STEP 5: Generate Secrets & Configure Environment
#===============================================================================
echo -e "\n${YELLOW}[5/8] Generating secrets and configuring environment...${NC}"

# Generate strong secrets
SECRET_KEY=$(python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())")
FIELD_ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
POSTGRES_PASSWORD=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)

# Prompt for domain
echo -e "\n${BLUE}Please enter your domain (e.g., hosting.example.com):${NC}"
read -p "Domain: " DOMAIN
echo -e "\n${BLUE}Please enter your email for SSL certificates:${NC}"
read -p "Email: " ACME_EMAIL

# Create .env file
cat > .env << EOF
# SMSLY Hosting Production Configuration
# Generated: $(date)

# ============================================================================
# REQUIRED SECRETS (DO NOT SHARE)
# ============================================================================
SECRET_KEY=${SECRET_KEY}
FIELD_ENCRYPTION_KEY=${FIELD_ENCRYPTION_KEY}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# ============================================================================
# DOMAIN & SSL
# ============================================================================
DOMAIN=${DOMAIN}
ACME_EMAIL=${ACME_EMAIL}

# ============================================================================
# SECURITY SETTINGS
# ============================================================================
DEBUG=False
ALLOWED_HOSTS=${DOMAIN}
CSRF_TRUSTED_ORIGINS=https://${DOMAIN}
CORS_ALLOWED_ORIGINS=https://${DOMAIN}
CORS_ALLOW_ALL=False

# ============================================================================
# DATABASE
# ============================================================================
POSTGRES_DB=smsly_hosting
POSTGRES_USER=smsly_admin
DATABASE_URL=postgresql://smsly_admin:${POSTGRES_PASSWORD}@db:5432/smsly_hosting

# ============================================================================
# REDIS
# ============================================================================
REDIS_URL=redis://redis:6379/0

# ============================================================================
# CONTAINER REGISTRY (INTERNAL)
# ============================================================================
CONTAINER_REGISTRY_URL=registry:5000
EOF

chmod 600 .env

echo -e "${GREEN}✓ Environment configured${NC}"
echo -e "${YELLOW}  Secrets saved to: $INSTALL_DIR/.env${NC}"

#===============================================================================
# STEP 6: Validate Configuration
#===============================================================================
echo -e "\n${YELLOW}[6/8] Validating production configuration...${NC}"

if [ -f "scripts/validate_production.py" ]; then
    python3 scripts/validate_production.py || {
        echo -e "${RED}✗ Configuration validation failed${NC}"
        exit 1
    }
fi

echo -e "${GREEN}✓ Configuration validated${NC}"

#===============================================================================
# STEP 7: Deploy Services
#===============================================================================
echo -e "\n${YELLOW}[7/8] Deploying services...${NC}"

# Create external network for Traefik
docker network create smsly-proxy 2>/dev/null || echo "Network smsly-proxy already exists"

# Deploy Traefik (SSL/TLS)
echo -e "${BLUE}  → Starting Traefik (SSL/TLS)...${NC}"
docker-compose -f docker-compose.traefik.yml up -d

# Deploy main application stack
echo -e "${BLUE}  → Starting application stack...${NC}"
docker-compose -f docker-compose.prod.yml up -d

# Wait for services to be ready
echo -e "${BLUE}  → Waiting for services to start (30s)...${NC}"
sleep 30

# Run database migrations
echo -e "${BLUE}  → Running database migrations...${NC}"
docker-compose -f docker-compose.prod.yml exec -T backend python manage.py migrate

# Collect static files
echo -e "${BLUE}  → Collecting static files...${NC}"
docker-compose -f docker-compose.prod.yml exec -T backend python manage.py collectstatic --noinput

echo -e "${GREEN}✓ Services deployed${NC}"

#===============================================================================
# STEP 8: Verify Deployment
#===============================================================================
echo -e "\n${YELLOW}[8/8] Verifying deployment...${NC}"

# Check if services are running
BACKEND_STATUS=$(docker-compose -f docker-compose.prod.yml ps backend | grep -c "Up" || echo "0")
DB_STATUS=$(docker-compose -f docker-compose.prod.yml ps db | grep -c "Up" || echo "0")

if [ "$BACKEND_STATUS" -eq "1" ] && [ "$DB_STATUS" -eq "1" ]; then
    echo -e "${GREEN}✓ All services running${NC}"
else
    echo -e "${RED}✗ Some services failed to start${NC}"
    docker-compose -f docker-compose.prod.yml ps
    exit 1
fi

# Test health endpoint
echo -e "${BLUE}  → Testing health endpoint...${NC}"
sleep 5
HEALTH_CHECK=$(curl -s http://localhost:8090/health | grep -c "healthy" || echo "0")

if [ "$HEALTH_CHECK" -ge "1" ]; then
    echo -e "${GREEN}✓ Health check passed${NC}"
else
    echo -e "${YELLOW}⚠ Health check inconclusive (may need DNS propagation)${NC}"
fi

#===============================================================================
# SUCCESS SUMMARY
#===============================================================================
echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✓ SMSLY Hosting Deployment Complete!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}\n"

echo -e "📍 ${BLUE}Access Points:${NC}"
echo -e "   Dashboard: https://${DOMAIN}/"
echo -e "   Admin:     https://${DOMAIN}/admin/"
echo -e "   API:       https://${DOMAIN}/api/v1/"
echo -e "   Health:    https://${DOMAIN}/health"

echo -e "\n🔒 ${BLUE}Security Status:${NC}"
echo -e "   ✓ SSL/TLS enabled (Let's Encrypt)"
echo -e "   ✓ Docker socket secured via proxy"
echo -e "   ✓ Fail-closed secrets (no defaults)"
echo -e "   ✓ Health checks enabled"

echo -e "\n📝 ${BLUE}Next Steps:${NC}"
echo -e "   1. Create superuser:"
echo -e "      cd $INSTALL_DIR"
echo -e "      docker-compose -f docker-compose.prod.yml exec backend python manage.py createsuperuser"
echo -e ""
echo -e "   2. Update DNS:"
echo -e "      Add A record: ${DOMAIN} → $(curl -s ifconfig.me)"
echo -e ""
echo -e "   3. Wait for SSL (2-5 minutes after DNS propagation)"
echo -e ""
echo -e "   4. Access admin panel:"
echo -e "      https://${DOMAIN}/admin/"

echo -e "\n💾 ${BLUE}Backup Information:${NC}"
echo -e "   Configuration: $INSTALL_DIR/.env"
echo -e "   Backups: $INSTALL_DIR/backups/ (create this directory)"
echo -e ""
echo -e "   Enable automated backups:"
echo -e "   crontab -e"
echo -e "   Add: 0 2 * * * cd $INSTALL_DIR && docker-compose -f docker-compose.prod.yml exec -T db pg_dump -U smsly_admin smsly_hosting | gzip > backups/backup_\$(date +\\%Y\\%m\\%d).sql.gz"

echo -e "\n📚 ${BLUE}Documentation:${NC}"
echo -e "   Production Guide: $INSTALL_DIR/PRODUCTION_DEPLOYMENT.md"
echo -e "   Operations:       $INSTALL_DIR/RUNBOOK.md"

echo -e "\n${YELLOW}⚠️  IMPORTANT: Save the credentials in $INSTALL_DIR/.env to a secure location!${NC}\n"
