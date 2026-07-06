#!/bin/bash

# =============================================================================
# SMSLY Hosting - Podman Installer (Experimental)
# =============================================================================
# This script installs Podman and configures it to emulate Docker.
# It is designed for users who prefer Podman over Docker.
# =============================================================================

set -euo pipefail

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'
# Validate and safely detect a usable IPv4 address for installer defaults.
is_valid_ipv4() {
    local ip="$1"
    local octet

    [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
    IFS='.' read -r o1 o2 o3 o4 <<< "$ip"
    for octet in "$o1" "$o2" "$o3" "$o4"; do
        [[ "$octet" =~ ^[0-9]+$ ]] || return 1
        [ "$octet" -ge 0 ] && [ "$octet" -le 255 ] || return 1
    done
    return 0
}

detect_public_ip() {
    local candidate=""
    local endpoint=""
    local endpoints=(
        "https://api.ipify.org"
        "https://ifconfig.me/ip"
        "https://ipv4.icanhazip.com"
    )

    for endpoint in "${endpoints[@]}"; do
        candidate="$(curl -4 -fsS -m 5 "$endpoint" 2>/dev/null | tr -d '\r\n' || true)"
        if is_valid_ipv4 "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done

    candidate="$(hostname -I 2>/dev/null | awk '{print $1}' | tr -d '\r\n' || true)"
    if is_valid_ipv4 "$candidate"; then
        echo "$candidate"
        return 0
    fi

    echo "127.0.0.1"
    return 0
}

echo -e "${YELLOW}Starting Podman installation...${NC}"

# 1. Cleanup Old Docker/Podman
echo -e "\n${YELLOW}[1/6] Cleaning up conflicting container engines...${NC}"
systemctl stop docker podman || true
apt-get remove -y docker docker-engine docker.io containerd runc podman || true
rm -rf /var/lib/docker
rm -rf /var/run/docker.sock

# 2. Install Podman
echo -e "\n${YELLOW}[2/6] Installing Podman...${NC}"
. /etc/os-release
echo "deb https://download.opensuse.org/repositories/devel:/kubic:/libcontainers:/stable/xUbuntu_${VERSION_ID}/ /" | tee /etc/apt/sources.list.d/devel:kubic:libcontainers:stable.list
curl -L "https://download.opensuse.org/repositories/devel:/kubic:/libcontainers:/stable/xUbuntu_${VERSION_ID}/Release.key" | apt-key add -
apt-get update
apt-get install -y podman podman-docker podman-plugins buildah skopeo python3-pip

# 3. Configure Podman Socket (Rootful for compatibility)
echo -e "\n${YELLOW}[3/6] Configuring Podman Socket...${NC}"
systemctl enable --now podman.socket
if [ ! -S /var/run/docker.sock ]; then
    ln -s /run/podman/podman.sock /var/run/docker.sock
    echo "Symlinked /run/podman/podman.sock to /var/run/docker.sock"
fi

# 4. Install Podman Compose
echo -e "\n${YELLOW}[4/6] Installing Podman Compose...${NC}"
pip3 install podman-compose

# 5. Configure SMSLY Hosting
echo -e "\n${YELLOW}[5/6] Configuring SMSLY Hosting...${NC}"
INSTALL_DIR="/opt/smsly-hosting"

if [ ! -d "$INSTALL_DIR" ]; then
    git clone https://github.com/SMSLYCLOUD/smsly-hosting.git "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"

# Generate Secrets if missing
if [ ! -f .env ]; then
    FIELD_KEY=$(python3 -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
    SECRET_KEY=$(openssl rand -hex 32)
    POSTGRES_PASSWORD=$(openssl rand -hex 16)
    REDIS_PASSWORD=$(openssl rand -hex 16)
    RABBITMQ_PASSWORD=$(openssl rand -hex 16)
    GATEWAY_SECRET=$(openssl rand -hex 32)
    GITHUB_WEBHOOK_SECRET=$(openssl rand -hex 32)
    PUBLIC_IP="$(detect_public_ip)"
    ADMIN_PASS="$(openssl rand -hex 16)"

    cat <<EOF > .env
ENVIRONMENT=production
DEBUG=False
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$FIELD_KEY
DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@db:5432/smsly_hosting
REDIS_PASSWORD=$REDIS_PASSWORD
RABBITMQ_PASSWORD=$RABBITMQ_PASSWORD
REDIS_URL=redis://:$REDIS_PASSWORD@redis-primary:6379/0
CELERY_BROKER_URL=amqp://smsly_user:$RABBITMQ_PASSWORD@rabbitmq:5672//
GATEWAY_SECRET=$GATEWAY_SECRET
GITHUB_WEBHOOK_SECRET=$GITHUB_WEBHOOK_SECRET
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_USER=smsly_admin
POSTGRES_DB=smsly_hosting
DOMAIN=$PUBLIC_IP
ALLOWED_HOSTS=$PUBLIC_IP,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://$PUBLIC_IP:8090,http://localhost:8090,http://$PUBLIC_IP
CORS_ALLOWED_ORIGINS=http://$PUBLIC_IP:8090,http://$PUBLIC_IP

# Admin bootstrap (used on first boot by backend/entrypoint.sh)
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_EMAIL=admin@$PUBLIC_IP
DJANGO_SUPERUSER_PASSWORD=$ADMIN_PASS
EOF

    # Save credentials to secure file (NOT echoed to terminal)
    CREDENTIALS_FILE="$INSTALL_DIR/.credentials"
    cat > "$CREDENTIALS_FILE" <<CREDS
# SMSLY Hosting Admin Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE
Username: admin
Password: $ADMIN_PASS
CREDS
    chmod 600 "$CREDENTIALS_FILE"
fi

# 6. Deploy
echo -e "\n${YELLOW}[6/6] Deploying with Podman Compose...${NC}"
# Use standard docker-compose.prod.yml but run with podman-compose
podman-compose -f docker-compose.prod.yml up -d

echo -e "\n${GREEN}Installation Complete (Podman Mode)${NC}"
echo "Note: Monitor logs with 'podman logs -f smsly-hosting-backend-1'"
