#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# SMSLY Hosting - Production Install Script
# ═══════════════════════════════════════════════════════════════════════════════
# Usage: curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install-v2.sh | sudo bash
# Or:    git clone ... && cd smsly-hosting && sudo ./install-v2.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[SMSLY]${NC} $1"; }
success() { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# ═══════════════════════════════════════════════════════════════════════════════
# Check requirements
# ═══════════════════════════════════════════════════════════════════════════════
log "Checking system requirements..."

if ! command -v docker &> /dev/null; then
    log "Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
fi

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    log "Installing Docker Compose..."
    apt-get update && apt-get install -y docker-compose-plugin
fi

success "Docker ready"

# ═══════════════════════════════════════════════════════════════════════════════
# Clone or update repository
# ═══════════════════════════════════════════════════════════════════════════════
INSTALL_DIR="${SMSLY_INSTALL_DIR:-/opt/smsly-hosting}"

if [ -d "$INSTALL_DIR/.git" ]; then
    log "Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull origin main
else
    log "Cloning SMSLY Hosting..."
    git clone https://github.com/SMSLYCLOUD/smsly-hosting.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

success "Repository ready: $INSTALL_DIR"

# ═══════════════════════════════════════════════════════════════════════════════
# Generate secure .env file
# ═══════════════════════════════════════════════════════════════════════════════
if [ ! -f .env ]; then
    log "Generating secure .env file..."
    
    # Generate secure keys
    SECRET_KEY=$(openssl rand -hex 32)
    FERNET_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || openssl rand -base64 32)
    DB_PASS=$(openssl rand -hex 16)
    ADMIN_PASS=$(openssl rand -base64 12 | tr -dc 'a-zA-Z0-9' | head -c 12)
    
    cat > .env << EOF
# ═══════════════════════════════════════════════════════════════════════════════
# SMSLY Hosting Production Environment
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# ═══════════════════════════════════════════════════════════════════════════════

# Django
DEBUG=False
SECRET_KEY=${SECRET_KEY}
ALLOWED_HOSTS=localhost,127.0.0.1,${SMSLY_DOMAIN:-hosting.example.com}
CSRF_TRUSTED_ORIGINS=https://${SMSLY_DOMAIN:-hosting.example.com}

# Encryption
FIELD_ENCRYPTION_KEY=${FERNET_KEY}

# Database
DATABASE_URL=postgresql://smsly_admin:${DB_PASS}@db:5432/smsly_hosting
POSTGRES_DB=smsly_hosting
POSTGRES_USER=smsly_admin
POSTGRES_PASSWORD=${DB_PASS}

# Redis
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0

# API URL (internal Docker)
NEXT_PUBLIC_API_URL=http://backend:8000/api/v1

# Registry
CONTAINER_REGISTRY_URL=registry:5000

# Auto-created admin credentials
ADMIN_EMAIL=admin@smsly.io
ADMIN_PASSWORD=${ADMIN_PASS}
EOF
    
    success ".env file created"
    echo ""
    echo -e "${YELLOW}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  ADMIN CREDENTIALS (save these!)                             ║${NC}"
    echo -e "${YELLOW}╠══════════════════════════════════════════════════════════════╣${NC}"
    echo -e "${YELLOW}║  Username: admin                                             ║${NC}"
    echo -e "${YELLOW}║  Password: ${ADMIN_PASS}                                       ║${NC}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
else
    warn ".env already exists, skipping generation"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Build and start services
# ═══════════════════════════════════════════════════════════════════════════════
log "Building Docker images (this may take 5-10 minutes)..."
docker compose -f docker-compose.prod.yml build --no-cache

log "Starting services..."
docker compose -f docker-compose.prod.yml up -d

# ═══════════════════════════════════════════════════════════════════════════════
# Wait for database
# ═══════════════════════════════════════════════════════════════════════════════
log "Waiting for database to be ready..."
max_attempts=30
attempt=0
until docker compose -f docker-compose.prod.yml exec -T db pg_isready -U smsly_admin > /dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ $attempt -ge $max_attempts ]; then
        error "Database failed to start after ${max_attempts} attempts"
    fi
    sleep 2
done
success "Database is ready"

# ═══════════════════════════════════════════════════════════════════════════════
# Run migrations and create admin
# ═══════════════════════════════════════════════════════════════════════════════
log "Running database migrations..."
docker compose -f docker-compose.prod.yml exec -T backend python manage.py migrate --no-input
success "Migrations complete"

log "Collecting static files..."
docker compose -f docker-compose.prod.yml exec -T backend python manage.py collectstatic --no-input --clear > /dev/null
success "Static files collected"

log "Creating admin user..."
# Read password from .env
ADMIN_PASS=$(grep ADMIN_PASSWORD .env | cut -d'=' -f2)
docker compose -f docker-compose.prod.yml exec -T backend python manage.py shell << EOF
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@smsly.io', '${ADMIN_PASS}')
    print('Admin user created')
else:
    print('Admin user already exists')
EOF
success "Admin user ready"

# ═══════════════════════════════════════════════════════════════════════════════
# Health check
# ═══════════════════════════════════════════════════════════════════════════════
log "Running health checks..."
sleep 5

if curl -sf http://localhost/api/health/ > /dev/null 2>&1 || curl -sf http://localhost:8000/api/health/ > /dev/null 2>&1; then
    success "Backend is healthy"
else
    warn "Backend health check failed (may still be starting)"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Print success
# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ SMSLY Hosting Installation Complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BLUE}📍 Access Points:${NC}"
echo -e "     Dashboard:  http://localhost/ or https://${SMSLY_DOMAIN:-hosting.example.com}/"
echo -e "     API:        http://localhost/api/"
echo -e "     Admin:      http://localhost/admin/"
echo ""
echo -e "  ${BLUE}🔐 Admin Login:${NC}"
echo -e "     Username:   admin"
echo -e "     Password:   ${ADMIN_PASS}"
echo ""
echo -e "  ${BLUE}📋 Useful Commands:${NC}"
echo -e "     Logs:       docker compose -f docker-compose.prod.yml logs -f"
echo -e "     Stop:       docker compose -f docker-compose.prod.yml down"
echo -e "     Restart:    docker compose -f docker-compose.prod.yml restart"
echo -e "     Status:     docker compose -f docker-compose.prod.yml ps"
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════════════════════${NC}"
