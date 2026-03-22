#!/bin/bash

# =============================================================================
# CloudNeuron by SMSLY - Universal Installer v3.1 (Production Hardened)
# =============================================================================
# Supports: Ubuntu 20.04/22.04/24.04 LTS
# Modes:
#   1. IP Mode (HTTP :8090) - Quick start, no domain needed.
#   2. SSL Mode (HTTPS)     - Production ready, requires domain + DNS.
#
# Usage:
#   Fresh install:    sudo bash install.sh
#   Full update:      sudo bash install.sh --update
#   Frontend only:    sudo bash install.sh --update-frontend
#   Backend only:     sudo bash install.sh --update-backend
#   Wipe install:     sudo bash install.sh --wipe
#
# Features:
#   - Idempotent: safe to re-run without data loss
#   - Full installation logging to /var/log/smsly-install.log
#   - Rollback on failure via trap handler
#   - Secure credential storage (no plaintext to terminal)
#   - Update mode: git stash -> pull -> rebuild -> restart
#   - Disk space pre-check (prevents mid-build failures)
#   - Nginx config verification (prevents 502 from default config)
#   - Caddyfile IP catch-all (prevents unreachable dashboard)
# =============================================================================

set -euo pipefail

# --- Resolve script path BEFORE any cd (screen guard needs absolute path) ----
SCRIPT_PATH="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

# --- Screen Session Guard (survives SSH disconnects) -------------------------
# Collect ALL interactive input FIRST (before screen), then re-launch inside
# a screen session with the collected values as env vars.
# To reattach after disconnect: screen -r cloudneuron-install
if [ -z "${STY:-}" ] && [ -z "${SKIP_SCREEN:-}" ] && [[ "${1:-}" != "--verify" ]] && [[ "${1:-}" != "--verify-fix" ]] && [[ "${1:-}" != "--verify-autofix" ]] && [[ "${1:-}" != "--debug" ]]; then
    # Install screen if missing
    if ! command -v screen &> /dev/null; then
        apt-get update -qq && apt-get install -y screen > /dev/null 2>&1
    fi

    # -- Pre-collect interactive input (only for fresh installs) ----------
    # Skip collection if values are already pre-seeded via env vars, or if
    # this is an --update / --wipe run (those don't need interactive input).
    _ARG1="${1:-}"
    if [[ "$_ARG1" != "--update"* ]] && [[ "$_ARG1" != "--wipe" ]] && [[ "$_ARG1" != "--recover" ]] && [[ "$_ARG1" != "--debug" ]] && [[ "$_ARG1" != "--verify" ]] && [[ "$_ARG1" != "--verify-fix" ]] && [[ "$_ARG1" != "--verify-autofix" ]] && [ -z "${USE_SSL:-}" ]; then
        # Detect public IP for the mode selection prompt
        _detect_ip() {
            local c="" ep=""
            for ep in "https://api.ipify.org" "https://ifconfig.me/ip" "https://ipv4.icanhazip.com"; do
                c="$(curl -4 -fsS -m 5 "$ep" 2>/dev/null | tr -d '\r\n' || true)"
                if [[ "$c" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then echo "$c"; return 0; fi
            done
            c="$(hostname -I 2>/dev/null | awk '{print $1}' | tr -d '\r\n' || true)"
            if [[ "$c" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then echo "$c"; return 0; fi
            echo "127.0.0.1"
        }
        _PUB_IP="$(_detect_ip)"

        echo ""
        echo -e "\033[0;34mSelect Deployment Mode:\033[0m"
        echo -e "  1) \033[0;32mIP Mode\033[0m (Easy) - http://$_PUB_IP:8090"
        echo -e "  2) \033[0;32mSSL Mode\033[0m (Prod) - https://your-domain.com (Requires DNS A Record pointing to $_PUB_IP)"

        if [ -e /dev/tty ]; then
            read -p "Enter choice [1]: " _MODE_CHOICE < /dev/tty
            echo ""
            _MODE_CHOICE=${_MODE_CHOICE:-1}
        else
            _MODE_CHOICE=1
        fi

        if [ "$_MODE_CHOICE" -eq "2" ] 2>/dev/null; then
            export USE_SSL="true"
            _DOMAIN=""
            while [ -z "$_DOMAIN" ]; do
                read -p "  Enter your Domain (e.g., app.example.com): " _DOMAIN < /dev/tty
            done
            echo ""
            export DOMAIN="$_DOMAIN"

            _ACME_EMAIL=""
            while [ -z "$_ACME_EMAIL" ]; do
                read -p "  Enter Email for SSL (e.g., admin@example.com): " _ACME_EMAIL < /dev/tty
            done
            echo ""
            export ACME_EMAIL="$_ACME_EMAIL"

            echo ""
            echo -e "\033[0;34m  Wildcard subdomains allow deployed services to get automatic SSL.\033[0m"
            echo -e "  e.g., myapp-abc123.${_DOMAIN} will automatically have HTTPS."
            echo -e "  This requires a Cloudflare API Token with DNS:Edit permission."
            echo ""

            read -p "  Enable wildcard subdomains? (y/n) [n]: " _WC_CHOICE < /dev/tty
            echo ""
            _WC_CHOICE=${_WC_CHOICE:-n}
            if [[ $_WC_CHOICE =~ ^[Yy]$ ]]; then
                export WILDCARD_SUBDOMAINS="true"
                _CF_TOKEN=""
                while [ -z "$_CF_TOKEN" ]; do
                    read -sp "  Enter Cloudflare API Token (DNS:Edit): " _CF_TOKEN < /dev/tty
                    echo ""
                done
                export CLOUDFLARE_API_TOKEN="$_CF_TOKEN"
            else
                export WILDCARD_SUBDOMAINS="false"
            fi
        else
            export USE_SSL="false"
            export DOMAIN="$_PUB_IP"
        fi
    fi

    echo -e "\033[1;33m"
    echo "═══════════════════════════════════════════════════════════"
    echo "  Running inside a screen session for safety."
    echo "  If SSH disconnects, reconnect and run:"
    echo "    screen -r cloudneuron-install"
    echo "═══════════════════════════════════════════════════════════"
    echo -e "\033[0m"

    # Build env string to pass collected values into screen (printf %q escapes special chars)
    _ENV_PASS="SKIP_SCREEN=1"
    [ -n "${USE_SSL:-}" ]              && _ENV_PASS="$_ENV_PASS USE_SSL=$(printf '%q' "$USE_SSL")"
    [ -n "${DOMAIN:-}" ]               && _ENV_PASS="$_ENV_PASS DOMAIN=$(printf '%q' "$DOMAIN")"
    [ -n "${ACME_EMAIL:-}" ]           && _ENV_PASS="$_ENV_PASS ACME_EMAIL=$(printf '%q' "$ACME_EMAIL")"
    [ -n "${WILDCARD_SUBDOMAINS:-}" ]  && _ENV_PASS="$_ENV_PASS WILDCARD_SUBDOMAINS=$(printf '%q' "$WILDCARD_SUBDOMAINS")"
    [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && _ENV_PASS="$_ENV_PASS CLOUDFLARE_API_TOKEN=$(printf '%q' "$CLOUDFLARE_API_TOKEN")"

    # Stay ATTACHED (no -dm), use absolute path, set correct working directory
    exec screen -S cloudneuron-install bash -c "cd $(printf '%q' "$SCRIPT_DIR"); $_ENV_PASS bash $(printf '%q' "$SCRIPT_PATH") $*; echo ''; echo 'Installation complete. Press Enter to exit.'; read"
fi

# Ensure we start in a valid directory.
# Provisioning can pass SMSLY_INSTALL_WORKDIR to use a prepared local source tree.
if [ -n "${SMSLY_INSTALL_WORKDIR:-}" ] && [ -d "${SMSLY_INSTALL_WORKDIR}" ]; then
    cd "${SMSLY_INSTALL_WORKDIR}" 2>/dev/null || cd /root 2>/dev/null || cd /
else
    # Fallback for interactive/manual installer runs.
    cd /root 2>/dev/null || cd /
fi

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
# Validate and safely detect a usable IPv4 address for installer defaults.
is_valid_ipv4() {
    local ip="$1"
    local octet

    [[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
    IFS='.' read -r o1 o2 o3 o4 <<< "$ip"
    for octet in "$o1" "$o2" "$o3" "$o4"; do
        [[ "$octet" =~ ^[0-9]+$ ]] || return 1
        [ "$octet" -ge 0 ] && [ "$octet" -le 255 ] || return 1
    done
    return 0
}

detect_public_ip() {
    local candidate=""
    local endpoint=""
    local endpoints=(
        "https://api.ipify.org"
        "https://ifconfig.me/ip"
        "https://ipv4.icanhazip.com"
    )

    for endpoint in "${endpoints[@]}"; do
        candidate="$(curl -4 -fsS -m 5 "$endpoint" 2>/dev/null | tr -d '\r\n' || true)"
        if is_valid_ipv4 "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done

    candidate="$(hostname -I 2>/dev/null | awk '{print $1}' | tr -d '\r\n' || true)"
    if is_valid_ipv4 "$candidate"; then
        echo "$candidate"
        return 0
    fi

    echo "127.0.0.1"
    return 0
}

# --- Constants ---------------------------------------------------------------
gen_hex_secret() {
    local bytes="${1:-16}"
    python3 -c "import secrets; print(secrets.token_hex(${bytes}))" 2>/dev/null || openssl rand -hex "$bytes"
}

DEFAULT_PRODUCTION_SERVICES="ai-router-790baa29,ollama-nomic-embed-text-ef83dc98,ollama-qwen2-5-90f3cb01,ollama-phi3-a11eec59,llama-3-1-5a26740b,deepseek-r1-08265326"

get_service_allowlist() {
    local list="${1:-}"
    if [ -z "$list" ] && [ -f "$INSTALL_DIR/.env" ]; then
        list="$(env_get_value "$INSTALL_DIR/.env" "PRODUCTION_SERVICES" 2>/dev/null || true)"
    fi
    [ -z "$list" ] && list="$DEFAULT_PRODUCTION_SERVICES"
    echo "$list"
}

service_allowlist_to_python() {
    local raw="${1:-}"
    local item=""
    local items=()
    local list_repr="["
    local escaped=""

    IFS=',' read -ra items <<< "$raw"
    for item in "${items[@]}"; do
        item="$(printf '%s' "$item" | xargs)"
        if [ -z "$item" ]; then
            continue
        fi
        escaped="${item//\\/\\\\}"
        escaped="${escaped//\'/\\'}"
        list_repr="${list_repr}'${escaped}',"
    done

    if [ "$list_repr" = "[" ]; then
        echo "[]"
        return
    fi

    echo "${list_repr%,}]"
}

env_get_value() {
    local env_file="$1"
    local var_name="$2"
    grep -m1 "^${var_name}=" "$env_file" 2>/dev/null | cut -d= -f2- || true
}

env_set_value() {
    local env_file="$1"
    local var_name="$2"
    local var_value="$3"
    if grep -q "^${var_name}=" "$env_file" 2>/dev/null; then
        # Use SOH (\x01) as sed delimiter  -  safe for URLs, passwords, pipes
        sed -i "s\x01^${var_name}=.*\x01${var_name}=${var_value}\x01" "$env_file"
    else
        echo "${var_name}=${var_value}" >> "$env_file"
    fi
}

env_ensure_var() {
    local env_file="$1"
    local var_name="$2"
    local var_value="$3"
    local var_comment="${4:-}"
    if ! grep -q "^${var_name}=" "$env_file" 2>/dev/null; then
        echo -e "${BLUE}  -> Adding missing $var_name to .env${NC}"
        echo "" >> "$env_file"
        [ -n "$var_comment" ] && echo "# $var_comment" >> "$env_file"
        echo "${var_name}=${var_value}" >> "$env_file"
        echo -e "${GREEN}  OK $var_name added${NC}"
    fi
}

ensure_env_runtime_defaults() {
    local env_file="$1"
    local redis_password=""
    local postgres_password=""
    local current_redis_url=""
    local expected_redis_url=""
    local current_celery_broker_url=""
    local current_database_url=""
    local expected_database_url=""

    [ -f "$env_file" ] || return 1

    env_ensure_var "$env_file" "REDIS_PASSWORD" "$(gen_hex_secret 16)" "Redis authentication password"
    env_ensure_var "$env_file" "GATEWAY_SECRET" "$(gen_hex_secret 32)" "Inter-service HMAC authentication secret"
    env_ensure_var "$env_file" "GITHUB_WEBHOOK_SECRET" "$(gen_hex_secret 32)" "GitHub webhook signature verification"
    env_ensure_var "$env_file" "AUTOSCALER_API_TOKEN" "$(gen_hex_secret 32)" "Autoscaler API bearer token (shared between autoscaler service and Django backend)"
    env_ensure_var "$env_file" "SMSLY_DISABLE_TIER_GATES" "true" "Disable owner-tier paywall gates in this edition"

    redis_password="$(env_get_value "$env_file" "REDIS_PASSWORD")"
    postgres_password="$(env_get_value "$env_file" "POSTGRES_PASSWORD")"

    if [ -n "$redis_password" ]; then
        expected_redis_url="redis://:${redis_password}@redis:6379/0"
        current_redis_url="$(env_get_value "$env_file" "REDIS_URL")"
        current_celery_broker_url="$(env_get_value "$env_file" "CELERY_BROKER_URL")"

        if [[ "$current_redis_url" == redis://redis:* ]]; then
            echo -e "${BLUE}  -> Fixing REDIS_URL to include authentication${NC}"
            sed -i "s|^REDIS_URL=redis://redis:|REDIS_URL=redis://:${redis_password}@redis:|" "$env_file"
            current_redis_url="$(env_get_value "$env_file" "REDIS_URL")"
            echo -e "${GREEN}  OK REDIS_URL updated with auth${NC}"
        fi

        env_ensure_var "$env_file" "REDIS_URL" "$expected_redis_url" "Redis connection string"
        env_ensure_var "$env_file" "CELERY_BROKER_URL" "$expected_redis_url" "Celery broker (Redis with auth)"

        if [[ "$current_redis_url" =~ ^redis://:.*@redis:6379/0$ ]] && [ "$current_redis_url" != "$expected_redis_url" ]; then
            echo -e "${BLUE}  -> Syncing REDIS_URL with REDIS_PASSWORD${NC}"
            env_set_value "$env_file" "REDIS_URL" "$expected_redis_url"
            echo -e "${GREEN}  OK REDIS_URL synced${NC}"
        fi

        if [[ "$current_celery_broker_url" =~ ^redis://:.*@redis:6379/0$ ]] && [ "$current_celery_broker_url" != "$expected_redis_url" ]; then
            echo -e "${BLUE}  -> Syncing CELERY_BROKER_URL with REDIS_PASSWORD${NC}"
            env_set_value "$env_file" "CELERY_BROKER_URL" "$expected_redis_url"
            echo -e "${GREEN}  OK CELERY_BROKER_URL synced${NC}"
        fi
    fi

    if [ -n "$postgres_password" ]; then
        # Route through PgBouncer for connection pooling
        expected_database_url="postgresql://smsly_admin:${postgres_password}@pgbouncer:5432/smsly_hosting"
        current_database_url="$(env_get_value "$env_file" "DATABASE_URL")"

        # Migrate legacy @db:5432 URLs to @pgbouncer:5432
        if [[ "$current_database_url" =~ @db:5432 ]]; then
            echo -e "${BLUE}  -> Migrating DATABASE_URL from db to pgbouncer${NC}"
            local migrated_url="${current_database_url/@db:5432/@pgbouncer:5432}"
            env_set_value "$env_file" "DATABASE_URL" "$migrated_url"
            current_database_url="$migrated_url"
            echo -e "${GREEN}  OK DATABASE_URL migrated to pgbouncer${NC}"
        fi

        if [ -z "$current_database_url" ]; then
            env_ensure_var "$env_file" "DATABASE_URL" "$expected_database_url" "PostgreSQL connection string (via PgBouncer)"
        elif [[ "$current_database_url" =~ ^postgresql://smsly_admin:.*@pgbouncer:5432/smsly_hosting$ ]] && [ "$current_database_url" != "$expected_database_url" ]; then
            echo -e "${BLUE}  -> Fixing DATABASE_URL to match POSTGRES_PASSWORD${NC}"
            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            echo -e "${GREEN}  OK DATABASE_URL password synced${NC}"
        fi
    fi

    return 0
}

validate_env_file() {
    local env_file="$1"
    local required_vars=(
        "SECRET_KEY"
        "FIELD_ENCRYPTION_KEY"
        "POSTGRES_PASSWORD"
        "DATABASE_URL"
        "REDIS_PASSWORD"
        "REDIS_URL"
        "CELERY_BROKER_URL"
        "GATEWAY_SECRET"
        "GITHUB_WEBHOOK_SECRET"
    )
    local missing_vars=()
    local invalid_vars=()
    local var_name=""
    local var_value=""
    local secret_key=""
    local field_encryption_key=""
    local database_url=""
    local redis_url=""
    local celery_broker_url=""

    [ -f "$env_file" ] || {
        echo -e "${RED}x .env file not found: $env_file${NC}"
        return 1
    }

    for var_name in "${required_vars[@]}"; do
        var_value="$(env_get_value "$env_file" "$var_name")"
        if [ -z "$var_value" ]; then
            missing_vars+=("$var_name")
        fi
    done

    secret_key="$(env_get_value "$env_file" "SECRET_KEY")"
    if [ -n "$secret_key" ] && [ "${#secret_key}" -lt 32 ]; then
        invalid_vars+=("SECRET_KEY (too short)")
    fi

    field_encryption_key="$(env_get_value "$env_file" "FIELD_ENCRYPTION_KEY")"
    if [ -n "$field_encryption_key" ] && [[ ! "$field_encryption_key" =~ ^[A-Za-z0-9_-]{43}=$ ]]; then
        invalid_vars+=("FIELD_ENCRYPTION_KEY (invalid Fernet format)")
    fi

    database_url="$(env_get_value "$env_file" "DATABASE_URL")"
    if [ -n "$database_url" ] && [[ ! "$database_url" =~ ^postgres(ql)?:// ]]; then
        invalid_vars+=("DATABASE_URL (must start with postgres:// or postgresql://)")
    fi

    redis_url="$(env_get_value "$env_file" "REDIS_URL")"
    if [ -n "$redis_url" ] && [[ ! "$redis_url" =~ ^redis:// ]]; then
        invalid_vars+=("REDIS_URL (must start with redis://)")
    fi

    celery_broker_url="$(env_get_value "$env_file" "CELERY_BROKER_URL")"
    if [ -n "$celery_broker_url" ] && [[ ! "$celery_broker_url" =~ ^redis:// ]]; then
        invalid_vars+=("CELERY_BROKER_URL (must start with redis://)")
    fi

    if [ ${#missing_vars[@]} -gt 0 ] || [ ${#invalid_vars[@]} -gt 0 ]; then
        echo -e "${RED}x Invalid .env configuration detected.${NC}"
        if [ ${#missing_vars[@]} -gt 0 ]; then
            echo -e "${RED}  Missing/empty required variables:${NC}"
            for var_name in "${missing_vars[@]}"; do
                echo -e "${RED}    - $var_name${NC}"
            done
        fi
        if [ ${#invalid_vars[@]} -gt 0 ]; then
            echo -e "${RED}  Invalid values:${NC}"
            for var_name in "${invalid_vars[@]}"; do
                echo -e "${RED}    - $var_name${NC}"
            done
        fi
        echo -e "${YELLOW}  Fix .env and rerun install. Backup file: $INSTALL_DIR/.env.backup${NC}"
        return 1
    fi

    echo -e "${GREEN}  OK .env validation passed${NC}"
    return 0
}

LOG_FILE="/var/log/smsly-install.log"
INSTALL_DIR="/opt/smsly-hosting"
CREDENTIALS_FILE="$INSTALL_DIR/.credentials"
COMPOSE_FILE="docker-compose.prod.yml"
ROLLBACK_NEEDED=false

PRODUCTION_SERVICES_ALLOWLIST="$(get_service_allowlist "${PRODUCTION_SERVICES:-}")"
SERVICE_ALLOWLIST_PY="$(service_allowlist_to_python "$PRODUCTION_SERVICES_ALLOWLIST")"
if [ "$SERVICE_ALLOWLIST_PY" = "[]" ]; then
    SERVICE_ALLOWLIST_PY="$(service_allowlist_to_python "$DEFAULT_PRODUCTION_SERVICES")"
fi

# --- Parse Arguments ---------------------------------------------------------
UPDATE_MODE=""
WIPE_MODE="false"
RECOVER_MODE="false"
DEBUG_MODE="false"
case "${1:-}" in
    --update)          UPDATE_MODE="full" ;;
    --update-frontend) UPDATE_MODE="frontend" ;;
    --update-backend)  UPDATE_MODE="backend" ;;
    --wipe)            WIPE_MODE="true" ;;
    --recover)         RECOVER_MODE="true" ;;
    --debug)           DEBUG_MODE="true" ;;
    --verify)          VERIFY_MODE="true" ;;
    --help|-h)
        echo "Usage: sudo bash install.sh [--update|--update-frontend|--update-backend|--recover|--debug|--wipe]"
        echo ""
        echo "  (no args)          Fresh install"
        echo "  --update           Pull latest code and rebuild all services"
        echo "  --update-frontend  Pull latest code and rebuild frontend only"
        echo "  --update-backend   Pull latest code and rebuild backend only"
        echo "  --recover          Restart Docker/network/runtime stack without deleting data"
        echo "  --debug            Print deep runtime diagnostics (containers, networks, health, logs)"
        echo "  --verify           Run endpoint verification only (no changes)"
        echo "  --wipe             Delete existing install artifacts (for fresh VPS reset)"
        echo "  Optional: set PRODUCTION_SERVICES (comma-separated) to define which service slugs to include in production checks"
        exit 0
        ;;
esac

MODE_LABEL="fresh-install"
if [ -n "$UPDATE_MODE" ]; then
    MODE_LABEL="update-$UPDATE_MODE"
elif [ "$RECOVER_MODE" = "true" ]; then
    MODE_LABEL="recover"
elif [ "$DEBUG_MODE" = "true" ]; then
    MODE_LABEL="debug"
elif [ "$WIPE_MODE" = "true" ]; then
    MODE_LABEL="wipe"
fi

# Log all output to file AND terminal
exec > >(tee -a "$LOG_FILE") 2>&1
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  SMSLY Hosting Install Log  -  $(date -Iseconds)"
echo "  Mode: $MODE_LABEL"
echo "═══════════════════════════════════════════════════════════"

# --- Rollback Trap ----------------------------------------------------------
cleanup_on_failure() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo -e "\n${RED}════════════════════════════════════════════════════════════${NC}"
        echo -e "${RED}  INSTALLATION FAILED (exit code: $exit_code)${NC}"
        echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
        echo -e "${YELLOW}  -> Rolling back...${NC}"

        # Stop any containers that were started
        if [ -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
            cd "$INSTALL_DIR" 2>/dev/null || true
            docker compose -f "$COMPOSE_FILE" down 2>/dev/null || true
        fi

        # Restore backup .env if one was created
        if [ -f "$INSTALL_DIR/.env.backup" ]; then
            echo -e "${YELLOW}  -> Restoring previous .env from backup${NC}"
            mv "$INSTALL_DIR/.env.backup" "$INSTALL_DIR/.env" 2>/dev/null || true
        fi

        # Restore git stash if we stashed
        if [ -f "$INSTALL_DIR/.git-stash-marker" ]; then
            echo -e "${YELLOW}  -> Restoring git stash (rolling back code changes)${NC}"
            cd "$INSTALL_DIR" && git stash pop 2>/dev/null || true
            rm -f "$INSTALL_DIR/.git-stash-marker"
        fi

        echo -e "${YELLOW}  Full log: $LOG_FILE${NC}"
        echo -e "${RED}  Please review the log and re-run the installer.${NC}"
    fi
}
trap cleanup_on_failure EXIT

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   CloudNeuron - Production Installer v3.1${NC}"
echo -e "${BLUE}   Target: Ubuntu LTS (Fresh Install Recommended)${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}\n"

# =============================================================================
# WIPE MODE  -  Remove all install artifacts for a clean re-install
# =============================================================================
wipe_existing_install() {
    echo -e "${YELLOW}[WIPE] Removing existing SMSLY Hosting installation artifacts...${NC}"

    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --wipe)${NC}"
        exit 1
    fi

    if [ "${FORCE_WIPE:-0}" != "1" ]; then
        if [ -e /dev/tty ]; then
            echo -e "${RED}  WARNING: This permanently deletes containers, volumes, networks, and $INSTALL_DIR${NC}"
            read -r -p "  Type WIPE to continue: " WIPE_CONFIRM < /dev/tty
            if [ "$WIPE_CONFIRM" != "WIPE" ]; then
                echo -e "${YELLOW}  Wipe cancelled by user.${NC}"
                exit 1
            fi
        else
            echo -e "${RED}x Non-interactive wipe requires FORCE_WIPE=1${NC}"
            exit 1
        fi
    fi

    if [ -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
        cd "$INSTALL_DIR"
        docker compose -f "$COMPOSE_FILE" down -v --remove-orphans 2>/dev/null || true
    fi

    SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly-hosting" -q 2>/dev/null || true)
    if [ -n "$SMSLY_CONTAINERS" ]; then
        docker rm -f $SMSLY_CONTAINERS 2>/dev/null || true
    fi

    SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly-hosting" -q 2>/dev/null || true)
    if [ -n "$SMSLY_VOLUMES" ]; then
        for vol in $SMSLY_VOLUMES; do
            docker volume rm "$vol" 2>/dev/null || true
        done
    fi

    SMSLY_NETWORKS=$(docker network ls --filter "name=smsly-hosting" -q 2>/dev/null || true)
    if [ -n "$SMSLY_NETWORKS" ]; then
        for net in $SMSLY_NETWORKS; do
            docker network rm "$net" 2>/dev/null || true
        done
    fi

    # Clean up Caddy watcher service (prevents stale config on reinstall)
    systemctl stop caddy-watcher 2>/dev/null || true
    systemctl disable caddy-watcher 2>/dev/null || true
    rm -f /etc/systemd/system/caddy-watcher.service

    # Reset Caddyfile to default (prevents stale routing)
    if [ -f /etc/caddy/Caddyfile ]; then
        echo ':80 { respond "Caddy is running" 200 }' > /etc/caddy/Caddyfile
        systemctl reload caddy 2>/dev/null || true
    fi

    # Remove Cloudflare token override
    rm -rf /etc/systemd/system/caddy.service.d
    systemctl daemon-reload 2>/dev/null || true

    rm -rf "$INSTALL_DIR"
    rm -f "$LOG_FILE"

    trap - EXIT
    echo -e "${GREEN}OK Wipe complete. The server is ready for a fresh install.${NC}"
    echo -e "${YELLOW}  Run: curl -fsSL https://raw.githubusercontent.com/SMSLYCLOUD/smsly-hosting/main/install.sh -o /tmp/install.sh && sudo bash /tmp/install.sh${NC}"
    exit 0
}

if [ "$WIPE_MODE" = "true" ]; then
    wipe_existing_install
fi

ensure_update_networks() {
    # Never delete data networks/volumes in update mode. Only (re)create if missing.
    docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null 2>&1 || true
    docker network inspect smsly-proxy >/dev/null 2>&1 || docker network create smsly-proxy >/dev/null 2>&1 || true
    docker network inspect socket-proxy >/dev/null 2>&1 || docker network create --driver bridge --internal socket-proxy >/dev/null 2>&1 || true
}

ensure_caddy_config_permissions() {
    local caddy_config_dir="/opt/smsly-hosting/caddy-config"

    mkdir -p "$caddy_config_dir"

    # Backend container writes /caddy-config as uid/gid 1000 ("smsly").
    # Keep this idempotent and non-fatal for heterogeneous hosts.
    if id smsly >/dev/null 2>&1; then
        chown -R smsly:smsly "$caddy_config_dir" 2>/dev/null || true
    else
        chown -R 1000:1000 "$caddy_config_dir" 2>/dev/null || true
    fi
    # Keep directory group-writable and sticky for future files.
    chmod -R u+rwX,g+rwX "$caddy_config_dir" 2>/dev/null || true
    find "$caddy_config_dir" -type d -exec chmod 2775 {} + 2>/dev/null || true

    # Ensure key files are writable by backend uid 1000.
    [ -f "$caddy_config_dir/Caddyfile" ] && chmod 664 "$caddy_config_dir/Caddyfile" 2>/dev/null || true
    [ -f "$caddy_config_dir/.reload" ] && chmod 664 "$caddy_config_dir/.reload" 2>/dev/null || true
    [ -f "$caddy_config_dir/.cloudflare_token" ] && chmod 600 "$caddy_config_dir/.cloudflare_token" 2>/dev/null || true
    [ -f "$caddy_config_dir/.cloudflare_token_clear" ] && chmod 600 "$caddy_config_dir/.cloudflare_token_clear" 2>/dev/null || true

    # Fast write probe so --update can self-heal before UI writes Caddyfile.
    echo "perm-check $(date +%s)" > "$caddy_config_dir/.perm_probe" 2>/dev/null || true
}

ensure_container_on_network() {
    local network_name="$1"
    local container_name="$2"

    [ -z "$network_name" ] && return 0
    [ -z "$container_name" ] && return 0

    docker container inspect "$container_name" >/dev/null 2>&1 || return 0
    docker network inspect "$network_name" >/dev/null 2>&1 || return 0
    docker network connect "$network_name" "$container_name" >/dev/null 2>&1 || true
}

# --- Shared Caddy Safety Function --------------------------------------------
# Called from: recover_runtime_stack, update flow, restart_edge_stack.
# Generates a safe fallback Caddyfile when the current one is broken or risky.
# - Discovers domain from DB first, falls back to .env
# - Skips HTTPS blocks for IP addresses (certs can't be issued)
# - Adds individual Caddy blocks for each deployed service (HTTP-01 SSL)
# - Detects dns cloudflare + missing systemd override (validates passes, runtime crashes)
generate_safe_caddyfile() {
    local reason="${1:-unknown}"
    echo -e "${YELLOW}  Generating safe fallback Caddyfile...${NC}"

    # 1. Discover domain: DB first, .env fallback
    local domain=""
    domain="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
" 2>/dev/null | tr -d '[:space:]' || true)"
    if [ -z "$domain" ]; then
        domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    fi

    # 2. Discover ALL deployed service domains from DB (public + custom)
    local svc_blocks=""
    svc_blocks="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
allowlisted_services = $SERVICE_ALLOWLIST_PY
queryset = Service.objects.all()
if allowlisted_services:
    queryset = queryset.filter(name__in=allowlisted_services)
for svc in queryset.exclude(public_domain__isnull=True).exclude(public_domain=''):
    d = svc.public_domain.strip()
    if d:
        print(f'{d} {{\n    reverse_proxy localhost:8081\n    encode gzip\n}}\n')
    for cd in (svc.custom_domains or []):
        cd = cd.strip()
        if cd:
            print(f'{cd} {{\n    reverse_proxy localhost:8081\n    encode gzip\n}}\n')
" 2>/dev/null | tr -d '\r' || true)"

    # 3. Check if domain is a real hostname (not an IP address)
    local is_real_domain=false
    if [ -n "$domain" ] && [ "$domain" != "localhost" ]; then
        if ! echo "$domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
            is_real_domain=true
        fi
    fi

    # 4. Build the Caddyfile
    if [ "$is_real_domain" = "true" ]; then
        cat > /etc/caddy/Caddyfile <<SAFECADDY
# Auto-generated safe fallback (reason: $reason)
# Individual service domains get SSL via Let's Encrypt HTTP-01 challenge.
# Set CLOUDFLARE_API_TOKEN in .env and run --update to re-enable wildcard SSL.
${domain} {
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

:80 {
    handle {
        rewrite * /notice
        reverse_proxy localhost:8090
    }
}

${svc_blocks}
SAFECADDY
    else
        cat > /etc/caddy/Caddyfile <<SAFECADDY
# Auto-generated safe fallback (reason: $reason)
:80 {
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

${svc_blocks}
SAFECADDY
    fi
    caddy fmt --overwrite /etc/caddy/Caddyfile 2>/dev/null || true
    echo -e "${YELLOW}  [WARN] Wildcard HTTPS disabled. Individual service domains have HTTP-01 SSL.${NC}"