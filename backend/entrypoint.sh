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

# Collect static files (non-critical — don't block startup)
echo "Collecting static files..."
python manage.py collectstatic --noinput 2>&1 || echo "⚠ Static files collection failed (non-critical)"

# Start Server (WSGI with gunicorn)
echo "Starting server on port ${PORT:-8000}..."
exec gunicorn --bind 0.0.0.0:${PORT:-8000} config.wsgi:application --workers ${WORKERS:-4} --timeout 120
