#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════════
# SMSLY Hosting - Quick Start Script (ONE COMMAND)
# Run: bash quick-start.sh
# ═══════════════════════════════════════════════════════════════════════════════
set -e

cd "$(dirname "$0")"

echo "🚀 SMSLY Hosting Quick Start"
echo ""

# Start Redis and Postgres
echo "🐳 Starting containers..."
docker run -d --name postgres -p 5432:5432 \
    -e POSTGRES_USER=smsly -e POSTGRES_PASSWORD=smsly_dev -e POSTGRES_DB=smsly_hosting \
    postgres:16-alpine 2>/dev/null || docker start postgres

docker run -d --name redis -p 6379:6379 redis:alpine 2>/dev/null || docker start redis

# Wait for Postgres
echo "⏳ Waiting for PostgreSQL..."
sleep 5
until docker exec postgres pg_isready -U smsly > /dev/null 2>&1; do sleep 1; done

# Setup backend
cd backend

# Generate .env if missing
if [ ! -f .env ]; then
    ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    DJANGO_SECRET=$(python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())")
    
    cat > .env << EOF
DEBUG=True
SECRET_KEY=${DJANGO_SECRET}
FIELD_ENCRYPTION_KEY=${ENCRYPTION_KEY}
DATABASE_URL=postgresql://smsly:smsly_dev@localhost:5432/smsly_hosting
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
ALLOWED_HOSTS=localhost,127.0.0.1,.github.dev,.app.github.dev
EOF
fi

# Install and migrate
pip install -q -r requirements.txt
python manage.py migrate --no-input
python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
if not User.objects.filter(username='admin').exists():
    User.objects.create_superuser('admin', 'admin@smsly.io', 'admin123')
" 2>/dev/null

echo ""
echo "✅ Ready! Run these commands:"
echo ""
echo "   # Terminal 1 (Backend)"
echo "   cd backend && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 4"
echo ""
echo "   # Terminal 2 (Frontend)"
echo "   cd frontend && npm install && npm run dev"
echo ""
echo "🔐 Admin: admin / admin123"
