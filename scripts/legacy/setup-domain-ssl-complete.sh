#!/bin/bash

# =============================================================================
# SMSLY Custom Domain SSL Complete Setup Script
# VERSION: 1.0.0
# =============================================================================
# This script provides a complete solution for the custom domain SSL issue.
# It installs all necessary components and ensures the system works permanently.
#
# Usage:
#   sudo bash setup-domain-ssl-complete.sh
# =============================================================================

set -euo pipefail

# Configuration
INSTALL_DIR="/opt/smsly-hosting"
DOMAIN_SSL_MANAGER="$INSTALL_DIR/smsly-domain-ssl-manager.sh"
DOMAIN_SSL_SERVICE="/etc/systemd/system/smsly-domain-ssl.service"
DOMAIN_SSL_TIMER="/etc/systemd/system/smsly-domain-ssl.timer"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() {
    echo -e "${BLUE}SMSLY Domain SSL Setup${NC} $1"
}

log_success() {
    echo -e "${GREEN}SMSLY Domain SSL Setup${NC} $1"
}

log_error() {
    echo -e "${RED}SMSLY Domain SSL Setup${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}SMSLY Domain SSL Setup${NC} $1"
}

# Check if running as root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}ERROR: This script must be run as root.${NC}"
        echo "Please use: sudo bash $0"
        exit 1
    fi
}

# Check if SMSLY is installed
check_smsly_installed() {
    if [ ! -d "$INSTALL_DIR" ] || [ ! -f "$INSTALL_DIR/docker-compose.prod.yml" ]; then
        log_error "SMSLY not found in $INSTALL_DIR"
        echo "Please install SMSLY first using the official installer."
        exit 1
    fi
}

# Check Docker is running
check_docker() {
    if ! docker info ; then
        log_error "Docker is not running."
        echo "Please start Docker first:"
        echo "  systemctl start docker"
        echo "  systemctl enable docker"
        exit 1
    fi
}

# Install domain SSL manager
install_domain_ssl_manager() {
    log "Installing domain SSL manager"
    
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
}

# Install systemd service
install_systemd_service() {
    log "Installing systemd service"
    
    # Copy service file
    if [ -f "smsly-domain-ssl.service" ]; then
        cp smsly-domain-ssl.service "$DOMAIN_SSL_SERVICE"
        log_success "Systemd service installed"
    else
        log_error "Systemd service file not found"
        exit 1
    fi
    
    # Copy timer file
    if [ -f "smsly-domain-ssl.timer" ]; then
        cp smsly-domain-ssl.timer "$DOMAIN_SSL_TIMER"
        log_success "Systemd timer installed"
    else
        log_error "Systemd timer file not found"
        exit 1
    fi
    
    # Reload systemd
    systemctl daemon-reload
}

# Enable and start services
enable_and_start_services() {
    log "Enabling and starting services"
    
    # Enable and start the main service
    systemctl enable smsly-domain-ssl.service
    systemctl start smsly-domain-ssl.service
    
    # Enable and start the timer
    systemctl enable smsly-domain-ssl.timer
    systemctl start smsly-domain-ssl.timer
    
    log_success "Services enabled and started"
}

# Verify installation
verify_installation() {
    log "Verifying installation"
    
    # Check service status
    if systemctl is-active --quiet smsly-domain-ssl.service; then
        log_success "Domain SSL service is running"
    else
        log_error "Domain SSL service is not running"
        systemctl status smsly-domain-ssl.service
        return 1
    fi
    
    # Check timer status
    if systemctl is-active --quiet smsly-domain-ssl.timer; then
        log_success "Domain SSL timer is running"
    else
        log_error "Domain SSL timer is not running"
        systemctl status smsly-domain-ssl.timer
        return 1
    fi
    
    # Check domain SSL manager
    if [ -x "$DOMAIN_SSL_MANAGER" ]; then
        log_success "Domain SSL manager is executable"
    else
        log_error "Domain SSL manager is not executable"
        return 1
    fi
    
    # Test domain verification
    log "Testing domain verification"
    if timeout -k 5 60 "$DOMAIN_SSL_MANAGER" start; then
        log_success "Domain verification test passed"
    else
        log_warning "Domain verification test timed out"
    fi
}

# Display status and next steps
display_status() {
    echo -e "\n${GREEN}SMSLY Custom Domain SSL Setup Complete!${NC}"
    echo "=============================================="
    echo ""
    echo "The following services are now running:"
    echo "  • smsly-domain-ssl.service - Main domain SSL service"
    echo "  • smsly-domain-ssl.timer - Domain verification timer (runs every 5 minutes)"
    echo ""
    echo "Service Status:"
    systemctl status smsly-domain-ssl.service --no-pager -l
    echo ""
    systemctl status smsly-domain-ssl.timer --no-pager -l
    echo ""
    echo "Management Commands:"
    echo "  • Start services:   systemctl start smsly-domain-ssl.service"
    echo "  • Stop services:    systemctl stop smsly-domain-ssl.service"
    echo "  • Restart services: systemctl restart smsly-domain-ssl.service"
    echo "  • View status:      systemctl status smsly-domain-ssl.service"
    echo "  • View logs:        journalctl -u smsly-domain-ssl.service -f"
    echo "  • View timer logs:  journalctl -u smsly-domain-ssl.timer -f"
    echo ""
    echo "Domain SSL Manager:"
    echo "  • Start manually:  $DOMAIN_SSL_MANAGER start"
    echo "  • Check status:     $DOMAIN_SSL_MANAGER status"
    echo "  • View logs:       $DOMAIN_SSL_MANAGER logs"
    echo ""
    echo "Next Steps:"
    echo "1. Add a custom domain that points to your VPS IP"
    echo "2. The system will automatically verify DNS and issue SSL"
    echo "3. Monitor domain status using: $DOMAIN_SSL_MANAGER status"
    echo ""
    echo "For more information, see: FINAL_SOLUTION.md"
}

# Main setup function
main() {
    echo -e "${BLUE}SMSLY Custom Domain SSL Complete Setup${NC}"
    echo "====================================="
    echo ""
    
    check_root
    check_smsly_installed
    check_docker
    
    log "Starting installation..."
    
    install_domain_ssl_manager
    install_systemd_service
    enable_and_start_services
    verify_installation
    
    display_status
}

# Run main function
main "$@"