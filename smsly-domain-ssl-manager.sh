#!/bin/bash

# =============================================================================
# SMSLY Custom Domain SSL Service Manager
# VERSION: 1.0.0
# =============================================================================
# This script ensures that custom domain SSL services run permanently and
# automatically restarts them if they fail. It integrates with the existing
# SMSLY installation process and prevents the custom domain SSL issue.
#
# Usage:
#   sudo bash smsly-domain-ssl-manager.sh start    # Start services
#   sudo bash smsly-domain-ssl-manager.sh stop     # Stop services  
#   sudo bash smsly-domain-ssl-manager.sh status   # Check status
#   sudo bash smsly-domain-ssl-manager.sh enable   # Enable auto-start on boot
#   sudo bash smsly-domain-ssl-manager.sh disable  # Disable auto-start
# =============================================================================

set -euo pipefail

# Configuration
INSTALL_DIR="/opt/smsly-hosting"
COMPOSE_FILE="docker-compose.prod.yml"
CELERY_WORKER_SERVICE="celery"
CELERY_BEAT_SERVICE="celery-beat"
DOMAIN_VERIFICATION_SCRIPT="/opt/smsly-hosting/scripts/verify-domains.sh"
LOG_FILE="/var/log/smsly-domain-ssl.log"
PID_FILE="/var/run/smsly-domain-ssl.pid"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Logging function
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${RED}$(date '+%Y-%m-%d %H:%M:%S') - ERROR: $1${NC}" | tee -a "$LOG_FILE"
}

log_success() {
    echo -e "${GREEN}$(date '+%Y-%m-%d %H:%M:%S') - SUCCESS: $1${NC}" | tee -a "$LOG_FILE"
}

log_info() {
    echo -e "${BLUE}$(date '+%Y-%m-%d %H:%M:%S') - INFO: $1${NC}" | tee -a "$LOG_FILE"
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
        echo -e "${RED}ERROR: SMSLY not found in $INSTALL_DIR${NC}"
        exit 1
    fi
}

# Check Docker is running
check_docker() {
    if ! docker info; then
        echo -e "${RED}ERROR: Docker is not running.${NC}"
        echo "Please start Docker first: systemctl start docker"
        exit 1
    fi
}

# Check service status
check_service_status() {
    local service_name="$1"
    if docker compose -f "$INSTALL_DIR/$COMPOSE_FILE" ps -q "$service_name" | grep -q .; then
        if docker inspect -f '{{.State.Status}}' "$(docker compose -f "$INSTALL_DIR/$COMPOSE_FILE" ps -q "$service_name")" | grep -q "running"; then
            return 0
        fi
    fi
    return 1
}

# Start domain verification script
start_domain_verification() {
    log_info "Starting domain verification process"
    
    # Create domain verification script if it doesn't exist
    cat > "$DOMAIN_VERIFICATION_SCRIPT" << 'EOF'
#!/bin/bash
# SMSLY Domain Verification Script
# Uses the backend API to check health and trigger domain verification
# via Celery tasks. Avoids brittle docker exec + Django import overhead.

cd /opt/smsly-hosting

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> /var/log/smsly-domain-ssl.log
}

log "Starting domain verification process"

# Check if backend is running and healthy via HTTP (fast, no Python import overhead)
BACKEND_CID=$(docker compose ps -q backend || true)
if [ -z "$BACKEND_CID" ]; then
    log "ERROR: backend container is not running"
    exit 1
fi

# Wait for the backend to be truly ready (gunicorn workers started).
# Curl may succeed on /health/live before Django is fully bootstrapped.
# Use the public edge (port 80 -> Caddy -> backend); port 8000 is not
# published on the host.
for i in $(seq 1 12); do
    if curl -sS --max-time 5 http://localhost/health/live; then
        break
    fi
    log "Backend not ready yet (attempt $i/12)..."
    sleep 5
done

log "Backend API is responding — dispatching domain verification tasks..."

# Dispatch pending domain verification via Django management command.
# Uses the backend's own manage.py context so DJANGO_SETTINGS_MODULE
# and the Python path are always correct.
# `< /dev/null` is REQUIRED: under a detached screen the docker exec would
# otherwise try to read stdin, get SIGTTIN and stop forever.
docker exec "$BACKEND_CID" python manage.py shell -c "
from apps.domains.models import Domain
from apps.domains.tasks import verify_dns_and_provision_ssl_task

domains = Domain.objects.filter(status__in=['pending', 'dns_pending'])
count = domains.count()
print(f'Processing {count} pending domains...')
for domain in domains:
    print(f'  Queueing verification for: {domain.domain_name}')
    verify_dns_and_provision_ssl_task.delay(domain.id)
print(f'Queued {count} domain verification tasks.')
" < /dev/null 2>&1 | tee -a /var/log/smsly-domain-ssl.log

log "Domain verification process completed"
EOF

    chmod +x "$DOMAIN_VERIFICATION_SCRIPT"
    
    # Run domain verification with 5-minute timeout for cold-start safety
    # (Django import can take 60-90 s on cold container start).
    local _exit_code=0
    timeout 300 "$DOMAIN_VERIFICATION_SCRIPT" || _exit_code=$?
    if [ $_exit_code -eq 0 ]; then
        log_success "Domain verification completed successfully"
    elif [ $_exit_code -eq 124 ]; then
        log_error "Domain verification timed out after 5 minutes"
    else
        log_error "Domain verification failed with exit code $_exit_code"
    fi
}

# Start services
start_services() {
    log_info "Starting SMSLY Custom Domain SSL services"
    
    check_docker
    
    # Start Celery worker
    if check_service_status "$CELERY_WORKER_SERVICE"; then
        log_info "Celery worker is already running"
    else
        log_info "Starting Celery worker"
        cd "$INSTALL_DIR"
        docker compose -f "$COMPOSE_FILE" up -d "$CELERY_WORKER_SERVICE"
        if check_service_status "$CELERY_WORKER_SERVICE"; then
            log_success "Celery worker started successfully"
        else
            log_error "Failed to start Celery worker"
            return 1
        fi
    fi
    
    # Start Celery beat
    if check_service_status "$CELERY_BEAT_SERVICE"; then
        log_info "Celery beat is already running"
    else
        log_info "Starting Celery beat"
        cd "$INSTALL_DIR"
        docker compose -f "$COMPOSE_FILE" up -d "$CELERY_BEAT_SERVICE"
        if check_service_status "$CELERY_BEAT_SERVICE"; then
            log_success "Celery beat started successfully"
        else
            log_error "Failed to start Celery beat"
            return 1
        fi
    fi
    
    # Start domain verification
    start_domain_verification
    
    log_success "All custom domain SSL services started"
}

# Stop services
stop_services() {
    log_info "Stopping SMSLY Custom Domain SSL services"
    
    cd "$INSTALL_DIR"
    
    # Stop Celery beat
    if check_service_status "$CELERY_BEAT_SERVICE"; then
        log_info "Stopping Celery beat"
        docker compose -f "$COMPOSE_FILE" stop "$CELERY_BEAT_SERVICE"
        log_success "Celery beat stopped"
    fi
    
    # Stop Celery worker
    if check_service_status "$CELERY_WORKER_SERVICE"; then
        log_info "Stopping Celery worker"
        docker compose -f "$COMPOSE_FILE" stop "$CELERY_WORKER_SERVICE"
        log_success "Celery worker stopped"
    fi
    
    log_success "All custom domain SSL services stopped"
}

# Check status
check_status() {
    echo -e "${BLUE}SMSLY Custom Domain SSL Service Status${NC}"
    echo "========================================"
    
    cd "$INSTALL_DIR"
    
    # Check Celery worker
    if check_service_status "$CELERY_WORKER_SERVICE"; then
        echo -e "${GREEN}✓ Celery worker: RUNNING${NC}"
        docker compose -f "$COMPOSE_FILE" logs --tail=5 "$CELERY_WORKER_SERVICE" | grep -E "(celery|worker|task)" | tail -3
    else
        echo -e "${RED}✗ Celery worker: STOPPED${NC}"
    fi
    
    # Check Celery beat
    if check_service_status "$CELERY_BEAT_SERVICE"; then
        echo -e "${GREEN}✓ Celery beat: RUNNING${NC}"
        docker compose -f "$COMPOSE_FILE" logs --tail=5 "$CELERY_BEAT_SERVICE" | grep -E "(celery|beat|schedule)" | tail -3
    else
        echo -e "${RED}✗ Celery beat: STOPPED${NC}"
    fi
    
    # Check domain status
    echo -e "${BLUE}\nDomain Status Summary${NC}"
    echo "======================"
    
    if docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell <<< "
from apps.domains.models import Domain, DomainStatus
domains = Domain.objects.all()
print(f'Total domains: {domains.count()}')
active_domains = domains.filter(status=DomainStatus.ACTIVE)
ssl_active_domains = domains.filter(ssl_active=True)
pending_domains = domains.filter(status__in=['pending', 'dns_pending'])
print(f'Active domains: {active_domains.count()} (SSL: {ssl_active_domains.count()}))')
print(f'Pending domains: {pending_domains.count()}')
if pending_domains.count() > 0:
    print('Pending domains:')
    for domain in pending_domains:
        print(f'  - {domain.domain_name} ({domain.status})')
"; then
        echo -e "${GREEN}✓ Domain database accessible${NC}"
    else
        echo -e "${RED}✗ Domain database not accessible${NC}"
    fi
    
    echo -e "\n${BLUE}Log file: $LOG_FILE${NC}"
}

# Enable auto-start on boot
enable_auto_start() {
    log_info "Enabling auto-start on boot"
    
    # Create systemd service file
    cat > /etc/systemd/system/smsly-domain-ssl.service << EOF
[Unit]
Description=SMSLY Custom Domain SSL Services
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$INSTALL_DIR
ExecStart=$0 start
ExecStop=$0 stop
TimeoutStartSec=300
TimeoutStopSec=60
RestartSec=10
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

    # Create systemd timer for periodic domain verification
    cat > /etc/systemd/system/smsly-domain-ssl.timer << EOF
[Unit]
Description=Run SMSLY domain verification every 5 minutes
Requires=smsly-domain-ssl.service

[Timer]
OnCalendar=*:0/5
Unit=smsly-domain-ssl.service
Persistent=true

[Install]
WantedBy=timers.target
EOF

    systemctl daemon-reload
    systemctl enable smsly-domain-ssl.service
    systemctl enable smsly-domain-ssl.timer
    
    log_success "Auto-start enabled on boot"
    log_info "Starting timer for periodic domain verification"
    systemctl start smsly-domain-ssl.timer
}

# Disable auto-start
disable_auto_start() {
    log_info "Disabling auto-start on boot"
    
    systemctl stop smsly-domain-ssl.timer || true
    systemctl disable smsly-domain-ssl.timer || true
    
    systemctl stop smsly-domain-ssl.service || true
    systemctl disable smsly-domain-ssl.service || true
    
    rm -f /etc/systemd/system/smsly-domain-ssl.service
    rm -f /etc/systemd/system/smsly-domain-ssl.timer
    
    systemctl daemon-reload
    
    log_success "Auto-start disabled"
}

# Main function
main() {
    check_root
    check_smsly_installed
    
    case "${1:-}" in
        start)
            start_services
            ;;
        stop)
            stop_services
            ;;
        status)
            check_status
            ;;
        enable)
            enable_auto_start
            ;;
        disable)
            disable_auto_start
            ;;
        restart)
            stop_services
            sleep 2
            start_services
            ;;
        logs)
            tail -f "$LOG_FILE"
            ;;
        *)
            echo "SMSLY Custom Domain SSL Service Manager"
            echo "====================================="
            echo "Usage: $0 {start|stop|restart|status|enable|disable|logs}"
            echo ""
            echo "  start    - Start all custom domain SSL services"
            echo "  stop     - Stop all custom domain SSL services"
            echo "  restart  - Restart all custom domain SSL services"
            echo "  status   - Check service status and domain information"
            echo "  enable   - Enable auto-start on boot"
            echo "  disable  - Disable auto-start on boot"
            echo "  logs     - Follow service logs"
            exit 1
            ;;
    esac
}

# Run main function
main "$@"