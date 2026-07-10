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
        # Try chown first (works if container runs with --privileged or as root).
        chown -R 1000:1000 /caddy-config 2>/dev/null || true
        # Try chmod (fast path, works if uid 1000 already owns it).
        chmod -R u+rwX,g+rwX /caddy-config 2>/dev/null || true
        find /caddy-config -type d -exec chmod 2775 {} + 2>/dev/null || true
        # Verify write access with a probe file. If it fails, the host
        # directory is owned by root and needs a one-time chown from the host.
        if ! touch /caddy-config/.perm_probe 2>/dev/null; then
            echo "[entrypoint] WARNING: /caddy-config is not writable by uid $(id -u)." >&2
            echo "[entrypoint] Run on host: sudo chown -R 1000:1000 /opt/smsly-hosting/caddy-config" >&2
        else
            rm -f /caddy-config/.perm_probe
        fi
    fi
}
ensure_caddy_config_writable

ensure_prometheus_targets_writable() {
    mkdir -p /opt/smsly-hosting/prometheus-targets 2>/dev/null || true
    chmod 2777 /opt/smsly-hosting/prometheus-targets 2>/dev/null || true
}
ensure_prometheus_targets_writable

# ── Auto-setup registry htpasswd if missing ───────────────────────
# TLS certificates for the registry are managed by Traefik — do NOT
# regenerate certs here (see .commandcode/taste/taste.md: infrastructure).
ensure_registry_bootstrap() {
    # Only run if we're root and the registry volumes are mounted
    if [ "$(id -u)" != "0" ]; then
        return 0
    fi
    if [ ! -d /certs ] || [ ! -d /auth ]; then
        return 0
    fi

    # TLS certs are Traefik-managed — warn if missing but don't generate
    if [ ! -f /certs/registry.crt ] || [ ! -f /certs/registry.key ]; then
        echo "[entrypoint] WARNING: Registry TLS certs missing from /certs/." >&2
        echo "[entrypoint] Certs are managed by Traefik — run install.sh or check Traefik cert generation." >&2
    fi

    if [ ! -f /auth/htpasswd ]; then
        echo "[entrypoint] Generating registry htpasswd auth file..."
        _reg_user="${REGISTRY_USER:-smsly-registry}"
        _reg_pass="${REGISTRY_PASSWORD:-}"
        if [ -z "$_reg_pass" ]; then
            _reg_pass=$(python3 -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null \
                || openssl rand -hex 12 2>/dev/null \
                || echo "auto-generated-change-me")
            echo "[entrypoint] Generated random REGISTRY_PASSWORD"
        fi
        # Try htpasswd first, fall back to Python bcrypt
        if command -v htpasswd >/dev/null 2>&1; then
            htpasswd -Bbn "$_reg_user" "$_reg_pass" > /auth/htpasswd 2>/dev/null || true
        else
            # Use env vars to avoid shell injection via password interpolation
            REGISTRY_USER="$_reg_user" REGISTRY_PASSWORD="$_reg_pass" python3 -c '
import os, bcrypt
user = os.environ["REGISTRY_USER"]
pw = os.environ["REGISTRY_PASSWORD"].encode()
hashed = bcrypt.hashpw(pw, bcrypt.gensalt(10))
print(user + ":" + hashed.decode())
' > /auth/htpasswd 2>/dev/null || echo "[entrypoint] WARNING: htpasswd generation failed" >&2
        fi
        chmod 644 /auth/htpasswd 2>/dev/null || true
    fi
}
ensure_registry_bootstrap

# Ensure /auth directory is writable by the smsly user (UID 1000) so that
# the Django signal can sync htpasswd when registry credentials change.
# This MUST run AFTER ensure_registry_bootstrap so the htpasswd file
# (created as root) is chowned to UID 1000 before dropping privileges.
ensure_auth_writable() {
    if [ -d /auth ]; then
        chown -R 1000:1000 /auth 2>/dev/null || true
        chmod -R u+rwX,g+rwX /auth 2>/dev/null || true
    fi
}
ensure_auth_writable

# Write local docker-labels target files on every web container start
if is_web_container "$@"; then
    python manage.py deploy_docker_labels_exporters --targets-only 2>&1 | \
        awk '{print "[deploy-docker-labels]", $0}' || \
        echo "[deploy-docker-labels] WARNING: failed to write target files" >&2
fi

# Clean up stale celery beat pidfile if we're starting beat
if [ "${1:-}" = "celery" ] && [ "${4:-}" = "beat" ] || echo "$*" | grep -q "celery.*beat"; then
    rm -f /app/celerybeat.pid
fi

# Drop from root to the smsly (UID 1000) non-privileged user.
# This allows the setup above (chown/chmod on bind-mounted volumes,
# migrations, static collect) to run as root while the application
# itself runs unprivileged.
if [ "$(id -u)" = "0" ]; then
    echo "Starting: $* (as smsly)"
    exec su -s /bin/sh -c 'exec "$0" "$@"' smsly -- "$@"
fi

echo "Starting: $*"
exec "$@"
