#!/bin/bash
set -e

# =============================================================================
#   SMSly Hosting - Enterprise Control Plane Installer
#   Version: 2.0.0
#   Features: Docker, Nginx, SSL, SMSLY Platform Integration
# =============================================================================

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}   SMSly Hosting - Enterprise Control Plane Installer ${NC}"
echo -e "${BLUE}   Native SMS/Voice/Video Integration Built-In        ${NC}"
echo -e "${BLUE}======================================================${NC}"

# 1. Pre-flight Checks
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Please run as root (sudo ./install-control-plane.sh)${NC}"
  exit 1
fi

# Check minimum requirements
TOTAL_RAM=$(free -m | awk '/^Mem:/{print $2}')
if [ "$TOTAL_RAM" -lt 3500 ]; then
  echo -e "${YELLOW}Warning: Minimum 4GB RAM recommended. You have ${TOTAL_RAM}MB${NC}"
  read -p "Continue anyway? (y/N): " CONTINUE
  [ "$CONTINUE" != "y" ] && exit 1
fi

echo -e "${GREEN}[+] Updating System Packages...${NC}"
apt-get update && apt-get upgrade -y
apt-get install -y curl git ufw fail2ban jq nginx certbot python3-certbot-nginx python3-pip

# 2. Hardening
echo -e "${GREEN}[+] Configuring Firewall (UFW)...${NC}"
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow http
ufw allow https
# Kubernetes API (for worker node communication)
ufw allow 6443/tcp
ufw --force enable

# Fail2ban for SSH protection
systemctl enable fail2ban
systemctl start fail2ban

# 3. Install Docker
if ! command -v docker &> /dev/null; then
    echo -e "${GREEN}[+] Installing Docker Engine...${NC}"
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    
    # Enable Docker on boot
    systemctl enable docker
fi

# 4. Install Docker Compose Plugin
if ! docker compose version &> /dev/null; then
    echo -e "${GREEN}[+] Installing Docker Compose...${NC}"
    apt-get install -y docker-compose-plugin
fi

# 5. Configuration
echo -e "${BLUE}--- Configuration ---${NC}"
read -p "Enter your Domain Name (e.g. hosting.smsly.cloud): " DOMAIN_NAME
read -p "Enter Admin Email (for SSL): " ADMIN_EMAIL

echo ""
echo -e "${YELLOW}--- SMSLY Platform Integration (Optional, recommended) ---${NC}"
read -p "Enable SMS/Voice alerts? (y/N): " ENABLE_SMSLY
if [ "$ENABLE_SMSLY" == "y" ]; then
    read -p "SMSLY Platform API URL [http://smsly-platform-api:8000/api/v1]: " SMSLY_PLATFORM_URL
    SMSLY_PLATFORM_URL=${SMSLY_PLATFORM_URL:-"http://smsly-platform-api:8000/api/v1"}
    read -p "SMSLY Internal API Key: " SMSLY_INTERNAL_KEY
    read -p "Alert Phone Number (E.164 format, e.g. +1234567890): " ALERT_PHONE
fi

INSTALL_DIR="/opt/smsly-hosting"
echo -e "${GREEN}[+] Setting up installation directory at ${INSTALL_DIR}...${NC}"
mkdir -p $INSTALL_DIR

# Clone or copy repository
if [ -d "./backend" ]; then
    echo -e "${GREEN}[+] Copying local files...${NC}"
    cp -r ./* $INSTALL_DIR/
elif [ -d "../backend" ]; then
    cp -r ../* $INSTALL_DIR/
else
    echo -e "${GREEN}[+] Cloning from GitHub...${NC}"
    git clone https://github.com/SMSLYCLOUD/smsly-hosting.git $INSTALL_DIR
fi

# 6. Generate Secrets
echo -e "${GREEN}[+] Generating Secrets...${NC}"

# Install cryptography for key generation
pip3 install cryptography -q

ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
SECRET_KEY=$(openssl rand -hex 50)

# 7. Create .env file
cat > $INSTALL_DIR/.env <<EOF
# ============================================
# SMSly Hosting - Production Configuration
# Generated: $(date)
# ============================================

# Core Security (NEVER commit these)
DEBUG=False
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$ENCRYPTION_KEY

# Domain & Hosts
ALLOWED_HOSTS=$DOMAIN_NAME,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://$DOMAIN_NAME

# CORS
CORS_ALLOW_ALL=False
CORS_ALLOWED_ORIGINS=https://$DOMAIN_NAME

# Database & Redis (internal Docker network)
DATABASE_URL=postgres://postgres:postgres@db:5432/smsly_hosting
REDIS_URL=redis://redis:6379/0

# Container Registry
CONTAINER_REGISTRY_URL=registry.smsly.cloud
REGISTRY_USER=
REGISTRY_PASSWORD=

# API URLs
NEXT_PUBLIC_API_URL=https://$DOMAIN_NAME/api/v1
INTERNAL_API_URL=http://backend:8000/api/v1

# SMSLY Platform Integration
SMSLY_SMS_API_URL=${SMSLY_PLATFORM_URL:-"http://smsly-sms:8000/api/v1"}
SMSLY_VOICE_API_URL=${SMSLY_PLATFORM_URL:-"http://smsly-voice:8000/api/v1"}
SMSLY_PLATFORM_API_URL=${SMSLY_PLATFORM_URL:-"http://smsly-platform-api:8000/api/v1"}
SMSLY_INTERNAL_API_KEY=${SMSLY_INTERNAL_KEY:-""}

# Alerting
ALERT_PHONE_NUMBER=${ALERT_PHONE:-""}
CRITICAL_ALERT_PHONE=${ALERT_PHONE:-""}
NOTIFY_ON_SUCCESS=False

# Logging
DJANGO_LOG_LEVEL=INFO
EOF

echo -e "${GREEN}[+] Environment file created at $INSTALL_DIR/.env${NC}"

# 8. Nginx Reverse Proxy
echo -e "${GREEN}[+] Configuring Nginx...${NC}"
cat > /etc/nginx/sites-available/smsly-hosting <<EOF
server {
    server_name $DOMAIN_NAME;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Frontend (Next.js)
    location / {
        proxy_pass http://localhost:3000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # API (Django)
    location /api/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # Rate limiting
        limit_req zone=api burst=20 nodelay;
    }

    # WebSocket (Real-time logs, Terminal)
    location /ws/ {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 86400;
    }

    # Prometheus Metrics (internal only)
    location /api/v1/metrics/prometheus/ {
        allow 127.0.0.1;
        allow 10.0.0.0/8;
        allow 172.16.0.0/12;
        allow 192.168.0.0/16;
        deny all;
        proxy_pass http://localhost:8000;
    }
}
EOF

# Add rate limiting zone to nginx.conf
if ! grep -q "limit_req_zone" /etc/nginx/nginx.conf; then
    sed -i '/http {/a\    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;' /etc/nginx/nginx.conf
fi

ln -sf /etc/nginx/sites-available/smsly-hosting /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

# 9. SSL Certificate
if [ ! -z "$DOMAIN_NAME" ] && [ "$DOMAIN_NAME" != "localhost" ]; then
    echo -e "${GREEN}[+] Requesting SSL Certificate...${NC}"
    certbot --nginx -d $DOMAIN_NAME --non-interactive --agree-tos -m $ADMIN_EMAIL || {
        echo -e "${YELLOW}SSL setup failed. You can run 'certbot --nginx -d $DOMAIN_NAME' later.${NC}"
    }
fi

# 10. Start Services
echo -e "${GREEN}[+] Starting Platform Services...${NC}"
cd $INSTALL_DIR
docker compose up -d --build

# 11. Wait for services to be ready
echo -e "${GREEN}[+] Waiting for services to start...${NC}"
sleep 15

# 12. Run migrations
echo -e "${GREEN}[+] Running database migrations...${NC}"
docker compose exec -T backend python manage.py migrate --noinput || true

# 13. Create superuser prompt
echo ""
echo -e "${GREEN}[+] Create admin superuser?${NC}"
read -p "Create Django superuser? (y/N): " CREATE_SUPER
if [ "$CREATE_SUPER" == "y" ]; then
    docker compose exec backend python manage.py createsuperuser
fi

# 14. Final summary
echo ""
echo -e "${BLUE}======================================================${NC}"
echo -e "${GREEN}   Installation Complete! ${NC}"
echo -e "${BLUE}======================================================${NC}"
echo ""
echo -e "   ${GREEN}Dashboard:${NC} https://$DOMAIN_NAME"
echo -e "   ${GREEN}API:${NC} https://$DOMAIN_NAME/api/v1/"
echo -e "   ${GREEN}Admin:${NC} https://$DOMAIN_NAME/api/admin/"
echo ""
echo -e "${YELLOW}Next Steps:${NC}"
echo "   1. Set up a Worker Node (see install-worker-node.sh)"
echo "   2. Configure Container Registry credentials in .env"
echo "   3. Add SMSLY API keys for full integration"
echo ""
echo -e "${BLUE}======================================================${NC}"
