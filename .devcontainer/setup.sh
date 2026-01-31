#!/bin/bash
# SMSLY Hosting - GitHub Codespaces Setup Script
set -e

echo "🚀 Setting up SMSLY Hosting development environment..."

# Create necessary directories
mkdir -p /var/smsly/uploads
mkdir -p /var/smsly/build-cache

# Install Nixpacks for container building
echo "📦 Installing Nixpacks..."
curl -sSL https://nixpacks.com/install.sh | bash

# Generate .env file if not exists
if [ ! -f .env ]; then
    echo "📝 Creating .env file..."
    cat > .env << 'EOF'
# Auto-generated for Codespaces
DEBUG=True
SECRET_KEY=$(openssl rand -hex 32)
FIELD_ENCRYPTION_KEY=$(openssl rand -base64 32)

# Database (Docker internal)
DATABASE_URL=postgresql://smsly:smsly_dev@postgres:5432/smsly_hosting
POSTGRES_DB=smsly_hosting
POSTGRES_USER=smsly
POSTGRES_PASSWORD=smsly_dev

# Redis
REDIS_URL=redis://redis:6379/0

# CORS
ALLOWED_HOSTS=localhost,127.0.0.1,.github.dev
CSRF_TRUSTED_ORIGINS=https://*.github.dev

# Registry
CONTAINER_REGISTRY_URL=localhost:5000
EOF
    echo "✅ .env file created"
fi

# Create Docker network
docker network create smsly-net 2>/dev/null || true

# Start infrastructure services
echo "🐳 Starting infrastructure services..."
docker-compose -f docker-compose.prod.yml up -d postgres redis registry

# Wait for PostgreSQL
echo "⏳ Waiting for PostgreSQL..."
sleep 10

# Install Python dependencies
echo "🐍 Installing Python dependencies..."
cd backend
pip install -r requirements.txt

# Run migrations
echo "📊 Running database migrations..."
python manage.py migrate

# Create superuser
echo "👤 Creating admin user..."
python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@smsly.com', 'admin123')
    print('Created admin user: admin / admin123')
"

cd ..

echo ""
echo "✅ Setup complete!"
echo ""
echo "🔗 Quick Start:"
echo "   Backend:  cd backend && python manage.py runserver 0.0.0.0:8000"
echo "   Frontend: cd frontend && npm install && npm run dev"
echo ""
echo "📚 Access Points (after starting services):"
echo "   API:      https://<codespace-url>-8000.app.github.dev"
echo "   Frontend: https://<codespace-url>-3000.app.github.dev"
echo "   Admin:    https://<codespace-url>-8000.app.github.dev/admin/"
echo ""
echo "🔐 Admin Credentials: admin / admin123"
