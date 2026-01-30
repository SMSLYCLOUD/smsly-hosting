#!/bin/bash
set -e

echo "🚀 SMSLY Hosting V2 - Fresh VPS Deployment Script"
echo "=================================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
DOMAIN="${DOMAIN:-smsly-hosting.com}"
EMAIL="${EMAIL:-admin@smsly.com}"
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -base64 32)}"
REDIS_PASSWORD="${REDIS_PASSWORD:-$(openssl rand -base64 32)}"
SECRET_KEY="${SECRET_KEY:-$(openssl rand -base64 64)}"
ENCRYPTION_KEY="${ENCRYPTION_KEY:-$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')}"

echo -e "${GREEN}✓ Configuration loaded${NC}"

# Step 1: System Update
echo -e "\n${YELLOW}[1/10] Updating system packages...${NC}"
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y curl wget git build-essential python3-pip python3-venv nginx certbot python3-certbot-nginx

echo -e "${GREEN}✓ System updated${NC}"

# Step 2: Install Docker
echo -e "\n${YELLOW}[2/10] Installing Docker...${NC}"
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    systemctl enable docker
    systemctl start docker
    rm get-docker.sh
fi

# Install Docker Compose
if ! command -v docker-compose &> /dev/null; then
    curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
fi

echo -e "${GREEN}✓ Docker installed${NC}"

# Step 3: Install Nixpacks
echo -e "\n${YELLOW}[3/10] Installing Nixpacks...${NC}"
if ! command -v nixpacks &> /dev/null; then
    curl -sSL https://nixpacks.com/install.sh | bash
fi

echo -e "${GREEN}✓ Nixpacks installed${NC}"

# Step 4: Configure Firewall
echo -e "\n${YELLOW}[4/10] Configuring firewall...${NC}"
ufw --force enable
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw reload

echo -e "${GREEN}✓ Firewall configured${NC}"

# Step 5: Deploy PostgreSQL
echo -e "\n${YELLOW}[5/10] Deploying PostgreSQL...${NC}"
docker run -d \
    --name smsly-postgres \
    --restart unless-stopped \
    -e POSTGRES_DB=smsly_hosting \
    -e POSTGRES_USER=smsly \
    -e POSTGRES_PASSWORD="${DB_PASSWORD}" \
    -v postgres_data:/var/lib/postgresql/data \
    -p 5432:5432 \
    postgres:15-alpine

# Wait for PostgreSQL to be ready
sleep 5
docker exec smsly-postgres pg_isready -U smsly

echo -e "${GREEN}✓ PostgreSQL deployed${NC}"

# Step 6: Deploy Redis
echo -e "\n${YELLOW}[6/10] Deploying Redis...${NC}"
docker run -d \
    --name smsly-redis \
    --restart unless-stopped \
    -p 6379:6379 \
    redis:7-alpine redis-server --requirepass "${REDIS_PASSWORD}"

echo -e "${GREEN}✓ Redis deployed${NC}"

# Step 7: Clone Repository
echo -e "\n${YELLOW}[7/10] Cloning SMSLY Hosting repository...${NC}"
cd /opt
if [ -d "smsly-hosting" ]; then
    rm -rf smsly-hosting
fi

git clone https://github.com/SMSLYCLOUD/smsly-hosting.git
cd smsly-hosting

echo -e "${GREEN}✓ Repository cloned${NC}"

# Step 8: Configure Backend
echo -e "\n${YELLOW}[8/10] Configuring backend...${NC}"

cat > backend/.env << EOF
# Django Settings
SECRET_KEY=${SECRET_KEY}
FIELD_ENCRYPTION_KEY=${ENCRYPTION_KEY}
DEBUG=False
ENVIRONMENT=production
ALLOWED_HOSTS=${DOMAIN},www.${DOMAIN}

# Database
DATABASE_URL=postgresql://smsly:${DB_PASSWORD}@localhost:5432/smsly_hosting

# Redis
REDIS_URL=redis://:${REDIS_PASSWORD}@localhost:6379/0

# CORS
CORS_ALLOWED_ORIGINS=https://${DOMAIN},https://www.${DOMAIN}
CSRF_TRUSTED_ORIGINS=https://${DOMAIN},https://www.${DOMAIN}

# Container Registry (optional)
CONTAINER_REGISTRY_URL=registry.${DOMAIN}

# AI Features (optional)
# GEMINI_API_KEY=your_key_here
EOF

echo -e "${GREEN}✓ Backend configured${NC}"

# Step 9: Build and Deploy Backend
echo -e "\n${YELLOW}[9/10] Building and deploying backend...${NC}"

cd backend

# Build Docker image
docker build -t smsly-hosting-backend:latest .

# Run migrations
docker run --rm \
    --network host \
    --env-file .env \
    smsly-hosting-backend:latest \
    python manage.py migrate

# Collect static files
docker run --rm \
    --network host \
    --env-file .env \
    -v /opt/smsly-hosting/staticfiles:/app/staticfiles \
    smsly-hosting-backend:latest \
    python manage.py collectstatic --noinput

# Start backend
docker run -d \
    --name smsly-backend \
    --restart unless-stopped \
    --network host \
    --env-file .env \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /opt/smsly-hosting/staticfiles:/app/staticfiles \
    smsly-hosting-backend:latest \
    gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4

# Start Celery worker
docker run -d \
    --name smsly-celery-worker \
    --restart unless-stopped \
    --network host \
    --env-file .env \
    -v /var/run/docker.sock:/var/run/docker.sock \
    smsly-hosting-backend:latest \
    celery -A config worker -l info

# Start Celery beat
docker run -d \
    --name smsly-celery-beat \
    --restart unless-stopped \
    --network host \
    --env-file .env \
    smsly-hosting-backend:latest \
    celery -A config beat -l info

echo -e "${GREEN}✓ Backend deployed${NC}"

# Step 10: Configure Nginx
echo -e "\n${YELLOW}[10/10] Configuring Nginx...${NC}"

cat > /etc/nginx/sites-available/smsly-hosting << 'EOF'
server {
    listen 80;
    server_name DOMAIN www.DOMAIN;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name DOMAIN www.DOMAIN;

    ssl_certificate /etc/letsencrypt/live/DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/DOMAIN/privkey.pem;

    # API Backend
    location /api/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Admin
    location /admin/ {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # Static files
    location /static/ {
        alias /opt/smsly-hosting/staticfiles/;
    }

    # WebSocket (for real-time features)
    location /ws/ {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Frontend (if serving from same domain)
    location / {
        root /opt/smsly-hosting/frontend/out;
        try_files $uri $uri.html $uri/ /index.html;
    }
}
EOF

sed -i "s/DOMAIN/${DOMAIN}/g" /etc/nginx/sites-available/smsly-hosting

ln -sf /etc/nginx/sites-available/smsly-hosting /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
nginx -t

# Get SSL certificate
mkdir -p /var/www/certbot
systemctl reload nginx
certbot --nginx -d ${DOMAIN} -d www.${DOMAIN} --non-interactive --agree-tos -m ${EMAIL}

echo -e "${GREEN}✓ Nginx configured with SSL${NC}"

# Final Summary
echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}✓ SMSLY Hosting V2 Deployment Complete!${NC}"
echo -e "${GREEN}========================================${NC}"

echo -e "\n📝 Important Information:"
echo -e "Domain: https://${DOMAIN}"
echo -e "Admin: https://${DOMAIN}/admin/"
echo -e "API: https://${DOMAIN}/api/v1/"

echo -e "\n🔐 Credentials (SAVE THESE!):"
echo -e "Database Password: ${DB_PASSWORD}"
echo -e "Redis Password: ${REDIS_PASSWORD}"
echo -e "Django Secret Key: ${SECRET_KEY}"
echo -e "Encryption Key: ${ENCRYPTION_KEY}"

echo -e "\n📊 Service Status:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo -e "\n🔍 Next Steps:"
echo -e "1. Create superuser: docker exec -it smsly-backend python manage.py createsuperuser"
echo -e "2. Access admin panel: https://${DOMAIN}/admin/"
echo -e "3. Configure cloud providers in admin"
echo -e "4. Test deployment workflow"

echo -e "\n${YELLOW}⚠️  Save the credentials above to a secure location!${NC}"
