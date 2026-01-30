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
apt-get update && apt-get install -y curl git python3-pip

# Install Docker if missing
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
fi

# Clone/Pull Repo
if [ ! -d "smsly-hosting" ]; then
    git clone https://github.com/SMSLYCLOUD/smsly-hosting.git
    cd smsly-hosting
else
    cd smsly-hosting
    git pull origin main
fi

# Setup Environment
if [ ! -f .env ]; then
    cp .env.example .env
    sed -i "s/example.com/$DOMAIN/g" .env
    # Generate Secrets
    SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
    sed -i "s/SECRET_KEY=.*/SECRET_KEY=$SECRET/" .env
fi

# Build & Start
echo "Starting services..."
docker compose -f docker-compose.prod.yml up -d --build

echo "=================================================="
echo "Installation Complete!"
echo "Dashboard: https://$DOMAIN"
echo "Admin User: Created on first launch"
echo "=================================================="
