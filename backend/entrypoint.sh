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

should_run_entrypoint_tasks() {
    case "${SMSLY_RUN_ENTRYPOINT_TASKS:-true}" in
        0|false|False|FALSE|no|No|NO)
            return 1
            ;;
        *)
            return 0
            ;;
    esac
}

select_management_database() {
    if [ -n "${DIRECT_DATABASE_URL:-}" ]; then
        echo "[INFO] DIRECT_DATABASE_URL is set. Using direct database alias for management tasks."
        printf '%s\n' "direct"
        return 0
    fi

    if python manage.py shell -c "from django.conf import settings; exit(0 if 'session' in settings.DATABASES else 1)" 2>/dev/null; then
        echo "Detected 'session' pool. Using it for management tasks..." >&2
        printf '%s\n' "session"
        return 0
    fi

    printf '%s\n' "default"
}

run_migrations_with_retry() {
    migrate_db="${1:-default}"
    echo "Running migrations on database: $migrate_db..."

    max_retries="${MIGRATE_MAX_RETRIES:-5}"
    retry=0
    while [ "$retry" -lt "$max_retries" ]; do
        if python manage.py migrate --database="$migrate_db" --noinput 2>&1; then
            echo "Migrations complete (on database: $migrate_db)."
            return 0
        fi
        retry=$((retry + 1))
        if [ "$retry" -ge "$max_retries" ]; then
            break
        fi
        wait_secs=$((retry * 5))
        echo "Migration attempt $retry/$max_retries failed. Retrying in ${wait_secs}s..."
        sleep "$wait_secs"
    done

    echo "ERROR: migrations failed after $max_retries attempts."
    echo ""
    echo "If the error mentions 'No pool configured for database',"
    echo "PgCat on the Master node needs to be reloaded to pick up this node agent."
    echo "  On Master: docker restart smsly-hosting-pgcat-1"
    return 1
}

create_admin_if_configured() {
    migrate_db="${1:-default}"
    admin_user="${DJANGO_SUPERUSER_USERNAME:-admin}"
    admin_email="${DJANGO_SUPERUSER_EMAIL:-admin@localhost}"
    admin_pass="${DJANGO_SUPERUSER_PASSWORD:-}"

    echo "Checking for existing superuser..."
    has_superuser="$(python manage.py shell -c "from django.contrib.auth import get_user_model; User=get_user_model(); print('yes' if User.objects.using('${migrate_db}').filter(is_superuser=True).exists() else 'no')" 2>/dev/null | tail -n 1 || true)"

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
        --database="$migrate_db" \
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
    migrate_db="${1:-default}"
    echo "Configuring OAuth social apps..."
    python manage.py setup_social_apps --database="$migrate_db" >/dev/null 2>&1 || \
        echo "WARNING: setup_social_apps failed (non-fatal)"
}

    migrate_db="$(select_management_database)"

    if is_web_container "$@" && should_run_entrypoint_tasks; then
        run_migrations_with_retry "$migrate_db"
        setup_social_apps_nonfatal "$migrate_db"
        create_admin_if_configured "$migrate_db"
        collect_static_nonfatal
    elif is_web_container "$@"; then
        echo "SMSLY_RUN_ENTRYPOINT_TASKS=false; skipping entrypoint migrations/static/admin bootstrap."
    fi

    # Self-healing: ensure node agent DB permissions are always correct.
    # This fixes permissions for tables created by recent migrations that
    # the node agent user may not have access to.
    fix_node_db_permissions() {
        echo "Checking node agent database permissions..."
        python manage.py fix_node_db_permissions 2>&1 || \
            echo "WARNING: fix_node_db_permissions failed (non-fatal)"
    }
    fix_node_db_permissions

ensure_caddy_config_writable() {
    if [ -d /caddy-config ]; then
        chown -R smsly:smsly /caddy-config 2>/dev/null || true
        chmod -R u+rwX,g+rwX,o+rX /caddy-config 2>/dev/null || true
        find /caddy-config -type d -exec chmod 2775 {} + 2>/dev/null || true
    fi
}
ensure_caddy_config_writable

echo "Starting: $*"
exec "$@"
