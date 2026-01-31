#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# SMSLY Hosting - GitHub Codespaces Setup Script
# ═══════════════════════════════════════════════════════════════════════════════
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "🚀 Setting up SMSLY Hosting development environment..."
echo "   Root directory: $ROOT_DIR"

# ═══════════════════════════════════════════════════════════════════════════════
# Create necessary directories
# ═══════════════════════════════════════════════════════════════════════════════
echo "📁 Creating directories..."
sudo mkdir -p /var/smsly/uploads
sudo mkdir -p /var/smsly/build-cache
sudo chown -R $USER:$USER /var/smsly || true

# ═══════════════════════════════════════════════════════════════════════════════
# Install Nixpacks for container building
# ═══════════════════════════════════════════════════════════════════════════════
if ! command -v nixpacks &> /dev/null; then
    echo "📦 Installing Nixpacks..."
    curl -sSL https://nixpacks.com/install.sh | bash
else
    echo "✅ Nixpacks already installed"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Generate .env file with PROPER key generation
# ═══════════════════════════════════════════════════════════════════════════════
cd "$ROOT_DIR/backend"

if [ ! -f .env ]; then
    echo "📝 Generating .env file with secure keys..."
    
    # Generate keys FIRST (these are expanded before writing)
    DJANGO_SECRET=$(python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())" 2>/dev/null || openssl rand -hex 32)
    ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || openssl rand -base64 32)
    
    # Write .env with actual values (NO 'EOF' quotes = variables expanded)
    cat > .env << EOF
# ═══════════════════════════════════════════════════════════════════════════════
# SMSLY Hosting - Auto-generated Environment Configuration
# Generated: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
# ═══════════════════════════════════════════════════════════════════════════════

# Django Settings
DEBUG=True
SECRET_KEY=${DJANGO_SECRET}
ALLOWED_HOSTS=localhost,127.0.0.1,.github.dev,.codespaces.ms,.app.github.dev

# Field Encryption (Fernet key - 32 bytes base64)
FIELD_ENCRYPTION_KEY=${ENCRYPTION_KEY}

# Database Configuration
DATABASE_URL=postgresql://smsly:smsly_dev@localhost:5432/smsly_hosting
POSTGRES_DB=smsly_hosting
POSTGRES_USER=smsly
POSTGRES_PASSWORD=smsly_dev

# Redis Configuration
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0

# CORS & CSRF
CSRF_TRUSTED_ORIGINS=https://*.github.dev,https://*.app.github.dev

# Container Registry (local)
CONTAINER_REGISTRY_URL=localhost:5000
EOF
    
    echo "✅ .env file created with secure keys"
else
    echo "✅ .env file already exists"
fi

cd "$ROOT_DIR"

# ═══════════════════════════════════════════════════════════════════════════════
# Create Docker network
# ═══════════════════════════════════════════════════════════════════════════════
echo "🌐 Creating Docker network..."
docker network create smsly-net 2>/dev/null || echo "   Network already exists"

# ═══════════════════════════════════════════════════════════════════════════════
# Start infrastructure services (standalone containers for dev)
# ═══════════════════════════════════════════════════════════════════════════════
echo "🐳 Starting infrastructure services..."

# PostgreSQL
if ! docker ps | grep -q "smsly-postgres"; then
    echo "   Starting PostgreSQL..."
    docker run -d --name smsly-postgres \
        --network smsly-net \
        -p 5432:5432 \
        -e POSTGRES_USER=smsly \
        -e POSTGRES_PASSWORD=smsly_dev \
        -e POSTGRES_DB=smsly_hosting \
        -v smsly_pg_data:/var/lib/postgresql/data \
        postgres:16-alpine 2>/dev/null || docker start smsly-postgres
else
    echo "   PostgreSQL already running"
fi

# Redis
if ! docker ps | grep -q "smsly-redis"; then
    echo "   Starting Redis..."
    docker run -d --name smsly-redis \
        --network smsly-net \
        -p 6379:6379 \
        redis:alpine 2>/dev/null || docker start smsly-redis
else
    echo "   Redis already running"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Wait for PostgreSQL to be ready
# ═══════════════════════════════════════════════════════════════════════════════
echo "⏳ Waiting for PostgreSQL to be ready..."
max_attempts=30
attempt=0
until docker exec smsly-postgres pg_isready -U smsly > /dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ $attempt -ge $max_attempts ]; then
        echo "❌ PostgreSQL failed to start after ${max_attempts} attempts"
        exit 1
    fi
    echo "   Waiting for PostgreSQL... (attempt $attempt/$max_attempts)"
    sleep 2
done
echo "✅ PostgreSQL is ready"

# ═══════════════════════════════════════════════════════════════════════════════
# Install Python dependencies (from correct directory!)
# ═══════════════════════════════════════════════════════════════════════════════
echo "🐍 Installing Python dependencies..."
cd "$ROOT_DIR/backend"
pip install --quiet -r requirements.txt

# ═══════════════════════════════════════════════════════════════════════════════
# Run database migrations
# ═══════════════════════════════════════════════════════════════════════════════
echo "📊 Running database migrations..."
python manage.py migrate --no-input

# ═══════════════════════════════════════════════════════════════════════════════
# Create admin superuser
# ═══════════════════════════════════════════════════════════════════════════════
echo "👤 Creating admin user..."
python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@smsly.io', 'admin123')
    print('   Created admin user: admin / admin123')
else:
    print('   Admin user already exists')
"

# ═══════════════════════════════════════════════════════════════════════════════
# Collect static files
# ═══════════════════════════════════════════════════════════════════════════════
echo "📦 Collecting static files..."
python manage.py collectstatic --no-input --clear > /dev/null 2>&1

cd "$ROOT_DIR"

# ═══════════════════════════════════════════════════════════════════════════════
# Install frontend dependencies
# ═══════════════════════════════════════════════════════════════════════════════
if [ -d "$ROOT_DIR/frontend" ]; then
    echo "📦 Installing frontend dependencies..."
    cd "$ROOT_DIR/frontend"
    npm install --silent 2>/dev/null || npm install
    cd "$ROOT_DIR"
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Print success message
# ═══════════════════════════════════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════════════════════════════════"
echo "✅ SMSLY Hosting Setup Complete!"
echo "═══════════════════════════════════════════════════════════════════════════════"
echo ""
echo "🔗 Quick Start Commands:"
echo ""
echo "   # Start Backend (Terminal 1)"
echo "   cd backend && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4"
echo ""
echo "   # Start Frontend (Terminal 2)"
echo "   cd frontend && npm run dev"
echo ""
echo "   # Start Celery Worker (Terminal 3)"
echo "   cd backend && celery -A config worker -l INFO"
echo ""
echo "📚 Access Points:"
echo "   🌐 Frontend:  https://<codespace>-3000.app.github.dev"
echo "   🔌 API:       https://<codespace>-8000.app.github.dev/api/"
echo "   📖 Swagger:   https://<codespace>-8000.app.github.dev/api/schema/swagger/"
echo "   🔧 Admin:     https://<codespace>-8000.app.github.dev/admin/"
echo ""
echo "🔐 Admin Credentials: admin / admin123"
echo ""
echo "═══════════════════════════════════════════════════════════════════════════════"
