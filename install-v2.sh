#!/bin/bash
set -e

# SMSLY Hosting v2 - One-Command Installer
# Supported OS: Ubuntu 22.04+
# Installs Docker, Caddy/Traefik, Postgres, Redis, and the SMSLY Platform.

echo "=================================================="
echo "      SMSLY HOSTING v2 - The Hyperscale PaaS      "
echo "=================================================="

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

# Build & Start
echo "Starting services..."
docker compose -f docker-compose.prod.yml up -d --build

echo "=================================================="
echo "Installation Complete!"
echo "Dashboard: https://$DOMAIN"
echo "Admin User: Created on first launch"
echo "=================================================="
