#!/bin/sh

# ─── Wait for database to be ready before running migrations ────────────────
# The database may still be initializing. Retry with backoff.
echo "Waiting for database..."
MAX_RETRIES=5
RETRY=0
while [ $RETRY -lt $MAX_RETRIES ]; do
    if python manage.py migrate --noinput 2>&1; then
        echo "✓ Migrations complete."
        break
    fi
    RETRY=$((RETRY + 1))
    if [ $RETRY -eq $MAX_RETRIES ]; then
        echo "✗ Migrations failed after $MAX_RETRIES attempts."
        echo "  Check DATABASE_URL and database connectivity."
        echo "  Starting server anyway (may have limited functionality)..."
        break
    fi
    WAIT=$((RETRY * 5))
    echo "⚠ Migration attempt $RETRY/$MAX_RETRIES failed. Retrying in ${WAIT}s..."
    sleep $WAIT
done

# ─── Create default admin account if no superuser exists ────────────────────
# Uses env vars with sensible defaults for first-time setup.
# IMPORTANT: Change the default password immediately after first login!
ADMIN_USER="${DJANGO_SUPERUSER_USERNAME:-admin}"
ADMIN_EMAIL="${DJANGO_SUPERUSER_EMAIL:-admin@localhost}"
ADMIN_PASS="${DJANGO_SUPERUSER_PASSWORD:-admin}"

echo "Checking for existing superuser..."
HAS_SUPERUSER=$(python manage.py shell -c "
from django.contrib.auth import get_user_model
User = get_user_model()
print('yes' if User.objects.filter(is_superuser=True).exists() else 'no')
" 2>/dev/null)

if [ "$HAS_SUPERUSER" = "no" ]; then
    echo "⚡ No superuser found. Creating default admin account..."
    DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASS" python manage.py createsuperuser \
        --noinput \
        --username "$ADMIN_USER" \
        --email "$ADMIN_EMAIL" 2>&1 && \
    echo "╔══════════════════════════════════════════════════════════╗" && \
    echo "║  ⚠  DEFAULT ADMIN CREATED                              ║" && \
    echo "║  Username: $ADMIN_USER                                  ║" && \
    echo "║  Password: $ADMIN_PASS                                  ║" && \
    echo "║                                                         ║" && \
    echo "║  ⚠  CHANGE THIS PASSWORD IMMEDIATELY AFTER LOGIN!      ║" && \
    echo "╚══════════════════════════════════════════════════════════╝" || \
    echo "⚠ Failed to create default admin (may already exist)"
else
    echo "✓ Superuser already exists. Skipping creation."
fi

# Collect static files (non-critical — don't block startup)
echo "Collecting static files..."
python manage.py collectstatic --noinput 2>&1 || echo "⚠ Static files collection failed (non-critical)"

# Execute the CMD passed from Dockerfile or docker-compose command:
# - backend: gunicorn (default CMD in Dockerfile)
# - celery: celery worker (from docker-compose command:)
# - celery-beat: celery beat (from docker-compose command:)
echo "Starting: $@"
exec "$@"
