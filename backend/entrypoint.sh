#!/bin/sh
set -e

# Run migrations
echo "Running migrations..."
python manage.py migrate --noinput

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Start Server (WSGI with gunicorn — prod compose may override this command)
echo "Starting server on port ${PORT:-8000}..."
exec gunicorn --bind 0.0.0.0:${PORT:-8000} config.wsgi:application --workers ${WORKERS:-4} --timeout 120
