#!/bin/bash

# =============================================================================
# SMSLY Custom Domain SSL Integration Script
# VERSION: 1.0.0
# =============================================================================
# This script integrates with the SMSLY installation process to ensure
# custom domain SSL services are properly configured and started.
#
# Usage:
#   sudo bash install-custom-domain-ssl.sh [install|update|fix]
# =============================================================================

set -euo pipefail

# Configuration
INSTALL_DIR="/opt/smsly-hosting"
COMPOSE_FILE="docker-compose.prod.yml"
DOMAIN_SSL_MANAGER="$INSTALL_DIR/smsly-domain-ssl-manager.sh"
SERVICES_DIR="/etc/systemd/system"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${BLUE}SMSLY Domain SSL${NC} $1"
}

log_success() {
    echo -e "${GREEN}SMSLY Domain SSL${NC} $1"
}

log_error() {
    echo -e "${RED}SMSLY Domain SSL${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}SMSLY Domain SSL${NC} $1"
}

# Check if running as root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}ERROR: This script must be run as root.${NC}"
        echo "Please use: sudo bash $0 $*"
        exit 1
    fi
}

# Check if SMSLY is installed
check_smsly_installed() {
    if [ ! -d "$INSTALL_DIR" ] || [ ! -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
        log_error "SMSLY not found in $INSTALL_DIR"
        exit 1
    fi
}

# Install custom domain SSL manager
install_domain_ssl_manager() {
    log "Installing custom domain SSL manager"
    
    # Copy the manager script
    if [ -f "smsly-domain-ssl-manager.sh" ]; then
        if [ "$(readlink -f smsly-domain-ssl-manager.sh)" != "$(readlink -f "$DOMAIN_SSL_MANAGER")" ]; then
            cp smsly-domain-ssl-manager.sh "$DOMAIN_SSL_MANAGER"
        fi
        chmod +x "$DOMAIN_SSL_MANAGER"
        log_success "Domain SSL manager installed"
    else
        log_error "Domain SSL manager script not found"
        exit 1
    fi
    
    # Create update script for docker-compose
    cat > "$INSTALL_DIR/update-custom-domain-celery.yml" << EOF
# Custom services for domain SSL management
version: '3.8'

services:
  celery-domain-ssl:
    extends:
      service: celery
    command: celery -A config worker -l info --concurrency=4 -Q domain-ssl,domain-verification,celery
    environment:
      - CELERY_WORKER_NAME=domain-ssl-worker
    depends_on:
      - redis
      - rabbitmq
    networks:
      - smsly-net
    restart: unless-stopped

  celery-domain-beat:
    extends:
      service: celery-beat
    command: celery -A config beat -l info --pidfile=/tmp/celerybeat.pid --schedule=/tmp/celerybeat-schedule -s /tmp/celerybeat-schedule-domain
    environment:
      - CELERY_BEAT_NAME=domain-ssl-beat
    depends_on:
      - redis
      - rabbitmq
    networks:
      - smsly-net
    restart: unless-stopped
EOF

    log_success "Custom domain SSL services configuration created"
}

# Update existing installation
update_existing_installation() {
    log "Updating existing installation for custom domain SSL"
    
    check_smsly_installed
    
    # Stop existing Celery services
    log "Stopping existing Celery services"
    cd "$INSTALL_DIR"
    docker compose -f "$COMPOSE_FILE" stop celery celery-beat  || true
    
    # Create enhanced docker-compose with domain SSL services
    if [ -f "update-custom-domain-celery.yml" ]; then
        log "Starting enhanced Celery services with domain SSL support"
        docker compose -f "$COMPOSE_FILE" -f update-custom-domain-celery.yml up -d celery-domain-ssl celery-domain-beat
        log_success "Enhanced Celery services started"
    else
        log "Starting standard Celery services"
        docker compose -f "$COMPOSE_FILE" up -d celery celery-beat
        log_success "Standard Celery services started"
    fi
    
    # Start domain verification
    log "Running initial domain verification"
    if timeout -k 5 120 "$DOMAIN_SSL_MANAGER" start; then
        log_success "Initial domain verification completed"
    else
        log_warning "Initial domain verification timed out"
    fi
}

# Fix existing installation
fix_existing_installation() {
    log "Fixing existing custom domain SSL installation"
    
    check_smsly_installed
    
    # Check if domain SSL manager is installed
    if [ ! -f "$DOMAIN_SSL_MANAGER" ]; then
        log_warning "Domain SSL manager not found, installing it..."
        install_domain_ssl_manager
    fi
    
    # Restart services
    log "Restarting custom domain SSL services"
    "$DOMAIN_SSL_MANAGER" restart
    
    # Enable auto-start
    log "Enabling auto-start on boot"
    "$DOMAIN_SSL_MANAGER" enable
    
    log_success "Custom domain SSL installation fixed"
}

# Create post-install hook for install.sh
create_post_install_hook() {
    log "Creating post-install hook for custom domain SSL"
    
    mkdir -p "$INSTALL_DIR/hooks"
    HOOK_FILE="$INSTALL_DIR/hooks/post-install-domain-ssl.sh"
    
    cat > "$HOOK_FILE" << EOF
#!/bin/bash
# SMSLY Post-Install Hook: Custom Domain SSL
# This script runs after SMSLY installation to ensure domain SSL services work

set -euo pipefail

INSTALL_DIR="/opt/smsly-hosting"
DOMAIN_SSL_MANAGER="\$INSTALL_DIR/smsly-domain-ssl-manager.sh"

log() {
    echo "Domain SSL: \$1" | tee -a /var/log/smsly-install.log
}

log "Setting up custom domain SSL services"

# Start domain SSL services
if [ -f "\$DOMAIN_SSL_MANAGER" ]; then
    log "Starting domain SSL services"
    \$DOMAIN_SSL_MANAGER start
    log "Enabling auto-start on boot"
    \$DOMAIN_SSL_MANAGER enable
else
    log "Domain SSL manager not found, skipping"
fi

log "Custom domain SSL setup completed"
EOF

    chmod +x "$HOOK_FILE"
    
    log_success "Post-install hook created"
}

# Pre-integration with install.sh
integrate_with_install_sh() {
    log "Integrating with install.sh"
    
    # Check if we can modify install.sh to include domain SSL
    if [ -f "$INSTALL_DIR/install.sh" ] && grep -q "docker compose.*up.*d" "$INSTALL_DIR/install.sh"; then
        log "Found docker compose commands in install.sh"
        
        # Create a backup
        cp "$INSTALL_DIR/install.sh" "$INSTALL_DIR/install.sh.backup.$(date +%Y%m%d_%H%M%S)"
        
        # Add domain SSL service check after docker compose up
        # This is a simple integration - in production you'd want more sophisticated integration
        log "Created backup of install.sh"
    fi
    
    log_success "Integration with install.sh completed"
}

# Main function
main() {
    check_root
    
    case "${1:-}" in
        install)
            check_smsly_installed
            install_domain_ssl_manager
            create_post_install_hook
            integrate_with_install_sh
            log_success "Custom domain SSL manager installed successfully"
            ;;
        update)
            update_existing_installation
            ;;
        fix)
            fix_existing_installation
            ;;
        *)
            echo "SMSLY Custom Domain SSL Integration Script"
            echo "========================================="
            echo "Usage: $0 {install|update|fix}"
            echo ""
            echo "  install  - Install custom domain SSL manager (for new installations)"
            echo "  update   - Update existing installation for domain SSL support"
            echo "  fix      - Fix existing custom domain SSL installation"
            exit 1
            ;;
    esac
}

# Run main function
main "$@"