#!/bin/bash

# =============================================================================
# SMSLY Hosting - Aggressive One-Click Installer
# =============================================================================
# WARNING: THIS SCRIPT WILL TAKE OVER THE SYSTEM.
# IT REMOVES CONFLICTING PACKAGES AND RESETS DOCKER.
# USE ONLY ON A FRESH VPS.
# =============================================================================

set -e

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${RED}WARNING: This script is designed for a FRESH VPS.${NC}"
echo -e "${RED}It will aggressively remove Docker, Nginx, Apache, and other services.${NC}"
echo -e "${YELLOW}Starting installation in 5 seconds... (Ctrl+C to cancel)${NC}"
sleep 5

# 1. Aggressive Cleanup
echo -e "\n${YELLOW}[1/6] Cleaning up system...${NC}"
# Stop conflicting services
systemctl stop nginx apache2 docker || true
systemctl disable nginx apache2 || true

# Remove old docker
apt-get remove -y docker docker-engine docker.io containerd runc || true
apt-get purge -y docker-ce docker-ce-cli containerd.io || true
rm -rf /var/lib/docker
rm -rf /var/lib/containerd

# Kill ports 80, 443, 8090
fuser -k 80/tcp || true
fuser -k 443/tcp || true
fuser -k 8090/tcp || true

# 2. System Updates & Dependencies
echo -e "\n${YELLOW}[2/6] Installing dependencies...${NC}"
apt-get update
apt-get install -y ca-certificates curl gnupg lsb-release git python3 python3-pip python3-venv

# 3. Install Docker (Official Script)
echo -e "\n${YELLOW}[3/6] Installing Docker...${NC}"
mkdir -m 0755 -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 4. Clone & Configure (One-Click Defaults)
echo -e "\n${YELLOW}[4/6] Configuring SMSLY Hosting...${NC}"
INSTALL_DIR="/opt/smsly-hosting"

if [ -d "$INSTALL_DIR" ]; then
    echo "Directory exists, pulling latest..."
    cd $INSTALL_DIR
    git pull origin main || echo "Git pull failed, continuing..."
else
    # Assuming we are running this script FROM the repo or curl
    git clone https://github.com/SMSLYCLOUD/smsly-hosting.git $INSTALL_DIR || true
    cd $INSTALL_DIR
fi

# Generate Secrets
FIELD_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || echo "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=")
SECRET_KEY=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
PUBLIC_IP=$(curl -s ifconfig.me || echo "localhost")

# Write .env aggressively
cat <<EOF > .env
ENVIRONMENT=production
DEBUG=False
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$FIELD_KEY
DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@db:5432/smsly_hosting
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_USER=smsly_admin
POSTGRES_DB=smsly_hosting
DOMAIN=$PUBLIC_IP
ALLOWED_HOSTS=$PUBLIC_IP,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://$PUBLIC_IP:8090,http://localhost:8090,https://hosting.smsly.cloud

# GitHub OAuth (Placeholders - User must configure)
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

# Admin Defaults
ADMIN_USER=admin
ADMIN_EMAIL=admin@$PUBLIC_IP
ADMIN_PASSWORD=admin
EOF

# 5. Deploy Stack
echo -e "\n${YELLOW}[5/6] Deploying Containers (Port 8090)...${NC}"
# Ensure we use the production compose file
COMPOSE_FILE="docker-compose.prod.yml"

docker compose -f $COMPOSE_FILE up -d --build

# 6. Post-Install Setup
echo -e "\n${YELLOW}[6/6] Finalizing Setup...${NC}"
echo "Waiting for database (15s)..."
sleep 15

# Run Migrations Explicitly
echo "Running database migrations..."
docker compose -f $COMPOSE_FILE exec -T backend python manage.py migrate --noinput || echo "Migration failed!"

# Create Admin User
# We use 'backend' service name as defined in docker-compose.prod.yml
echo "Creating admin user..."
docker compose -f $COMPOSE_FILE exec -T backend python manage.py createsuperuser --noinput --username admin --email admin@example.com || true

# Set password manually
docker compose -f $COMPOSE_FILE exec -T backend python manage.py shell -c "from django.contrib.auth import get_user_model; User = get_user_model(); u = User.objects.filter(username='admin').first(); u.set_password('admin'); u.save()" 2>/dev/null || true

echo -e "\n${GREEN}==============================================${NC}"
echo -e "${GREEN}   INSTALLATION COMPLETE${NC}"
echo -e "${GREEN}==============================================${NC}"
echo -e "Dashboard: http://$PUBLIC_IP:8090"
echo -e "Admin Panel: http://$PUBLIC_IP:8090/admin"
echo -e "Username: admin"
echo -e "Password: admin"
echo -e "----------------------------------------------"
