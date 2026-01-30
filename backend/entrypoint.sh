#!/bin/sh
set -e

# Kill any stale processes on port 8000
echo "Cleaning up port ${PORT:-8000}..."
fuser -k ${PORT:-8000}/tcp 2>/dev/null || true
sleep 1

# Run migrations
echo "Running migrations..."
python manage.py migrate --noinput

# Collect static files
echo "Collecting static files..."
python manage.py collectstatic --noinput

# Start Server
echo "Starting server on port ${PORT:-8000}..."
exec gunicorn --bind 0.0.0.0:${PORT:-8000} config.asgi:application -k uvicorn.workers.UvicornWorker
