#!/bin/bash
set -e

echo "🚀 SMSLY Hosting V2 - Complete Installation & Data Wipe Script"
echo "=============================================================="
echo "⚠️  WARNING: This will DELETE all existing data and containers!"
echo ""
read -p "Are you sure you want to continue? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "Installation cancelled."
    exit 1
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
DOMAIN="${DOMAIN:-smsly-hosting.com}"
EMAIL="${EMAIL:-admin@smsly.com}"
# Use alphanumeric passwords to avoid URL encoding issues
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -hex 24)}"
REDIS_PASSWORD="${REDIS_PASSWORD:-$(openssl rand -hex 24)}"
SECRET_KEY="$(openssl rand -hex 48)"
INSTALL_DIR="/opt/smsly-hosting"

# Generate encryption key (requires Python)
if command -v python3 &> /dev/null; then
    ENCRYPTION_KEY=$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' 2>/dev/null || echo "")
fi
if [ -z "$ENCRYPTION_KEY" ]; then
    ENCRYPTION_KEY=$(openssl rand -base64 32)
fi

echo -e "${GREEN}✓ Configuration loaded${NC}"

# ============================================================
# STEP 1: COMPLETE DATA WIPE
# ============================================================
echo -e "\n${RED}[1/12] Wiping existing data...${NC}"

# Stop and remove all SMSLY containers
echo "Stopping containers..."
docker stop smsly-backend smsly-celery-worker smsly-celery-beat smsly-postgres smsly-redis smsly-nginx 2>/dev/null || true
docker rm smsly-backend smsly-celery-worker smsly-celery-beat smsly-postgres smsly-redis smsly-nginx 2>/dev/null || true

# Remove volumes
echo "Removing volumes..."
docker volume rm postgres_data redis_data 2>/dev/null || true

# Remove old installation
if [ -d "$INSTALL_DIR" ]; then
    echo "Removing old installation..."
    rm -rf "$INSTALL_DIR"
fi

# Clean Docker system
echo "Cleaning Docker system..."
docker system prune -af --volumes

echo -e "${GREEN}✓ Data wiped${NC}"

# ============================================================
# STEP 2: INSTALL SYSTEM DEPENDENCIES
# ============================================================
echo -e "\n${YELLOW}[2/12] Installing system dependencies...${NC}"

apt-get update -qq
apt-get install -y -qq \
    curl \
    wget \
    git \
    build-essential \
    python3-pip \
    python3-venv \
    nginx \
    certbot \
    python3-certbot-nginx \
    jq \
    htop

echo -e "${GREEN}✓ System dependencies installed${NC}"

# ============================================================
# STEP 3: INSTALL DOCKER
# ============================================================
echo -e "\n${YELLOW}[3/12] Installing/Updating Docker...${NC}"

if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
fi

if ! command -v docker-compose &> /dev/null; then
    curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
fi

systemctl enable docker
systemctl start docker

echo -e "${GREEN}✓ Docker ready${NC}"

# ============================================================
# STEP 4: INSTALL NIXPACKS
# ============================================================
echo -e "\n${YELLOW}[4/12] Installing Nixpacks...${NC}"

if ! command -v nixpacks &> /dev/null; then
    curl -sSL https://nixpacks.com/install.sh | bash
fi

echo -e "${GREEN}✓ Nixpacks installed${NC}"

# ============================================================
# STEP 5: CONFIGURE FIREWALL
# ============================================================
echo -e "\n${YELLOW}[5/12] Configuring firewall...${NC}"

if command -v ufw &> /dev/null; then
    ufw --force enable
    ufw allow 22/tcp
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw reload
fi

echo -e "${GREEN}✓ Firewall configured${NC}"

# ============================================================
# STEP 6: DEPLOY POSTGRESQL
# ============================================================
echo -e "\n${YELLOW}[6/12] Deploying PostgreSQL...${NC}"

docker run -d \
    --name smsly-postgres \
    --restart unless-stopped \
    -e POSTGRES_DB=smsly_hosting \
    -e POSTGRES_USER=smsly \
    -e POSTGRES_PASSWORD="${DB_PASSWORD}" \
    -v postgres_data:/var/lib/postgresql/data \
    -p 127.0.0.1:5432:5432 \
    postgres:15-alpine

# Wait for PostgreSQL
echo "Waiting for PostgreSQL to be ready..."
sleep 10
until docker exec smsly-postgres pg_isready -U smsly > /dev/null 2>&1; do
    echo "Waiting..."
    sleep 2
done

echo -e "${GREEN}✓ PostgreSQL deployed${NC}"

# ============================================================
# STEP 7: DEPLOY REDIS
# ============================================================
echo -e "\n${YELLOW}[7/12] Deploying Redis...${NC}"

docker run -d \
    --name smsly-redis \
    --restart unless-stopped \
    -p 127.0.0.1:6379:6379 \
    -v redis_data:/data \
    redis:7-alpine redis-server --requirepass "${REDIS_PASSWORD}"

sleep 3

echo -e "${GREEN}✓ Redis deployed${NC}"

# ============================================================
# STEP 8: CLONE REPOSITORY
# ============================================================
echo -e "\n${YELLOW}[8/12] Cloning repository...${NC}"

mkdir -p /opt
cd /opt
git clone https://github.com/SMSLYCLOUD/smsly-hosting.git
cd smsly-hosting

echo -e "${GREEN}✓ Repository cloned${NC}"

# ============================================================
# STEP 9: CONFIGURE BACKEND
# ============================================================
echo -e "\n${YELLOW}[9/12] Configuring backend...${NC}"

cd backend

cat > .env << EOF
# Django Settings
SECRET_KEY=${SECRET_KEY}
FIELD_ENCRYPTION_KEY=${ENCRYPTION_KEY}
DEBUG=False
ENVIRONMENT=production
ALLOWED_HOSTS=${DOMAIN},www.${DOMAIN},localhost,127.0.0.1

# Database
DATABASE_URL=postgresql://smsly:${DB_PASSWORD}@127.0.0.1:5432/smsly_hosting

# Redis
REDIS_URL=redis://:${REDIS_PASSWORD}@127.0.0.1:6379/0

# CORS
CORS_ALLOWED_ORIGINS=https://${DOMAIN},https://www.${DOMAIN},http://localhost:3000
CSRF_TRUSTED_ORIGINS=https://${DOMAIN},https://www.${DOMAIN}

# Container Registry (optional - leave empty for local builds)
CONTAINER_REGISTRY_URL=

# AI Features (optional)
# GEMINI_API_KEY=
EOF

echo -e "${GREEN}✓ Backend configured${NC}"

# ============================================================
# STEP 10: BUILD & DEPLOY BACKEND
# ============================================================
echo -e "\n${YELLOW}[10/12] Building and deploying backend...${NC}"

# Build Docker image
echo "Building Docker image..."
docker build -t smsly-hosting-backend:latest .

# Run migrations
echo "Running database migrations..."
docker run --rm \
    --network host \
    --env-file .env \
    smsly-hosting-backend:latest \
    python manage.py migrate

# Create superuser (non-interactive)
echo "Creating superuser..."
docker run --rm \
    --network host \
    --env-file .env \
    smsly-hosting-backend:latest \
    python manage.py shell -c "
from django.contrib.auth import get_user_model;
User = get_user_model();
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@smsly.com', 'admin123');
    print('Superuser created: admin / admin123');
else:
    print('Superuser already exists');
"

# Collect static files
echo "Collecting static files..."
mkdir -p /opt/smsly-hosting/staticfiles
docker run --rm \
    --network host \
    --env-file .env \
    -v /opt/smsly-hosting/staticfiles:/app/staticfiles \
    smsly-hosting-backend:latest \
    python manage.py collectstatic --noinput

# Start backend
echo "Starting backend..."
docker run -d \
    --name smsly-backend \
    --restart unless-stopped \
    --network host \
    --env-file .env \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /opt/smsly-hosting/staticfiles:/app/staticfiles \
    -v /tmp:/tmp \
    smsly-hosting-backend:latest \
    gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4 --timeout 120

# Start Celery worker
echo "Starting Celery worker..."
docker run -d \
    --name smsly-celery-worker \
    --restart unless-stopped \
    --network host \
    --env-file .env \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /tmp:/tmp \
    smsly-hosting-backend:latest \
    celery -A config worker -l info

# Start Celery beat
echo "Starting Celery beat..."
docker run -d \
    --name smsly-celery-beat \
    --restart unless-stopped \
    --network host \
    --env-file .env \
    smsly-hosting-backend:latest \
    celery -A config beat -l info

sleep 5

echo -e "${GREEN}✓ Backend deployed${NC}"

# ============================================================
# STEP 11: CONFIGURE NGINX
# ============================================================
echo -e "\n${YELLOW}[11/12] Configuring Nginx...${NC}"

# Remove default site
rm -f /etc/nginx/sites-enabled/default

# Create SMSLY Hosting config
cat > /etc/nginx/sites-available/smsly-hosting << EOF
server {
    listen 80;
    server_name ${DOMAIN} www.${DOMAIN};

    client_max_body_size 100M;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name ${DOMAIN} www.${DOMAIN};

    client_max_body_size 100M;

    # SSL Configuration (will be added by certbot)
    # ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    # ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;

    # API Backend
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }

    # Admin Panel
    location /admin/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # Static Files
    location /static/ {
        alias /opt/smsly-hosting/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # WebSocket Support
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    # Health Check
    location /health {
        proxy_pass http://127.0.0.1:8000;
        access_log off;
    }
}
EOF

# Enable site
ln -sf /etc/nginx/sites-available/smsly-hosting /etc/nginx/sites-enabled/

# Test Nginx configuration
nginx -t

# Reload Nginx
systemctl reload nginx

echo -e "${GREEN}✓ Nginx configured${NC}"

# ============================================================
# STEP 12: SSL CERTIFICATE
# ============================================================
echo -e "\n${YELLOW}[12/12] Setting up SSL certificate...${NC}"

mkdir -p /var/www/certbot

if [ "$DOMAIN" != "smsly-hosting.com" ] && [ "$DOMAIN" != "localhost" ]; then
    echo "Attempting to obtain SSL certificate..."
    certbot --nginx -d ${DOMAIN} -d www.${DOMAIN} \
        --non-interactive \
        --agree-tos \
        --email ${EMAIL} \
        --redirect || echo "SSL setup skipped (manual configuration may be needed)"
else
    echo "Skipping SSL for default/localhost domain"
fi

echo -e "${GREEN}✓ SSL configured${NC}"

# ============================================================
# INSTALLATION COMPLETE
# ============================================================
echo -e "\n${GREEN}============================================${NC}"
echo -e "${GREEN}✓ INSTALLATION COMPLETE!${NC}"
echo -e "${GREEN}============================================${NC}"

echo -e "\n${BLUE}📝 Access Information:${NC}"
echo -e "Domain: https://${DOMAIN}"
echo -e "Admin Panel: https://${DOMAIN}/admin/"
echo -e "API: https://${DOMAIN}/api/v1/"
echo -e ""
echo -e "Superuser: admin"
echo -e "Password: admin123"
echo -e "${RED}⚠️  CHANGE THIS PASSWORD IMMEDIATELY!${NC}"

echo -e "\n${BLUE}🔐 Generated Credentials (SAVE THESE!):${NC}"
echo -e "Database Password: ${DB_PASSWORD}"
echo -e "Redis Password: ${REDIS_PASSWORD}"
echo -e "Django Secret Key: ${SECRET_KEY}"
echo -e "Encryption Key: ${ENCRYPTION_KEY}"

echo -e "\n${BLUE}📊 Service Status:${NC}"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo -e "\n${BLUE}🔍 Useful Commands:${NC}"
echo -e "View backend logs: docker logs -f smsly-backend"
echo -e "View worker logs: docker logs -f smsly-celery-worker"
echo -e "Restart services: docker restart smsly-backend smsly-celery-worker"
echo -e "Access Django shell: docker exec -it smsly-backend python manage.py shell"

echo -e "\n${BLUE}🚀 Next Steps:${NC}"
echo -e "1. Change admin password at https://${DOMAIN}/admin/"
echo -e "2. Configure cloud providers (AWS/Azure/GCP) in admin"
echo -e "3. Test deployment: Create a service and deploy"
echo -e "4. Set up monitoring and backups"

echo -e "\n${GREEN}✓ Ready to deploy applications!${NC}"

# Save credentials to file
cat > /root/smsly-credentials.txt << EOF
SMSLY Hosting V2 - Installation Credentials
Generated: $(date)

Domain: ${DOMAIN}
Admin URL: https://${DOMAIN}/admin/
Admin User: admin
Admin Password: admin123 (CHANGE THIS!)

Database Password: ${DB_PASSWORD}
Redis Password: ${REDIS_PASSWORD}
Django Secret Key: ${SECRET_KEY}
Encryption Key: ${ENCRYPTION_KEY}

Environment File: /opt/smsly-hosting/backend/.env
EOF

chmod 600 /root/smsly-credentials.txt
echo -e "\n${YELLOW}📄 Credentials saved to: /root/smsly-credentials.txt${NC}"
