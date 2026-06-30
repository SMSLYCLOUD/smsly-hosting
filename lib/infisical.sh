#!/bin/bash
# ============================================================
# SMSLY Hosting — Infisical Bootstrap
# Generates encryption keys and secrets, initializes the
# Infisical PostgreSQL database, and starts the container.
#
# Usage: source lib/infisical.sh && infisical_bootstrap
# ============================================================
set -euo pipefail

infisical_generate_keys() {
    local env_file="$1"
    shift

    local keys=(
        "INFISICAL_ENCRYPTION_KEY"
        "INFISICAL_JWT_SIGNUP_SECRET"
        "INFISICAL_JWT_REFRESH_SECRET"
        "INFISICAL_JWT_AUTH_SECRET"
        "INFISICAL_JWT_SERVICE_SECRET"
        "INFISICAL_JWT_MFA_SECRET"
        "INFISICAL_PROVIDER_AUTH_SECRET"
    )

    local generated=0
    for key in "${keys[@]}"; do
        if ! grep -q "^${key}=" "$env_file" 2>/dev/null; then
            local value
            if [ "$key" = "INFISICAL_ENCRYPTION_KEY" ]; then
                value="$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || openssl rand -hex 32)"
            else
                value="$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || openssl rand -hex 32)"
            fi
            echo "${key}=${value}" >> "$env_file"
            generated=$((generated + 1))
        fi
    done

    if [ "$generated" -gt 0 ]; then
        echo -e "\033[0;32m  ✓ Generated ${generated} Infisical key(s)\033[0m"
    fi
}

infisical_create_database() {
    local db_container="${1:-smsly-hosting-db-1}"
    local db_name="${2:-infisical}"

    echo -e "\033[0;34m  → Ensuring Infisical database exists...\033[0m"
    docker exec "$db_container" psql -U postgres -tc \
        "SELECT 1 FROM pg_database WHERE datname='$db_name'" 2>/dev/null | grep -q 1 || \
        docker exec "$db_container" psql -U postgres -c \
        "CREATE DATABASE $db_name OWNER ${POSTGRES_USER:-smsly_admin};" 2>/dev/null || true
    echo -e "\033[0;32m  ✓ Infisical database ready\033[0m"
}

infisical_bootstrap() {
    local env_file="${INSTALL_DIR:-/opt/smsly-hosting}/.env"
    local compose_file="$INSTALL_DIR/infrastructure/docker/docker-compose.infisical.yml"

    if [ ! -f "$env_file" ]; then
        echo -e "\033[0;31m  ✗ .env file not found at $env_file\033[0m"
        return 1
    fi

    echo -e "\033[0;34m=== Infisical Bootstrap ===\033[0m"

    # 1. Generate keys if missing
    infisical_generate_keys "$env_file"

    # 2. Create database
    infisical_create_database

    # 3. Source updated env
    set -a
    source "$env_file" 2>/dev/null || true
    set +a

    # 4. Pull and start Infisical
    if [ -f "$compose_file" ]; then
        echo -e "\033[0;34m  → Starting Infisical...\033[0m"
        docker compose --env-file "$env_file" -f "$compose_file" pull infisical 2>/dev/null || true
        docker compose --env-file "$env_file" -f "$compose_file" up -d infisical
        echo -e "\033[0;32m  ✓ Infisical started\033[0m"
    else
        echo -e "\033[0;33m  ⚠ Infisical compose file not found: $compose_file\033[0m"
        return 1
    fi

    echo -e "\033[0;32m=== Infisical bootstrap complete ===\033[0m"
}
