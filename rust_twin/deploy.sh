#!/usr/bin/env bash

set -e

echo "=========================================================="
echo "    CloudNeuron (Rust Twin) Automated Deployment script     "
echo "=========================================================="

# 1. Gather configuration
if [ ! -f .env ]; then
    echo "[*] Initializing new .env configuration..."
    # Simplified setup for the Rust twin deployment
    read -p "Enter your domain or IP address (e.g., neuron.cloud.com): " DOMAIN_INPUT
    read -p "Enter admin email for Let's Encrypt SSL (e.g., admin@cloud.com): " EMAIL_INPUT

    cat <<EOF > .env
DOMAIN=$DOMAIN_INPUT
ACME_EMAIL=$EMAIL_INPUT
SECRET_KEY=$(openssl rand -hex 32)
FIELD_ENCRYPTION_KEY=$(openssl rand -base64 32)
POSTGRES_USER=smsly_admin
POSTGRES_PASSWORD=$(openssl rand -hex 16)
POSTGRES_DB=smsly_hosting
REDIS_PASSWORD=$(openssl rand -hex 16)
DATABASE_URL=postgres://smsly_admin:$(openssl rand -hex 16)@db:5432/smsly_hosting
REDIS_HOST=redis
RUST_LOG=info,api=debug,core=debug
EOF
    echo "[*] .env created successfully with randomized secure secrets."
else
    echo "[*] Existing .env found, proceeding with current configuration."
fi

# Load variables
source .env

# 2. Start the Internal Platform (Docker Compose)
echo "[*] Bringing up the internal platform components (DB, Redis, API, Worker, Nginx, Frontend)..."
docker compose up -d --build

# 3. Configure External Ingress Proxy (Caddy)
echo "[*] Setting up Caddy for automatic SSL termination..."

# Check if Caddy is already running on the host system natively.
if command -v caddy &> /dev/null; then
    echo "[*] Formatting Caddyfile with provided domain: $DOMAIN"

    # We pass the env vars to Caddy and reload the config.
    # In a full deployment script, this would overwrite `/etc/caddy/Caddyfile` and run `systemctl reload caddy`.
    export DOMAIN
    export ACME_EMAIL

    # Run the syntax check to ensure it's valid
    caddy fmt --overwrite Caddyfile

    echo "[*] You can now reload your system Caddy service or start it locally."
    echo "[*] Suggestion: caddy run --config Caddyfile"
else
    echo "[!] Caddy not found on host. The application is running on localhost:8090 via Nginx."
    echo "[!] To enable SSL, install Caddy on the host system: https://caddyserver.com/docs/install"
fi

echo "=========================================================="
echo "    Deployment Complete!   "
echo "    Internal Router: http://localhost:8090"
if [ -n "$DOMAIN" ]; then
    echo "    Public URL: https://$DOMAIN"
fi
echo "=========================================================="