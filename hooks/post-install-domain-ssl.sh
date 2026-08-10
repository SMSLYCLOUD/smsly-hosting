#!/bin/bash
# SMSLY Post-Install Hook: Custom Domain SSL
# This script runs after SMSLY installation to ensure domain SSL services work

set -euo pipefail

INSTALL_DIR="/opt/smsly-hosting"
DOMAIN_SSL_MANAGER="$INSTALL_DIR/smsly-domain-ssl-manager.sh"

log() {
    echo "Domain SSL: $1" | tee -a /var/log/smsly-install.log
}

log "Setting up custom domain SSL services"

# Start domain SSL services
if [ -f "$DOMAIN_SSL_MANAGER" ]; then
    log "Starting domain SSL services"
    $DOMAIN_SSL_MANAGER start
    log "Enabling auto-start on boot"
    $DOMAIN_SSL_MANAGER enable
else
    log "Domain SSL manager not found, skipping"
fi

log "Custom domain SSL setup completed"
