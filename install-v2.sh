#!/bin/bash
set -e

# Redirect output to log file and console
LOG_FILE="install.log"
exec > >(tee -a "$LOG_FILE") 2>&1

# SMSLY Hosting v2 - One-Command Installer
# Supported OS: Ubuntu 22.04+
# Installs Docker, Caddy/Traefik, Postgres, Redis, and the SMSLY Platform.

echo "=================================================="
echo "      SMSLY HOSTING v2 - The Hyperscale PaaS      "
echo "=================================================="
echo "Log file: $LOG_FILE"

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root (sudo bash install-v2.sh)"
  exit
fi

DOMAIN=$1
EMAIL=$2

if [ -z "$DOMAIN" ]; then
    read -p "Enter Domain (e.g. hosting.smsly.cloud): " DOMAIN
fi

if [ -z "$EMAIL" ]; then
    read -p "Enter Email for SSL: " EMAIL
fi

echo "Installing dependencies..."
apt-get update && apt-get install -y curl git python3-pip openssl

# Install Docker if missing
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
fi

# Clone/Pull Repo Logic
if [ -f "docker-compose.prod.yml" ]; then
    echo "Running from repository root."
    git pull origin main || echo "Git pull failed, continuing..."
else
    if [ ! -d "smsly-hosting" ]; then
        git clone https://github.com/SMSLYCLOUD/smsly-hosting.git
        cd smsly-hosting
    else
        cd smsly-hosting
        git pull origin main
    fi
fi

# Setup Environment
if [ ! -f .env ]; then
    cp .env.example .env
    sed -i "s/example.com/$DOMAIN/g" .env

    # Generate Secrets
    echo "Generating secure keys..."

    # SECRET_KEY (Django)
    SECRET=$(openssl rand -base64 40 | tr -dc 'a-zA-Z0-9' | head -c 50)
    sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET/" .env

    # FIELD_ENCRYPTION_KEY (Fernet, 32 bytes url-safe base64)
    # Note: openssl rand -base64 32 returns 44 chars. We need to make it url-safe (+ -> -, / -> _)
    ENCRYPT_KEY=$(openssl rand -base64 32 | tr '+/' '-_')

    if grep -q "FIELD_ENCRYPTION_KEY=" .env; then
        sed -i "s|FIELD_ENCRYPTION_KEY=.*|FIELD_ENCRYPTION_KEY=$ENCRYPT_KEY|" .env
    else
        echo "FIELD_ENCRYPTION_KEY=$ENCRYPT_KEY" >> .env
    fi
fi

# Ensure ALLOWED_HOSTS is correctly configured (fixes 502/400 errors)
# This runs even if .env already exists
echo "Updating ALLOWED_HOSTS in .env..."
NEW_ALLOWED_HOSTS="localhost,127.0.0.1,backend,$DOMAIN"
if grep -q "ALLOWED_HOSTS=" .env; then
    sed -i "s|ALLOWED_HOSTS=.*|ALLOWED_HOSTS=$NEW_ALLOWED_HOSTS|" .env
else
    echo "ALLOWED_HOSTS=$NEW_ALLOWED_HOSTS" >> .env
fi

# Ensure CSRF/CORS are updated (fixes 403 Forbidden on Login)
echo "Updating CSRF/CORS settings..."
NEW_CORS="http://localhost:3000,https://$DOMAIN"
NEW_CSRF="https://$DOMAIN"

if grep -q "CORS_ALLOWED_ORIGINS=" .env; then
    sed -i "s|CORS_ALLOWED_ORIGINS=.*|CORS_ALLOWED_ORIGINS=$NEW_CORS|" .env
else
    echo "CORS_ALLOWED_ORIGINS=$NEW_CORS" >> .env
fi

if grep -q "CSRF_TRUSTED_ORIGINS=" .env; then
    sed -i "s|CSRF_TRUSTED_ORIGINS=.*|CSRF_TRUSTED_ORIGINS=$NEW_CSRF|" .env
else
    echo "CSRF_TRUSTED_ORIGINS=$NEW_CSRF" >> .env
fi

# Update Nginx Configuration
if [ -f nginx.conf ]; then
    echo "Updating nginx.conf with domain $DOMAIN..."
    # Replace hardcoded domain in server_name and ssl paths
    sed -i "s/hosting.smsly.cloud/$DOMAIN/g" nginx.conf
fi

# Check/Generate SSL Certificates to prevent Nginx crash
CERT_PATH="/etc/letsencrypt/live/$DOMAIN"
if [ ! -f "$CERT_PATH/fullchain.pem" ]; then
    echo "WARNING: SSL certificates for $DOMAIN not found."
    echo "Generating self-signed certificates so Nginx can start..."
    mkdir -p "$CERT_PATH"
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "$CERT_PATH/privkey.pem" \
        -out "$CERT_PATH/fullchain.pem" \
        -subj "/CN=$DOMAIN"
    echo "Self-signed certificates created at $CERT_PATH"
fi

# Build & Start
echo "Starting services..."
docker compose -f docker-compose.prod.yml up -d --build

echo "Waiting for services to be healthy..."
sleep 10
if docker compose -f docker-compose.prod.yml ps | grep -q "unhealthy"; then
    echo "WARNING: Some services are unhealthy. Checking logs..."
    docker compose -f docker-compose.prod.yml logs --tail=20 backend frontend nginx
    echo "Check configuration and try again."
else
    echo "Services started successfully."
fi

# Create Default Admin User
echo "Creating default admin user..."
ADMIN_EMAIL="admin@smsly.cloud"
ADMIN_USER="admin"
# Generate a random password if not provided
ADMIN_PASS=$(openssl rand -base64 12)

# Try to create superuser. This might fail if it already exists, so we catch the error but don't exit script.
set +e
docker compose -f docker-compose.prod.yml exec -e DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASS" -e DJANGO_SUPERUSER_EMAIL="$ADMIN_EMAIL" -e DJANGO_SUPERUSER_USERNAME="$ADMIN_USER" backend python manage.py createsuperuser --noinput > /dev/null 2>&1
CREATION_STATUS=$?
set -e

echo "=================================================="
echo "Installation Complete!"
echo "Dashboard: https://$DOMAIN"
if [ $CREATION_STATUS -eq 0 ]; then
    echo "Admin User: $ADMIN_EMAIL (or username: $ADMIN_USER)"
    echo "Password: $ADMIN_PASS"
    echo "PLEASE SAVE THIS PASSWORD NOW."
else
    echo "Admin User: admin / admin@smsly.cloud"
    echo "Password: (Hidden - User already exists or creation failed)"
fi
echo "=================================================="
