#!/bin/sh

# Entrypoint shared by backend/celery/celery-beat images.
#
# Production safety goals:
# - Only the web container (gunicorn) runs migrations/static/admin bootstrap.
# - Never ship a hardcoded default admin password.
#   Admin creation only runs when DJANGO_SUPERUSER_PASSWORD is explicitly set.

set -e

is_web_container() {
    [ "${1:-}" = "gunicorn" ]
}

run_migrations_with_retry() {
    echo "Running makemigrations..."
    python manage.py makemigrations --noinput 2>&1 || \
        echo "WARNING: makemigrations had issues (non-fatal)"

    echo "Running migrations..."
    max_retries="${MIGRATE_MAX_RETRIES:-5}"
    retry=0
    while [ "$retry" -lt "$max_retries" ]; do
        if python manage.py migrate --noinput; then
            echo "Migrations complete."
            return 0
        fi
        retry=$((retry + 1))
        wait_secs=$((retry * 5))
        echo "Migration attempt $retry/$max_retries failed. Retrying in ${wait_secs}s..."
        sleep "$wait_secs"
    done

    echo "ERROR: migrations failed after $max_retries attempts."
    return 1
}

create_admin_if_configured() {
    admin_user="${DJANGO_SUPERUSER_USERNAME:-admin}"
    admin_email="${DJANGO_SUPERUSER_EMAIL:-admin@localhost}"
    admin_pass="${DJANGO_SUPERUSER_PASSWORD:-}"

    echo "Checking for existing superuser..."
    has_superuser="$(python manage.py shell -c "from django.contrib.auth import get_user_model; User=get_user_model(); print('yes' if User.objects.filter(is_superuser=True).exists() else 'no')" 2>/dev/null | tail -n 1 || true)"

    if [ "$has_superuser" != "no" ]; then
        echo "Superuser already exists (or check failed). Skipping admin creation."
        return 0
    fi

    if [ -z "$admin_pass" ]; then
        echo "No superuser found. DJANGO_SUPERUSER_PASSWORD is not set; skipping admin creation."
        return 0
    fi

    echo "No superuser found. Creating admin account..."
    DJANGO_SUPERUSER_PASSWORD="$admin_pass" python manage.py createsuperuser \
        --noinput \
        --username "$admin_user" \
        --email "$admin_email" >/dev/null 2>&1 || \
        echo "WARNING: failed to create admin (may already exist)"
}

collect_static_nonfatal() {
    echo "Collecting static files..."
    python manage.py collectstatic --noinput >/dev/null 2>&1 || \
        echo "WARNING: collectstatic failed (non-fatal)"
}

setup_social_apps_nonfatal() {
    echo "Configuring OAuth social apps..."
    python manage.py setup_social_apps >/dev/null 2>&1 || \
        echo "WARNING: setup_social_apps failed (non-fatal)"
}

if is_web_container "$@"; then
    run_migrations_with_retry
    setup_social_apps_nonfatal
    create_admin_if_configured
    collect_static_nonfatal
fi

echo "Starting: $*"
exec "$@"
