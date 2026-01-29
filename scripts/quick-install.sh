#!/bin/bash
# =============================================================================
#   SMSly Hosting - AGGRESSIVE Quick Installer
#   Version: 3.0.0
#   
#   ONE-LINER INSTALL:
#   curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/scripts/quick-install.sh | sudo bash -s -- --domain=hosting.yoursite.com --email=admin@yoursite.com
#
#   OR for fully automatic mode:
#   curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/scripts/quick-install.sh | sudo bash -s -- --auto --domain=hosting.yoursite.com --email=admin@yoursite.com
# =============================================================================

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Defaults
INSTALL_DIR="/opt/smsly-hosting"
DOMAIN_NAME=""
ADMIN_EMAIL=""
AUTO_MODE=false
SKIP_SSL=false
GITHUB_REPO="https://github.com/SMSLYCLOUD/smsly-hosting.git"
GITHUB_RAW="https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main"

# Banner
banner() {
    echo -e "${CYAN}"
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║       ███████╗███╗   ███╗███████╗██╗  ██╗   ██╗               ║"
    echo "║       ██╔════╝████╗ ████║██╔════╝██║  ╚██╗ ██╔╝               ║"
    echo "║       ███████╗██╔████╔██║███████╗██║   ╚████╔╝                ║"
    echo "║       ╚════██║██║╚██╔╝██║╚════██║██║    ╚██╔╝                 ║"
    echo "║       ███████║██║ ╚═╝ ██║███████║███████╗██║                  ║"
    echo "║       ╚══════╝╚═╝     ╚═╝╚══════╝╚══════╝╚═╝  HOSTING         ║"
    echo "╠═══════════════════════════════════════════════════════════════╣"
    echo "║           🚀 AGGRESSIVE QUICK INSTALLER v3.0                  ║"
    echo "║           Deploy your own PaaS in 2 minutes!                  ║"
    echo "╚═══════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --domain=*)
                DOMAIN_NAME="${1#*=}"
                shift
                ;;
            --email=*)
                ADMIN_EMAIL="${1#*=}"
                shift
                ;;
            --auto|--yes|-y)
                AUTO_MODE=true
                shift
                ;;
            --skip-ssl)
                SKIP_SSL=true
                shift
                ;;
            --help|-h)
                echo "Usage: ./quick-install.sh [OPTIONS]"
                echo ""
                echo "Options:"
                echo "  --domain=DOMAIN    Your public domain (required)"
                echo "  --email=EMAIL      Admin email for SSL certs (required)"
                echo "  --auto, -y         Full automatic mode, no prompts"
                echo "  --skip-ssl         Skip SSL certificate setup"
                echo ""
                echo "Example:"
                echo '  curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/scripts/quick-install.sh | sudo bash -s -- --domain=hosting.example.com --email=admin@example.com --auto'
                exit 0
                ;;
            *)
                echo -e "${RED}Unknown option: $1${NC}"
                exit 1
                ;;
        esac
    done
}

log() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
fail() { echo -e "${RED}[✗]${NC} $1"; exit 1; }
step() { echo -e "${BLUE}[→]${NC} $1"; }

# Pre-flight checks
preflight() {
    step "Running pre-flight checks..."
    
    # Must be root
    [[ $EUID -ne 0 ]] && fail "Please run as root: sudo bash $0"
    
    # Check OS
    if [[ -f /etc/os-release ]]; then
        source /etc/os-release
        case "$ID" in
            ubuntu|debian) log "Detected $PRETTY_NAME" ;;
            *) warn "Untested OS: $ID. Proceeding anyway..." ;;
        esac
    fi
    
    # Check RAM
    TOTAL_RAM=$(free -m | awk '/^Mem:/{print $2}')
    [[ $TOTAL_RAM -lt 2000 ]] && warn "Low RAM detected (${TOTAL_RAM}MB). Minimum 2GB recommended."
    
    # Prompt for missing args if not in auto mode
    if [[ -z "$DOMAIN_NAME" ]]; then
        if [[ "$AUTO_MODE" == true ]]; then
            fail "--domain is required in auto mode"
        fi
        read -rp "Enter your domain (e.g., hosting.smsly.cloud): " DOMAIN_NAME
    fi
    
    if [[ -z "$ADMIN_EMAIL" ]]; then
        if [[ "$AUTO_MODE" == true ]]; then
            ADMIN_EMAIL="admin@${DOMAIN_NAME}"
            warn "Using default email: $ADMIN_EMAIL"
        else
            read -rp "Enter admin email (for SSL certs): " ADMIN_EMAIL
        fi
    fi
    
    log "Domain: $DOMAIN_NAME"
    log "Email: $ADMIN_EMAIL"
}

# Install dependencies aggressively
install_deps() {
    step "Installing dependencies (aggressive mode)..."
    
    # Force non-interactive
    export DEBIAN_FRONTEND=noninteractive
    
    # Update & upgrade
    apt-get update -qq
    apt-get upgrade -y -qq
    
    # Essential packages
    apt-get install -y -qq \
        curl git wget unzip \
        ufw fail2ban \
        nginx certbot python3-certbot-nginx \
        python3-pip \
        jq ca-certificates gnupg lsb-release
    
    log "System packages installed"
    
    # Docker - aggressive install
    if ! command -v docker &>/dev/null; then
        step "Installing Docker..."
        curl -fsSL https://get.docker.com | sh
        systemctl enable --now docker
        log "Docker installed"
    else
        log "Docker already installed"
    fi
    
    # Docker Compose
    if ! docker compose version &>/dev/null; then
        apt-get install -y -qq docker-compose-plugin
        log "Docker Compose installed"
    fi
}

# Firewall setup
setup_firewall() {
    step "Configuring firewall..."
    ufw --force reset >/dev/null 2>&1 || true
    ufw default deny incoming
    ufw default allow outgoing
    ufw allow ssh
    ufw allow http
    ufw allow https
    ufw allow 6443/tcp  # K8s API
    ufw --force enable
    log "Firewall configured"
    
    # Fail2ban
    systemctl enable --now fail2ban
    log "Fail2ban enabled"
}

# Clone from GitHub
clone_repo() {
    step "Downloading SMSly Hosting from GitHub..."
    
    rm -rf "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    
    # Try git clone first, fallback to tarball
    if command -v git &>/dev/null; then
        git clone --depth 1 "$GITHUB_REPO" "$INSTALL_DIR" 2>/dev/null || {
            warn "Git clone failed, trying tarball..."
            curl -fsSL "https://github.com/SMSLYCLOUD/smsly-hosting/archive/refs/heads/main.tar.gz" | tar -xz -C /tmp
            mv /tmp/smsly-hosting-main/* "$INSTALL_DIR/"
        }
    else
        curl -fsSL "https://github.com/SMSLYCLOUD/smsly-hosting/archive/refs/heads/main.tar.gz" | tar -xz -C /tmp
        mv /tmp/smsly-hosting-main/* "$INSTALL_DIR/"
    fi
    
    log "Repository downloaded to $INSTALL_DIR"
}

# Generate configuration
generate_config() {
    step "Generating production configuration..."
    
    # Generate secrets
    pip3 install -q cryptography 2>/dev/null || true
    ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || openssl rand -base64 32)
    SECRET_KEY=$(openssl rand -hex 50)
    
    # Create .env
    cat > "$INSTALL_DIR/.env" <<EOF
# ==============================================
# SMSly Hosting - Production Configuration
# Generated: $(date -Iseconds)
# ==============================================

# Security
DEBUG=False
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$ENCRYPTION_KEY

# Domain & CORS
ALLOWED_HOSTS=$DOMAIN_NAME,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://$DOMAIN_NAME
CORS_ALLOW_ALL=False
CORS_ALLOWED_ORIGINS=https://$DOMAIN_NAME

# Database & Redis
DATABASE_URL=postgres://postgres:postgres@db:5432/smsly_hosting
REDIS_URL=redis://redis:6379/0

# Container Registry
CONTAINER_REGISTRY_URL=registry.smsly.cloud
REGISTRY_USER=
REGISTRY_PASSWORD=

# API URLs
NEXT_PUBLIC_API_URL=https://$DOMAIN_NAME/api/v1

# SMSLY Platform (Optional)
SMSLY_SMS_API_URL=http://smsly-sms:8000/api/v1
SMSLY_VOICE_API_URL=http://smsly-voice:8000/api/v1
SMSLY_PLATFORM_API_URL=http://smsly-platform-api:8000/api/v1
SMSLY_INTERNAL_API_KEY=

# Alerting
ALERT_PHONE_NUMBER=
CRITICAL_ALERT_PHONE=
NOTIFY_ON_SUCCESS=False

# Docker Addon Network
DOCKER_NETWORK=smsly-hosting-network

# Logging
DJANGO_LOG_LEVEL=INFO
EOF
    
    log "Configuration generated at $INSTALL_DIR/.env"
}

# Setup Nginx
setup_nginx() {
    step "Configuring Nginx reverse proxy..."
    
    # Add rate limiting if not exists
    grep -q "limit_req_zone" /etc/nginx/nginx.conf || \
        sed -i '/http {/a\    limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;' /etc/nginx/nginx.conf
    
    cat > /etc/nginx/sites-available/smsly-hosting <<EOF
server {
    listen 80;
    server_name $DOMAIN_NAME;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # Frontend
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # API
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        limit_req zone=api burst=20 nodelay;
    }

    # WebSocket
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_read_timeout 86400;
    }
}
EOF
    
    ln -sf /etc/nginx/sites-available/smsly-hosting /etc/nginx/sites-enabled/
    rm -f /etc/nginx/sites-enabled/default
    nginx -t && systemctl restart nginx
    
    log "Nginx configured"
}

# Setup SSL
setup_ssl() {
    if [[ "$SKIP_SSL" == true ]] || [[ "$DOMAIN_NAME" == "localhost" ]]; then
        warn "Skipping SSL setup"
        return
    fi
    
    step "Requesting SSL certificate..."
    certbot --nginx -d "$DOMAIN_NAME" \
        --non-interactive --agree-tos \
        -m "$ADMIN_EMAIL" \
        --redirect || warn "SSL setup failed. Run 'certbot --nginx -d $DOMAIN_NAME' later."
    
    log "SSL configured"
}

# Start services
start_services() {
    step "Starting Docker services..."
    
    cd "$INSTALL_DIR"
    
    # Create addon network
    docker network create smsly-hosting-network 2>/dev/null || true
    
    # Build and start
    docker compose up -d --build
    
    log "Docker services started"
    
    # Wait for DB
    step "Waiting for database..."
    sleep 10
    
    # Run migrations
    step "Running database migrations..."
    docker compose exec -T backend python manage.py migrate --noinput 2>/dev/null || warn "Migrations may need manual run"
    
    log "Migrations complete"
}

# Finish
finish() {
    echo ""
    echo -e "${CYAN}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}               ${GREEN}✅ INSTALLATION COMPLETE!${NC}                      ${CYAN}║${NC}"
    echo -e "${CYAN}╠═══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${CYAN}║${NC}                                                               ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}   ${GREEN}Dashboard:${NC}  https://$DOMAIN_NAME                  ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}   ${GREEN}API:${NC}        https://$DOMAIN_NAME/api/v1/         ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}   ${GREEN}Admin:${NC}      https://$DOMAIN_NAME/api/admin/      ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                               ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}   ${YELLOW}Config:${NC}     $INSTALL_DIR/.env           ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}   ${YELLOW}Logs:${NC}       docker compose logs -f                 ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}                                                               ${CYAN}║${NC}"
    echo -e "${CYAN}╠═══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${CYAN}║${NC}   ${BLUE}Next Steps:${NC}                                             ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}   1. Create admin: docker compose exec backend \\             ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}         python manage.py createsuperuser                     ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}   2. Configure Container Registry in .env                    ${CYAN}║${NC}"
    echo -e "${CYAN}║${NC}   3. Deploy a Worker Node for K8s workloads                  ${CYAN}║${NC}"
    echo -e "${CYAN}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

# Main
main() {
    banner
    parse_args "$@"
    preflight
    install_deps
    setup_firewall
    clone_repo
    generate_config
    setup_nginx
    setup_ssl
    start_services
    finish
}

main "$@"
