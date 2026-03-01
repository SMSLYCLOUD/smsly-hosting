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
#   - Update mode: git stash → pull → rebuild → restart
#   - Disk space pre-check (prevents mid-build failures)
#   - Nginx config verification (prevents 502 from default config)
#   - Caddyfile IP catch-all (prevents unreachable dashboard)
# =============================================================================

set -euo pipefail

# ─── Resolve script path BEFORE any cd (screen guard needs absolute path) ────
SCRIPT_PATH="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

# ─── Screen Session Guard (survives SSH disconnects) ─────────────────────────
# Collect ALL interactive input FIRST (before screen), then re-launch inside
# a screen session with the collected values as env vars.
# To reattach after disconnect: screen -r cloudneuron-install
if [ -z "${STY:-}" ] && [ -z "${SKIP_SCREEN:-}" ]; then
    # Install screen if missing
    if ! command -v screen &> /dev/null; then
        apt-get update -qq && apt-get install -y screen > /dev/null 2>&1
    fi

    # ── Pre-collect interactive input (only for fresh installs) ──────────
    # Skip collection if values are already pre-seeded via env vars, or if
    # this is an --update / --wipe run (those don't need interactive input).
    _ARG1="${1:-}"
    if [[ "$_ARG1" != "--update"* ]] && [[ "$_ARG1" != "--wipe" ]] && [[ "$_ARG1" != "--recover" ]] && [[ "$_ARG1" != "--debug" ]] && [ -z "${USE_SSL:-}" ]; then
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

# ─── Constants ───────────────────────────────────────────────────────────────
gen_hex_secret() {
    local bytes="${1:-16}"
    python3 -c "import secrets; print(secrets.token_hex(${bytes}))" 2>/dev/null || openssl rand -hex "$bytes"
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
        # Use SOH (\x01) as sed delimiter — safe for URLs, passwords, pipes
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

# ─── Parse Arguments ─────────────────────────────────────────────────────────
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
echo "  SMSLY Hosting Install Log — $(date -Iseconds)"
echo "  Mode: $MODE_LABEL"
echo "═══════════════════════════════════════════════════════════"

# ─── Rollback Trap ──────────────────────────────────────────────────────────
cleanup_on_failure() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo -e "\n${RED}════════════════════════════════════════════════════════════${NC}"
        echo -e "${RED}  INSTALLATION FAILED (exit code: $exit_code)${NC}"
        echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
        echo -e "${YELLOW}  → Rolling back...${NC}"

        # Stop any containers that were started
        if [ -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
            cd "$INSTALL_DIR" 2>/dev/null || true
            docker compose -f "$COMPOSE_FILE" down 2>/dev/null || true
        fi

        # Restore backup .env if one was created
        if [ -f "$INSTALL_DIR/.env.backup" ]; then
            echo -e "${YELLOW}  → Restoring previous .env from backup${NC}"
            mv "$INSTALL_DIR/.env.backup" "$INSTALL_DIR/.env" 2>/dev/null || true
        fi

        # Restore git stash if we stashed
        if [ -f "$INSTALL_DIR/.git-stash-marker" ]; then
            echo -e "${YELLOW}  → Restoring git stash (rolling back code changes)${NC}"
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
# WIPE MODE — Remove all install artifacts for a clean re-install
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

ensure_container_on_network() {
    local network_name="$1"
    local container_name="$2"

    [ -z "$network_name" ] && return 0
    [ -z "$container_name" ] && return 0

    docker container inspect "$container_name" >/dev/null 2>&1 || return 0
    docker network inspect "$network_name" >/dev/null 2>&1 || return 0
    docker network connect "$network_name" "$container_name" >/dev/null 2>&1 || true
}

# ─── Shared Caddy Safety Function ────────────────────────────────────────────
# Called from: recover_runtime_stack, update flow, restart_edge_stack.
# Generates a safe fallback Caddyfile when the current one is broken or risky.
# - Discovers domain from DB first, falls back to .env
# - Skips HTTPS blocks for IP addresses (certs can't be issued)
# - Adds individual Caddy blocks for each deployed service (HTTP-01 SSL)
# - Detects dns cloudflare + missing systemd override (validates passes, runtime crashes)
generate_safe_caddyfile() {
    local reason="${1:-unknown}"
    echo -e "${YELLOW}  ⚠ Generating safe fallback Caddyfile (reason: $reason)...${NC}"

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

    # 2. Discover ALL deployed service domains from DB
    local svc_blocks=""
    svc_blocks="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
for svc in Service.objects.filter(is_active=True).exclude(public_domain__isnull=True).exclude(public_domain=''):
    d = svc.public_domain.strip()
    if d:
        print(f'{d} {{\n    reverse_proxy localhost:8081\n    encode gzip\n}}\n')
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
    reverse_proxy localhost:8090
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
    echo -e "${YELLOW}  ⚠ Wildcard HTTPS disabled. Individual service domains have HTTP-01 SSL.${NC}"
}

# Returns 0 if Caddy config needs fixing, 1 if it's fine.
caddy_needs_fix() {
    if ! caddy validate --config /etc/caddy/Caddyfile 2>/dev/null; then
        return 0  # Syntax error
    fi
    if grep -q 'dns cloudflare' /etc/caddy/Caddyfile 2>/dev/null \
       && [ ! -f /etc/systemd/system/caddy.service.d/override.conf ]; then
        return 0  # dns cloudflare without token override = runtime crash
    fi
    return 1  # Config is fine
}

bust_core_build_cache() {
    echo -e "${BLUE}  -> Busting frontend/backend build cache (safe mode)...${NC}"

    # Remove old app image layers for deterministic rebuilds (no DB/data touched).
    for svc in frontend backend celery celery-beat; do
        local image_ids=""
        image_ids="$(docker compose -f "$COMPOSE_FILE" images -q "$svc" 2>/dev/null | awk 'NF' | sort -u || true)"
        if [ -n "$image_ids" ]; then
            while read -r image_id; do
                [ -n "$image_id" ] && docker rmi -f "$image_id" >/dev/null 2>&1 || true
            done <<< "$image_ids"
        fi
    done

    # Build cache only (no global container/image prune).
    docker builder prune -af >/dev/null 2>&1 || true
    echo -e "${GREEN}  OK Cache bust complete (targeted images + build cache)${NC}"
}

restart_edge_stack() {
    local edge_services="socket-proxy traefik route-fallback nginx"

    echo -e "${BLUE}  -> Refreshing edge proxy stack (nginx/traefik/socket-proxy/route-fallback)...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps $edge_services >/dev/null 2>&1 || \
        docker compose -f "$COMPOSE_FILE" up -d --force-recreate $edge_services >/dev/null 2>&1 || true

    # Restart core app entrypoints so new upstream bindings are live.
    docker compose -f "$COMPOSE_FILE" restart frontend backend >/dev/null 2>&1 || true
    docker compose -f "$COMPOSE_FILE" restart $edge_services >/dev/null 2>&1 || true

    # Re-attach expected external networks (idempotent).
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-nginx-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    # Validate Caddy config before restart (H1 fix)
    if command -v caddy >/dev/null 2>&1; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "restart_edge_stack validation"
        fi
        systemctl restart caddy >/dev/null 2>&1 || true
        systemctl restart caddy-watcher >/dev/null 2>&1 || true
    fi
    echo -e "${GREEN}  OK Edge stack refreshed${NC}"
}

wait_for_container_ready() {
    local container_name="$1"
    local timeout_seconds="${2:-180}"
    local elapsed=0
    local state=""

    [ -z "$container_name" ] && return 1

    while [ "$elapsed" -lt "$timeout_seconds" ]; do
        state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name" 2>/dev/null || echo "missing")"
        if [ "$state" = "healthy" ] || [ "$state" = "running" ]; then
            echo -e "${GREEN}  OK $container_name is $state${NC}"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done

    echo -e "${YELLOW}  WARN $container_name not ready after ${timeout_seconds}s (state=$state)${NC}"
    return 1
}

recover_runtime_stack() {
    echo -e "${BLUE}  -> Running runtime recovery (network + core services + edge)...${NC}"

    ensure_update_networks

    if systemctl list-unit-files docker.service >/dev/null 2>&1; then
        echo -e "${BLUE}    -> Restarting Docker daemon...${NC}"
        systemctl restart docker >/dev/null 2>&1 || true
        sleep 8
        ensure_update_networks
    fi

    echo -e "${BLUE}    -> Starting dependency services...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d db pgbouncer redis socket-proxy registry || true
    wait_for_container_ready "smsly-hosting-db-1" 120 || true
    wait_for_container_ready "smsly-hosting-pgbouncer-1" 120 || true
    wait_for_container_ready "smsly-hosting-redis-1" 120 || true

    echo -e "${BLUE}    -> Starting app services...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d backend frontend || true
    wait_for_container_ready "smsly-hosting-backend-1" 180 || true
    wait_for_container_ready "smsly-hosting-frontend-1" 120 || true

    echo -e "${BLUE}    -> Starting workers and edge services...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d celery celery-beat traefik route-fallback nginx || true

    # Re-attach expected networks (idempotent)
    ensure_container_on_network "smsly-net" "smsly-hosting-backend-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-frontend-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-nginx-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    if command -v caddy >/dev/null 2>&1; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "recover_runtime_stack"
        fi
        systemctl restart caddy >/dev/null 2>&1 || true
        systemctl restart caddy-watcher >/dev/null 2>&1 || true
    fi

    echo -e "${GREEN}  OK Runtime recovery completed${NC}"
}

debug_platform_status() {
    set +e
    echo -e "\n${YELLOW}=== SMSLY DEBUG SNAPSHOT ===${NC}"
    echo "Timestamp: $(date -Iseconds)"
    echo "Install dir: $INSTALL_DIR"
    echo ""

    echo "---- Systemd ----"
    systemctl is-active docker 2>/dev/null || true
    systemctl is-active caddy 2>/dev/null || true
    systemctl is-active caddy-watcher 2>/dev/null || true
    systemctl is-active smsly-autoscaler 2>/dev/null || true
    echo ""

    echo "---- Docker Networks ----"
    docker network ls | grep -E 'smsly|socket-proxy' || true
    echo ""

    echo "---- Compose PS ----"
    docker compose -f "$COMPOSE_FILE" ps || true
    echo ""

    echo "---- Local Health ----"
    curl -iSsf http://127.0.0.1:8090/health 2>/dev/null | head -20 || echo "http://127.0.0.1:8090/health failed"
    curl -iSsf http://127.0.0.1/health 2>/dev/null | head -20 || echo "http://127.0.0.1/health failed"
    echo ""

    echo "---- Backend DNS Checks ----"
    docker compose -f "$COMPOSE_FILE" exec -T backend getent hosts db pgbouncer redis 2>/dev/null || echo "backend DNS check failed"
    echo ""

    echo "---- Key Logs (tail 120) ----"
    docker compose -f "$COMPOSE_FILE" logs --tail=120 backend frontend nginx traefik pgbouncer redis 2>/dev/null || true
    echo -e "${YELLOW}=== END DEBUG SNAPSHOT ===${NC}\n"
    set -e
}

# =============================================================================
# DEBUG/RECOVER MODES
# =============================================================================
if [ "$DEBUG_MODE" = "true" ]; then
    cd "$INSTALL_DIR" 2>/dev/null || cd /root 2>/dev/null || cd /
    debug_platform_status
    exit 0
fi

if [ "$RECOVER_MODE" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --recover)${NC}"
        exit 1
    fi
    if [ ! -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
        echo -e "${RED}x Missing $INSTALL_DIR/$COMPOSE_FILE. Run fresh install first.${NC}"
        exit 1
    fi
    cd "$INSTALL_DIR"
    ensure_env_runtime_defaults "$INSTALL_DIR/.env" || true
    recover_runtime_stack
    debug_platform_status
    exit 0
fi

# =============================================================================
# VERIFY MODE — Run endpoint checks only (no changes)
# =============================================================================
if [ "${VERIFY_MODE:-false}" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --verify)${NC}"
        exit 1
    fi
    cd "$INSTALL_DIR" 2>/dev/null || { echo -e "${RED}x $INSTALL_DIR not found. Run fresh install first.${NC}"; exit 1; }

    DOMAIN="$(env_get_value "$INSTALL_DIR/.env" "DOMAIN" 2>/dev/null || echo "")"

    echo -e "\n${BLUE}  → Running endpoint verification...${NC}"
    PASS_COUNT=0
    FAIL_COUNT=0

    # Backend health
    EP1_URL="http://127.0.0.1:8090/health"
    EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 -L "$EP1_URL" 2>/dev/null || echo "000")
    if [ "$EP1_CODE" = "200" ] || [ "$EP1_CODE" = "301" ]; then
        echo -e "${GREEN}  ✓ Backend: HTTP $EP1_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Backend: HTTP $EP1_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # HTTPS domain
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ]; then
        EP2_URL="https://${DOMAIN}/health"
        EP2_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 10 -L "$EP2_URL" 2>/dev/null || echo "000")
        if [ "$EP2_CODE" = "200" ] || [ "$EP2_CODE" = "301" ]; then
            echo -e "${GREEN}  ✓ HTTPS: HTTP $EP2_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
        else
            echo -e "${RED}  ✗ HTTPS: HTTP $EP2_CODE ($EP2_URL)${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    fi

    # Traefik
    EP3_URL="http://127.0.0.1:8081/"
    EP3_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP3_URL" 2>/dev/null || echo "000")
    if [ "$EP3_CODE" != "000" ] && [ "$EP3_CODE" != "502" ] && [ "$EP3_CODE" != "503" ]; then
        echo -e "${GREEN}  ✓ Traefik: HTTP $EP3_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Traefik: HTTP $EP3_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # Deployed service domains
    ALL_SVC_DOMAINS="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
for s in Service.objects.filter(is_active=True).exclude(public_domain__isnull=True).exclude(public_domain=''):
    print(f'{s.name}|{s.public_domain.strip()}')
" 2>/dev/null | tr -d '\r' || true)"

    if [ -n "$ALL_SVC_DOMAINS" ]; then
        while IFS='|' read -r svc_name svc_domain; do
            [ -z "$svc_domain" ] && continue
            svc_url="https://${svc_domain}/"
            svc_code=$(curl -so /dev/null -w '%{http_code}' --max-time 8 -L "$svc_url" 2>/dev/null || echo "000")
            if [ "$svc_code" != "000" ] && [ "$svc_code" != "502" ] && [ "$svc_code" != "503" ]; then
                echo -e "${GREEN}  ✓ $svc_name ($svc_domain): HTTP $svc_code${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
            else
                echo -e "${RED}  ✗ $svc_name ($svc_domain): HTTP $svc_code${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
        done <<< "$ALL_SVC_DOMAINS"
    fi

    TOTAL=$((PASS_COUNT + FAIL_COUNT))
    if [ "$FAIL_COUNT" -eq 0 ]; then
        echo -e "\n${GREEN}  ✓ All $PASS_COUNT/$TOTAL checks passed${NC}"
    else
        echo -e "\n${YELLOW}  ⚠ $PASS_COUNT passed, $FAIL_COUNT failed out of $TOTAL checks${NC}"
    fi

    docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
        docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true
    exit 0
fi

# =============================================================================
# UPDATE MODE — Fast path for pulling latest code and rebuilding
# =============================================================================
if [ -n "$UPDATE_MODE" ]; then
    echo -e "${YELLOW}[UPDATE] Running in update mode: $UPDATE_MODE${NC}"
    echo -e "${BLUE}  -> Safe update: preserves database/redis volumes and addon data.${NC}"

    # Ensure repo cache directory exists for user service builds
    mkdir -p /opt/smsly-cache/repos
    chmod 775 /opt/smsly-cache
    chown -R 1000:1000 /opt/smsly-cache 2>/dev/null || true

    # ─── Pre-flight ──────────────────────────────────────────────────────────
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}✗ Please run as root (sudo bash install.sh --update)${NC}"
        exit 1
    fi

    if [ ! -d "$INSTALL_DIR/.git" ]; then
        echo -e "${RED}✗ No git repository found at $INSTALL_DIR. Run a fresh install first.${NC}"
        exit 1
    fi

    if [ ! -f "$INSTALL_DIR/.env" ]; then
        echo -e "${RED}✗ No .env file found. Run a fresh install first.${NC}"
        exit 1
    fi

    cd "$INSTALL_DIR"

    echo -e "${BLUE}  -> Validating existing .env configuration...${NC}"
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x .env validation failed. Fix the values above and re-run update.${NC}"
        exit 1
    fi


    # ─── Git Stash + Pull (CRITICAL BLINDSPOT FIX) ───────────────────────────
    echo -e "${BLUE}  → Checking for local changes...${NC}"
    if ! git diff --quiet HEAD 2>/dev/null || ! git diff --cached --quiet HEAD 2>/dev/null; then
        echo -e "${YELLOW}  ⚠ Local changes detected — stashing before pull${NC}"
        git stash push -m "install-update-$(date +%s)"
        touch "$INSTALL_DIR/.git-stash-marker"
    fi

    echo -e "${BLUE}  → Pulling latest code from GitHub...${NC}"
    git pull origin main

    # Clean up stash marker (pull succeeded, we commit to the new code)
    rm -f "$INSTALL_DIR/.git-stash-marker"

    # ─── Validate required files exist ───────────────────────────────────────
    echo -e "${BLUE}  → Validating deployment files...${NC}"

    if [ ! -f "$COMPOSE_FILE" ]; then
        echo -e "${RED}✗ Missing $COMPOSE_FILE — cannot deploy.${NC}"
        exit 1
    fi

    if [ ! -f "nginx.conf" ]; then
        echo -e "${RED}✗ Missing nginx.conf — cannot deploy. This file is required for routing.${NC}"
        exit 1
    fi

    if [ ! -f "backend/Dockerfile" ]; then
        echo -e "${RED}✗ Missing backend/Dockerfile${NC}"
        exit 1
    fi

    if [ ! -f "frontend/Dockerfile" ]; then
        echo -e "${RED}✗ Missing frontend/Dockerfile${NC}"
        exit 1
    fi

    echo -e "${GREEN}  ✓ All required files present${NC}"

    # ─── Disk space check (prevents mid-build failure) ───────────────────────
    DISK_AVAIL_MB=$(df -BM "$INSTALL_DIR" | tail -1 | awk '{print $4}' | tr -d 'M')
    if [ "$DISK_AVAIL_MB" -lt 2000 ]; then
        echo -e "${YELLOW}  ⚠ WARNING: Only ${DISK_AVAIL_MB}MB disk space available.${NC}"
        echo -e "${YELLOW}    Docker builds typically need 2GB+. Cleaning safe caches (no volume deletion)...${NC}"
        bust_core_build_cache
        DISK_AVAIL_MB=$(df -BM "$INSTALL_DIR" | tail -1 | awk '{print $4}' | tr -d 'M')
        echo -e "${BLUE}  → Disk space after cleanup: ${DISK_AVAIL_MB}MB${NC}"
        if [ "$DISK_AVAIL_MB" -lt 1000 ]; then
            echo -e "${RED}  ✗ Still insufficient disk space (${DISK_AVAIL_MB}MB). Need at least 1GB.${NC}"
            exit 1
        fi
    fi

    # ─── Targeted Rebuild (CRITICAL BLINDSPOT FIX: --no-deps) ────────────────
    # Using --no-deps prevents cascade restart of unrelated services

    # ─── Fix script permissions (Git on Windows strips execute bits) ──────────
    echo -e "${BLUE}  → Fixing script permissions...${NC}"
    find "$INSTALL_DIR" -name "*.sh" -exec chmod +x {} \;
    echo -e "${GREEN}  ✓ Script permissions fixed${NC}"

    # Ensure shared networks exist (prod stack uses external networks)
    ensure_update_networks

    # Cache bust only if disk is low (already runs in the disk check above when needed).
    # Moved into case blocks below to avoid redundant double bust.

    case "$UPDATE_MODE" in
        frontend)
            echo -e "${BLUE}  → Rebuilding frontend container only...${NC}"
            docker compose -f "$COMPOSE_FILE" build --no-cache frontend
            docker compose -f "$COMPOSE_FILE" up -d --no-deps frontend
            ;;
        backend)
            echo -e "${BLUE}  → Rebuilding backend containers...${NC}"
            docker compose -f "$COMPOSE_FILE" build --no-cache backend celery
            echo -e "${BLUE}  → Ensuring backend dependencies are running...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d db pgbouncer redis socket-proxy
            docker compose -f "$COMPOSE_FILE" up -d --no-deps backend

            echo -e "${BLUE}  → Running makemigrations + migrations...${NC}"
            sleep 10  # Wait for backend to start
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py makemigrations --noinput 2>&1 || \
                echo -e "${YELLOW}  ⚠ makemigrations had issues (non-fatal)${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py migrate --noinput || {
                echo -e "${YELLOW}  ⚠ Migration failed — backend may still be starting. Retrying in 15s...${NC}"
                sleep 15
                docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py migrate --noinput
            }

            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput

            # Clean stale celerybeat-schedule (prevents Permission denied crash loop)
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true

            echo -e "${BLUE}  → Restarting celery workers...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d --no-deps celery celery-beat
            ;;
        full)
            echo -e "${BLUE}  → [FULL REBUILD] Rebuilding PaaS core (preserving addon databases)...${NC}"

            # 1. Only stop PaaS core services — NEVER touch addon containers
            CORE_SERVICES="frontend backend celery celery-beat nginx traefik socket-proxy route-fallback"
            echo -e "${BLUE}    ↳ Stopping core services...${NC}"
            docker compose -f "$COMPOSE_FILE" stop $CORE_SERVICES 2>/dev/null || true
            docker compose -f "$COMPOSE_FILE" rm -f $CORE_SERVICES 2>/dev/null || true

            # 2. Remove old PaaS images (NOT addon images)
            echo -e "${BLUE}    ↳ Removing old core images...${NC}"
            for svc in $CORE_SERVICES; do
                img=$(docker compose -f "$COMPOSE_FILE" config --images 2>/dev/null | grep -i "$svc" || true)
                if [ -n "$img" ]; then
                    docker rmi -f "$img" 2>/dev/null || true
                fi
            done

            # 3. Prune dangling images and build cache
            echo -e "${BLUE}    ↳ Pruning dangling images and build cache...${NC}"
            docker builder prune -af 2>/dev/null || true

            # 4. Ensure shared networks exist (create if missing, don't destroy)
            echo -e "${BLUE}    ↳ Ensuring networks exist...${NC}"
            ensure_update_networks

            # 5. Rebuild core images from scratch
            echo -e "${BLUE}    ↳ Rebuilding core images (no cache)...${NC}"
            docker compose -f "$COMPOSE_FILE" build --no-cache $CORE_SERVICES

            # 6. Start everything (addons stay running, core gets fresh containers)
            echo -e "${BLUE}    ↳ Starting all services...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d --force-recreate $CORE_SERVICES

            # 7. Reconnect Traefik + socket-proxy to smsly-proxy network
            #    (recreation drops Docker DNS links — causes 502 gateway errors)
            echo -e "${BLUE}    ↳ Reconnecting proxy network...${NC}"
            for ctr in smsly-hosting-traefik-1 smsly-hosting-socket-proxy-1; do
                ensure_container_on_network "smsly-proxy" "$ctr"
            done
            docker restart smsly-hosting-traefik-1 2>/dev/null || true

            # 8. Run migrations (as root to avoid PermissionError writing migration files)
            echo -e "${BLUE}  → Running makemigrations + migrations...${NC}"
            echo -e "${BLUE}  → Ensuring backend dependencies are running...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d db pgbouncer redis socket-proxy
            sleep 10
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py makemigrations --noinput 2>&1 || \
                echo -e "${YELLOW}  ⚠ makemigrations had issues (non-fatal)${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py migrate --noinput || {
                echo -e "${YELLOW}  ⚠ Migration failed — backend may still be starting. Retrying in 15s...${NC}"
                sleep 15
                docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py migrate --noinput
            }

            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput

            # 9. Clean celerybeat-schedule and restart beat
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true
            docker compose -f "$COMPOSE_FILE" restart celery-beat 2>/dev/null || true
            ;;
    esac

    # ─── Ensure Local Docker cloud provider exists ──────────────────────────
    echo -e "${BLUE}  → Ensuring Local Docker cloud provider exists...${NC}"
    echo "
from apps.cloud.models import CloudProvider
cp, created = CloudProvider.objects.get_or_create(
    provider_type='LOCAL',
    defaults={'name': 'Local Docker', 'is_active': True}
)
if not created and not cp.is_active:
    cp.is_active = True
    cp.save()
" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null || true
    echo -e "${GREEN}  ✓ Cloud provider ready${NC}"

    # Refresh proxy/runtime edge stack so routing and TLS state is always clean.
    # NOTE: restart_edge_stack now handles Caddy validation internally (H1+H2 fix).
    restart_edge_stack

    # Verify nginx loaded the correct custom config (not the default)
    sleep 2
    NGINX_CONFIG_CHECK=$(docker exec smsly-hosting-nginx-1 head -1 /etc/nginx/nginx.conf 2>/dev/null || echo "FAIL")
    if echo "$NGINX_CONFIG_CHECK" | grep -q "events"; then
        echo -e "${GREEN}  ✓ Nginx config verified (custom proxy config loaded)${NC}"
    else
        echo -e "${RED}  ✗ WARNING: Nginx may be running default config!${NC}"
        echo -e "${YELLOW}    Expected 'events {' but got: $NGINX_CONFIG_CHECK${NC}"
        echo -e "${YELLOW}    Fix: docker compose -f $COMPOSE_FILE up -d --force-recreate nginx${NC}"
    fi

    # ─── Caddy: Regenerate Caddyfile from DB (picks up new service domains) ──
    if command -v caddy &> /dev/null; then
        echo -e "${BLUE}  → Regenerating Caddyfile with current service domains...${NC}"
        docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
from services.caddy_manager import generate_caddyfile, apply_caddyfile
config = PlatformConfig.load()
content = generate_caddyfile(config)
result = apply_caddyfile(content)
print('OK' if result.get('ok') else f'FAIL: {result.get(\"message\")}')
" 2>/dev/null || echo -e "${YELLOW}  ⚠ Caddyfile regeneration skipped (backend not ready)${NC}"

        # ── Sync Cloudflare token to Caddy systemd override ──
        # The Caddyfile uses {env.CLOUDFLARE_API_TOKEN} — Caddy reads this from
        # its systemd environment. If the override is missing or token is empty,
        # Caddy will crash. This ensures the token is always synced.
        CADDY_OVERRIDE_DIR="/etc/systemd/system/caddy.service.d"
        CADDY_OVERRIDE_FILE="$CADDY_OVERRIDE_DIR/override.conf"
        CF_TOKEN=""

        # Priority: existing systemd override > .env file > PlatformConfig DB
        if [ -f "$CADDY_OVERRIDE_FILE" ]; then
            CF_TOKEN="$(grep 'CLOUDFLARE_API_TOKEN=' "$CADDY_OVERRIDE_FILE" 2>/dev/null | sed 's/.*CLOUDFLARE_API_TOKEN=//;s/"$//' || true)"
        fi
        if [ -z "$CF_TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
            CF_TOKEN="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
        fi
        # Fallback: read from PlatformConfig in the database (set via Settings UI)
        if [ -z "$CF_TOKEN" ] || [ "$CF_TOKEN" = "fake" ]; then
            DB_TOKEN="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
config = PlatformConfig.load()
token = (getattr(config, 'cloudflare_api_token', '') or '').strip()
if token and token.lower() not in ('fake', 'changeme', 'test', ''):
    print(token)
" 2>/dev/null || true)"
            DB_TOKEN="$(echo "$DB_TOKEN" | tr -d '[:space:]')"
            if [ -n "$DB_TOKEN" ]; then
                CF_TOKEN="$DB_TOKEN"
                echo -e "${GREEN}  ✓ Cloudflare token found in Settings DB${NC}"
                # Sync back to .env so it persists
                if grep -q 'CLOUDFLARE_API_TOKEN' "$INSTALL_DIR/.env" 2>/dev/null; then
                    sed -i "s/CLOUDFLARE_API_TOKEN=.*/CLOUDFLARE_API_TOKEN=$CF_TOKEN/" "$INSTALL_DIR/.env"
                else
                    echo "CLOUDFLARE_API_TOKEN=$CF_TOKEN" >> "$INSTALL_DIR/.env"
                fi
            fi
        fi

        if [ -n "$CF_TOKEN" ] && [ "$CF_TOKEN" != "fake" ]; then
            # Token available — ensure systemd override is set
            mkdir -p "$CADDY_OVERRIDE_DIR"
            cat > "$CADDY_OVERRIDE_FILE" <<ENVEOF
[Service]
ExecStart=
ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile
Environment="CLOUDFLARE_API_TOKEN=$CF_TOKEN"
ENVEOF
            chmod 600 "$CADDY_OVERRIDE_FILE"
            systemctl daemon-reload
            echo -e "${GREEN}  ✓ Cloudflare token synced to Caddy${NC}"
        else
            # No valid token — strip dns cloudflare blocks to prevent crash
            if grep -q 'dns cloudflare' /etc/caddy/Caddyfile 2>/dev/null; then
                echo -e "${YELLOW}  ⚠ No Cloudflare token — removing DNS challenge from Caddyfile${NC}"
                python3 -c "
import re
with open('/etc/caddy/Caddyfile') as f:
    content = f.read()
content = re.sub(r'\s*tls\s*\{[^}]*\}\s*\n?', '\n', content)
with open('/etc/caddy/Caddyfile', 'w') as f:
    f.write(content)
print('Stripped tls blocks')
" 2>/dev/null || true
                echo -e "${YELLOW}  ⚠ Wildcard HTTPS disabled. Set CLOUDFLARE_API_TOKEN in .env to re-enable.${NC}"
            fi
        fi

        # ROBUST SAFETY: Use shared function (C4 fix — single source of truth)
        if caddy_needs_fix; then
            generate_safe_caddyfile "update flow validation"
        fi

        systemctl restart caddy 2>/dev/null || true
        systemctl restart caddy-watcher 2>/dev/null || true

        # Verify Caddy is running
        sleep 2
        if systemctl is-active --quiet caddy 2>/dev/null; then
            echo -e "${GREEN}  ✓ Caddy config regenerated and running${NC}"
        else
            echo -e "${YELLOW}  ⚠ Caddy failed to start. Run: journalctl -u caddy --no-pager -n 20${NC}"
        fi
    fi

    # ─── Auto-redeploy active services (only if platform code changed) ──
    # H6 fix: Only redeploy if git detected actual changes (prevents unnecessary deploys)
    GIT_CHANGES="$(cd "$INSTALL_DIR" && git diff HEAD@{1} --name-only 2>/dev/null | head -5 || true)"
    if [ -n "$GIT_CHANGES" ]; then
        echo -e "${BLUE}  → Auto-redeploying active services (platform code changed)...${NC}"
        docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
import traceback
try:
    from apps.deployments.models import Service, Deployment
    from apps.cloud.models import CloudProvider
    from apps.deployments.tasks import smart_deploy_task
    from django.utils import timezone
    provider = CloudProvider.objects.filter(is_active=True).first()
    if not provider:
        print('WARN: No active cloud provider')
    else:
        count = 0
        for svc in Service.objects.filter(is_active=True):
            dep = svc.deployments.filter(status='ACTIVE').order_by('-created_at').first()
            if not dep or not dep.commit_hash:
                continue
            svc.deployments.filter(status='ACTIVE').update(
                status='CANCELLED', finished_at=timezone.now())
            new_dep = Deployment.objects.create(
                service=svc,
                status='QUEUED',
                commit_hash=dep.commit_hash,
                commit_message='Platform update auto-redeploy',
            )
            smart_deploy_task.delay(str(new_dep.id), str(provider.id), skip_review=True)
            count += 1
            print(f'  Queued: {svc.name} ({dep.commit_hash[:7]})')
        print(f'OK: {count} service(s) queued for redeploy')
except Exception as e:
    print(f'WARN: {e}')
    traceback.print_exc()
" 2>/dev/null || echo -e "${YELLOW}  ⚠ Auto-redeploy skipped (backend not ready)${NC}"
    else
        echo -e "${GREEN}  ✓ No platform code changes detected — skipping auto-redeploy${NC}"
    fi

    # ─── Endpoint Verification (3 checks) ──────────────────────────────────
    echo -e "\n${BLUE}  → Running endpoint verification (3 checks)...${NC}"
    sleep 5
    PASS_COUNT=0
    FAIL_COUNT=0

    # ── Check 1: Backend API health (internal — bypasses Caddy/Nginx) ──
    EP1_URL="http://127.0.0.1:8090/health"
    echo -e "${BLUE}  [1/3] Backend API health...${NC}"
    echo -e "${BLUE}        Endpoint: $EP1_URL${NC}"
    BACKEND_OK=false
    EP1_CODE="000"
    for attempt in 1 2 3 4 5; do
        EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 -L "$EP1_URL" 2>/dev/null || echo "000")
        if [ "$EP1_CODE" = "200" ] || [ "$EP1_CODE" = "301" ]; then
            BACKEND_OK=true
            break
        fi
        if [ "$attempt" -eq 1 ]; then
            docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true
        fi
        sleep 3
    done
    if [ "$BACKEND_OK" = "true" ]; then
        EP1_RESULT="${GREEN}PASS${NC}"
        echo -e "${GREEN}  ✓ [1/3] PASS — HTTP $EP1_CODE${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        EP1_RESULT="${RED}FAIL${NC}"
        echo -e "${RED}  ✗ [1/3] FAIL — HTTP $EP1_CODE${NC}"
        echo -e "${YELLOW}        Fix: docker compose -f $COMPOSE_FILE logs --tail=30 backend${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # ── Check 2: HTTPS platform domain (auto-discovered from DB → through Caddy) ──
    echo -e "${BLUE}  [2/3] HTTPS platform domain...${NC}"
    # Auto-discover domain from PlatformConfig in DB — zero config needed
    EP_DOMAIN="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
config = PlatformConfig.load()
d = (config.domain or '').strip()
if d and d != 'localhost':
    print(d)
" 2>/dev/null | tr -d '[:space:]' || true)"
    # Fallback to .env if DB query failed
    if [ -z "$EP_DOMAIN" ]; then
        EP_DOMAIN="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    fi
    HTTPS_OK=false
    EP2_CODE="---"
    EP2_URL="(skipped)"
    if [ -n "$EP_DOMAIN" ] && [ "$EP_DOMAIN" != "localhost" ]; then
        EP2_URL="https://${EP_DOMAIN}/health"
        echo -e "${BLUE}        Endpoint: $EP2_URL${NC}"
        for attempt in 1 2 3; do
            EP2_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 8 -L "$EP2_URL" 2>/dev/null || echo "000")
            if [ "$EP2_CODE" = "200" ]; then
                HTTPS_OK=true
                break
            fi
            sleep 3
        done
        if [ "$HTTPS_OK" = "true" ]; then
            EP2_RESULT="${GREEN}PASS${NC}"
            echo -e "${GREEN}  ✓ [2/3] PASS — HTTP $EP2_CODE${NC}"
            PASS_COUNT=$((PASS_COUNT + 1))
        else
            EP2_RESULT="${RED}FAIL${NC}"
            echo -e "${RED}  ✗ [2/3] FAIL — HTTP $EP2_CODE${NC}"
            echo -e "${YELLOW}        Fix: systemctl status caddy && journalctl -u caddy --no-pager -n 15${NC}"
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    else
        EP2_RESULT="${YELLOW}SKIP${NC}"
        echo -e "${YELLOW}  ⊘ [2/3] SKIPPED (no domain configured)${NC}"
    fi

    # ── Check 3+: ALL deployed services (auto-discovered from DB) ──
    echo -e "${BLUE}  [3/N] Deployed services routing...${NC}"

    # Query ALL active service domains from the DB
    ALL_SVC_DOMAINS="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
for svc in Service.objects.filter(is_active=True).exclude(public_domain__isnull=True).exclude(public_domain='').order_by('name'):
    print(f'{svc.name}|{svc.public_domain.strip()}')
" 2>/dev/null | tr -d '\r' || true)"

    # Also check Traefik port directly
    EP3_URL="http://127.0.0.1:8081/"
    EP3_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP3_URL" 2>/dev/null || echo "000")
    if [ "$EP3_CODE" != "000" ] && [ "$EP3_CODE" != "502" ] && [ "$EP3_CODE" != "503" ]; then
        EP3_RESULT="${GREEN}PASS${NC}"
        echo -e "${GREEN}  ✓ Traefik proxy ($EP3_URL) — HTTP $EP3_CODE${NC}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        EP3_RESULT="${RED}FAIL${NC}"
        echo -e "${RED}  ✗ Traefik proxy ($EP3_URL) — HTTP $EP3_CODE${NC}"
        echo -e "${YELLOW}        Fix: docker compose -f $COMPOSE_FILE logs --tail=20 traefik${NC}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # Collect service results for the table
    SVC_RESULTS=""
    SVC_COUNT=0
    if [ -n "$ALL_SVC_DOMAINS" ]; then
        while IFS='|' read -r svc_name svc_domain; do
            [ -z "$svc_domain" ] && continue
            SVC_COUNT=$((SVC_COUNT + 1))
            svc_url="https://${svc_domain}/"
            echo -e "${BLUE}        Testing: $svc_name → $svc_url${NC}"
            svc_code="000"
            svc_ok=false
            for attempt in 1 2 3; do
                svc_code=$(curl -so /dev/null -w '%{http_code}' --max-time 8 -L "$svc_url" 2>/dev/null || echo "000")
                if [ "$svc_code" != "000" ] && [ "$svc_code" != "502" ] && [ "$svc_code" != "503" ]; then
                    svc_ok=true
                    break
                fi
                sleep 2
            done
            if [ "$svc_ok" = "true" ]; then
                svc_result="${GREEN}PASS${NC}"
                echo -e "${GREEN}  ✓ $svc_name: HTTP $svc_code${NC}"
                PASS_COUNT=$((PASS_COUNT + 1))
            else
                svc_result="${RED}FAIL${NC}"
                echo -e "${RED}  ✗ $svc_name: HTTP $svc_code${NC}"
                FAIL_COUNT=$((FAIL_COUNT + 1))
            fi
            SVC_RESULTS="${SVC_RESULTS}${svc_name}|${svc_url}|${svc_code}|${svc_result}\n"
        done <<< "$ALL_SVC_DOMAINS"
    fi
    if [ "$SVC_COUNT" -eq 0 ]; then
        echo -e "${YELLOW}        No active services deployed${NC}"
    fi

    # ── Results Table ──
    TOTAL_CHECKS=$((PASS_COUNT + FAIL_COUNT))
    echo ""
    echo -e "${BLUE}  ╔══════════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}  ║                        ENDPOINT VERIFICATION REPORT                     ║${NC}"
    echo -e "${BLUE}  ╠════════════════════════════════════════════════════════╦══════╦══════════╣${NC}"
    echo -e "${BLUE}  ║  Endpoint                                            ║ HTTP ║  Result  ║${NC}"
    echo -e "${BLUE}  ╠════════════════════════════════════════════════════════╬══════╬══════════╣${NC}"
    printf "  ║  %-52.52s ║ %-4s ║ " "Backend: $EP1_URL" "$EP1_CODE"
    echo -e " $EP1_RESULT  ║"
    printf "  ║  %-52.52s ║ %-4s ║ " "HTTPS: $EP2_URL" "$EP2_CODE"
    echo -e " $EP2_RESULT  ║"
    printf "  ║  %-52.52s ║ %-4s ║ " "Traefik: $EP3_URL" "$EP3_CODE"
    echo -e " $EP3_RESULT  ║"
    # Print each deployed service row
    if [ -n "$SVC_RESULTS" ]; then
        echo -e "${BLUE}  ╠════════════════════════════════════════════════════════╬══════╬══════════╣${NC}"
        while IFS='|' read -r s_name s_url s_code s_result; do
            [ -z "$s_name" ] && continue
            printf "  ║  %-52.52s ║ %-4s ║ " "$s_name" "$s_code"
            echo -e " $s_result  ║"
        done <<< "$(echo -e "$SVC_RESULTS")"
    fi
    echo -e "${BLUE}  ╚════════════════════════════════════════════════════════╩══════╩══════════╝${NC}"

    # ── Summary ──
    if [ "$FAIL_COUNT" -eq 0 ]; then
        echo -e "\n${GREEN}  ✓ All $PASS_COUNT/$TOTAL_CHECKS endpoint checks passed${NC}"
    else
        echo -e "\n${YELLOW}  ⚠ $PASS_COUNT passed, $FAIL_COUNT failed out of $TOTAL_CHECKS checks${NC}"
    fi

    # Show container status
    echo -e "\n${BLUE}Container Status:${NC}"
    docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
        docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true

    # ─── Update autoscaler service (picks up code changes + new token) ────────
    if [ -f "$INSTALL_DIR/smsly-autoscaler.py" ]; then
        echo -e "${BLUE}  → Updating smsly-autoscaler service...${NC}"
        mkdir -p /opt/smsly
        cp "$INSTALL_DIR/smsly-autoscaler.py" /opt/smsly/autoscaler.py
        chmod +x /opt/smsly/autoscaler.py

        AUTOSCALER_API_TOKEN="$(env_get_value "$INSTALL_DIR/.env" "AUTOSCALER_API_TOKEN")"
        if [ -n "$AUTOSCALER_API_TOKEN" ] && [ -f /etc/systemd/system/smsly-autoscaler.service ]; then
            # Update token in existing service file
            sed -i "s|^Environment=AUTOSCALER_API_TOKEN=.*|Environment=AUTOSCALER_API_TOKEN=${AUTOSCALER_API_TOKEN}|" \
                /etc/systemd/system/smsly-autoscaler.service
            systemctl daemon-reload
        fi
        systemctl restart smsly-autoscaler 2>/dev/null || true
        echo -e "${GREEN}  ✓ Autoscaler updated${NC}"
    fi

    # ─── Re-apply OOM protection (scores reset when containers restart) ──────
    echo -e "${BLUE}  → Re-applying OOM protection for critical containers...${NC}"
    for CONTAINER in smsly-hosting-nginx-1 smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgbouncer-1; do
        CPID=$(docker inspect --format '{{.State.Pid}}' "$CONTAINER" 2>/dev/null || echo "")
        if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
            echo -500 > "/proc/$CPID/oom_score_adj" 2>/dev/null || true
        fi
    done
    echo -e "${GREEN}  ✓ OOM protection set (nginx, backend, db, pgbouncer)${NC}"

    trap - EXIT
    echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}   ✓ UPDATE SUCCESSFUL ($UPDATE_MODE)${NC}"
    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  Debug snapshot:    sudo bash install.sh --debug${NC}"
    echo -e "${YELLOW}  Runtime recovery:  sudo bash install.sh --recover${NC}"
    exit 0
fi

# =============================================================================
# FRESH INSTALL — Full setup from scratch
# =============================================================================

# -----------------------------------------------------------------------------
# 1. Pre-flight Checks
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[1/9] Checking system requirements...${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}✗ Please run as root (sudo bash install.sh)${NC}"
    exit 1
fi

# Check OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo -e "${BLUE}  Detected: $NAME $VERSION_ID${NC}"
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
        echo -e "${YELLOW}⚠ Warning: This script is optimized for Ubuntu/Debian.${NC}"
        if [ -e /dev/tty ]; then
             echo -e "${YELLOW}  Press ENTER to continue anyway, or Ctrl+C to abort.${NC}"
             read -r < /dev/tty
        else
             echo -e "${YELLOW}  ⚠ Non-interactive mode: Continuing automatically...${NC}"
        fi
    fi
fi

# ─── Disk space check (prevents mid-build OOM / no-space failures) ──────────
DISK_AVAIL_MB=$(df -BM / | tail -1 | awk '{print $4}' | tr -d 'M')
echo -e "${BLUE}  Disk space available: ${DISK_AVAIL_MB}MB${NC}"
if [ "$DISK_AVAIL_MB" -lt 3000 ]; then
    echo -e "${YELLOW}  ⚠ Low disk space (${DISK_AVAIL_MB}MB). Recommended: 3GB+${NC}"
    echo -e "${YELLOW}    Attempting Docker cache cleanup...${NC}"
    docker system prune -f 2>/dev/null || true
    docker builder prune -f 2>/dev/null || true
    DISK_AVAIL_MB=$(df -BM / | tail -1 | awk '{print $4}' | tr -d 'M')
    if [ "$DISK_AVAIL_MB" -lt 1500 ]; then
        echo -e "${RED}  ✗ Insufficient disk space (${DISK_AVAIL_MB}MB). Need at least 1.5GB for fresh install.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ After cleanup: ${DISK_AVAIL_MB}MB available${NC}"
fi
echo -e "${GREEN}  ✓ Pre-flight checks passed${NC}"

# -----------------------------------------------------------------------------
# 2. Dependency Management & cleanup
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[2/9] Installing dependencies...${NC}"

# Stop conflicting services if present (anything that holds port 80/443)
# NOTE: Don't stop Caddy here — we install/configure it in step 7.
# Stopping it on re-installs breaks the reverse proxy unnecessarily.
for svc in nginx apache2; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo -e "${YELLOW}  ⚠ Stopping conflicting service: $svc${NC}"
        systemctl stop "$svc" || true
        systemctl disable "$svc" || true
    fi
done

# ─── NUCLEAR CLEANUP: Remove ALL stale SMSLY containers, volumes, networks ──
# This prevents: port conflicts, stale DB password volumes, orphan containers
echo -e "${BLUE}  → Cleaning up previous SMSLY installation artifacts...${NC}"

# Stop and remove stale smsly-hosting platform containers (NOT user-deployed services)
SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly-hosting-" -q 2>/dev/null || true)
if [ -n "$SMSLY_CONTAINERS" ]; then
    echo -e "${YELLOW}  → Stopping smsly-hosting platform container(s)...${NC}"
    docker stop $SMSLY_CONTAINERS 2>/dev/null || true
    docker rm -f $SMSLY_CONTAINERS 2>/dev/null || true
fi

# Remove stale Docker volumes (postgres data with old passwords, etc.)
SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly" -q 2>/dev/null || true)
if [ -n "$SMSLY_VOLUMES" ]; then
    if [ "${SMSLY_ALLOW_DESTRUCTIVE_FRESH:-0}" = "1" ]; then
        echo -e "${YELLOW}  → Removing stale SMSLY volumes (SMSLY_ALLOW_DESTRUCTIVE_FRESH=1)...${NC}"
        for vol in $SMSLY_VOLUMES; do
            docker volume rm "$vol" 2>/dev/null || true
        done
    else
        echo -e "${YELLOW}  ⚠ Existing SMSLY volumes detected; preserving data by default.${NC}"
        echo -e "${YELLOW}    Use --wipe for full reset, or set SMSLY_ALLOW_DESTRUCTIVE_FRESH=1 to delete volumes in fresh install.${NC}"
    fi
fi

# Remove stale Docker networks
SMSLY_NETWORKS=$(docker network ls --filter "name=smsly" -q 2>/dev/null || true)
if [ -n "$SMSLY_NETWORKS" ]; then
    for net in $SMSLY_NETWORKS; do
        docker network rm "$net" 2>/dev/null || true
    done
fi

echo -e "${GREEN}  ✓ Previous artifacts cleaned${NC}"

apt-get update -qq
apt-get install -y curl wget git python3 python3-pip python3-venv openssl ca-certificates gnupg lsb-release dnsutils

# Install Docker if missing
if ! command -v docker &> /dev/null; then
    echo -e "${BLUE}  → Installing Docker...${NC}"
    mkdir -m 0755 -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
    echo -e "${GREEN}  ✓ Docker already installed ($(docker --version | head -c 40))${NC}"
fi

# Ensure docker compose is available
if ! docker compose version >/dev/null 2>&1; then
    echo -e "${BLUE}  → Installing Docker Compose plugin...${NC}"
    apt-get install -y docker-compose-plugin || true
fi
echo -e "${GREEN}  ✓ Dependencies installed${NC}"

# -----------------------------------------------------------------------------
# 3. Configuration & Secrets (IDEMPOTENT)
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[3/9] Configuration...${NC}"

mkdir -p "$INSTALL_DIR"

# Ensure we are in the install directory with correct files
if [ "$(pwd)" != "$INSTALL_DIR" ]; then
    echo -e "${BLUE}  → Setting up installation in $INSTALL_DIR${NC}"
    if [ -f "docker-compose.prod.yml" ]; then
        if [ "${SMSLY_FORCE_SOURCE_SYNC:-0}" = "1" ]; then
            cp -rf . "$INSTALL_DIR/"
        else
            cp -rn . "$INSTALL_DIR/" 2>/dev/null || cp -r . "$INSTALL_DIR/"
        fi
    else
        if [ -d "$INSTALL_DIR/.git" ]; then
             cd "$INSTALL_DIR"
             # Force-reset to match remote (handles chmod changes, local edits, etc.)
             git fetch origin main
             git reset --hard origin/main
        else
             git clone https://github.com/SMSLYCLOUD/smsly-hosting.git "$INSTALL_DIR"
        fi
    fi
fi
cd "$INSTALL_DIR"

# ─── BLINDSPOT FIX: Validate required deployment files ──────────────────────
echo -e "${BLUE}  → Validating deployment files...${NC}"
MISSING_FILES=()
for required_file in "$COMPOSE_FILE" "nginx.conf" "backend/Dockerfile" "frontend/Dockerfile" "backend/entrypoint.sh"; do
    if [ ! -f "$required_file" ]; then
        MISSING_FILES+=("$required_file")
    fi
done

if [ ${#MISSING_FILES[@]} -gt 0 ]; then
    echo -e "${RED}✗ Missing required files:${NC}"
    for f in "${MISSING_FILES[@]}"; do
        echo -e "${RED}    - $f${NC}"
    done
    exit 1
fi
echo -e "${GREEN}  ✓ All required deployment files present${NC}"

# ─── BLINDSPOT FIX: Ensure correct compose file is used ─────────────────────
# Check if any containers are running with the wrong compose file (dev instead of prod)
if docker compose ps --format "table {{.Name}}" 2>/dev/null | grep -q "smsly-hosting"; then
    echo -e "${YELLOW}  ⚠ Found containers running from docker-compose.yml (dev). Stopping...${NC}"
    docker compose down 2>/dev/null || true
fi

# ─── IDEMPOTENCY: Skip secret generation if .env already exists ─────────────
if [ -f "$INSTALL_DIR/.env" ]; then
    echo -e "${GREEN}  ✓ Existing .env found — preserving configuration${NC}"
    echo -e "${BLUE}  → Backing up existing .env to .env.backup${NC}"
    cp "$INSTALL_DIR/.env" "$INSTALL_DIR/.env.backup"

    # Backfill newer required keys and validate before deployment.
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x Existing .env is invalid. Fix it or restore .env.backup and rerun.${NC}"
        exit 1
    fi

    # Source existing values for summary output.
    source "$INSTALL_DIR/.env" 2>/dev/null || true
    DOMAIN="${DOMAIN:-localhost}"
    USE_SSL="${USE_SSL:-false}"
    PUBLIC_IP="$(detect_public_ip)"


else
    # ─── Fresh install: generate secrets ────────────────────────────────────
    # Force IPv4 to ensure valid URL syntax (avoiding [IPv6] bracket issues)
    PUBLIC_IP="$(detect_public_ip)"

    # Allow non-interactive SSL installs by pre-seeding env vars:
    #   USE_SSL=true DOMAIN=cloud.smsly.cloud ACME_EMAIL=admin@example.com SKIP_SCREEN=1 bash install.sh
    PRESET_DOMAIN="${DOMAIN:-}"
    PRESET_ACME_EMAIL="${ACME_EMAIL:-}"
    PRESET_USE_SSL="${USE_SSL:-}"

    echo -e "\n${BLUE}Select Deployment Mode:${NC}"
    echo -e "  1) ${GREEN}IP Mode${NC} (Easy) - http://$PUBLIC_IP:8090"
    echo -e "  2) ${GREEN}SSL Mode${NC} (Prod) - https://your-domain.com (Requires DNS A Record pointing to $PUBLIC_IP)"

    # If env vars are pre-seeded, skip prompting even in interactive shells.
    if [ "${PRESET_USE_SSL}" = "true" ] && [ -n "${PRESET_DOMAIN}" ] && [ -n "${PRESET_ACME_EMAIL}" ]; then
        echo -e "${BLUE}  → Preset detected. Using SSL Mode for ${PRESET_DOMAIN}.${NC}"
        MODE_CHOICE=2
    elif [ -e /dev/tty ]; then
        read -p "Enter choice [1]: " MODE_CHOICE < /dev/tty
        MODE_CHOICE=${MODE_CHOICE:-1}
    else
        echo -e "${YELLOW}  ⚠ Non-interactive mode detected. Defaulting to IP Mode.${NC}"
        MODE_CHOICE=1
    fi

    DOMAIN=""
    ACME_EMAIL=""
    USE_SSL="false"

    if [ "$MODE_CHOICE" -eq "2" ]; then
        USE_SSL="true"
        if [ ! -e /dev/tty ] && [ -n "${PRESET_DOMAIN}" ] && [ -n "${PRESET_ACME_EMAIL}" ]; then
            DOMAIN="${PRESET_DOMAIN}"
            ACME_EMAIL="${PRESET_ACME_EMAIL}"
        else
            while [ -z "$DOMAIN" ]; do
                read -p "Enter your Domain (e.g., app.example.com): " DOMAIN < /dev/tty
            done

            while [ -z "$ACME_EMAIL" ]; do
                read -p "Enter Email for SSL (e.g., admin@example.com): " ACME_EMAIL < /dev/tty
            done
        fi

        echo -e "${BLUE}  → Verifying DNS for $DOMAIN...${NC}"
        if command -v host &> /dev/null; then
            DETECTED_IP=$(host -t A "$DOMAIN" 2>/dev/null | awk '{print $NF}' | tail -n 1)
            if [[ "$DETECTED_IP" != "$PUBLIC_IP" && "$DETECTED_IP" != "127.0.0.1" ]]; then
                echo -e "${YELLOW}  ⚠ WARNING: DNS for $DOMAIN ($DETECTED_IP) does not match this server ($PUBLIC_IP).${NC}"
                echo -e "${YELLOW}  SSL generation may fail. Ensure your DNS A record is set.${NC}"
                if [ -e /dev/tty ]; then
                    read -p "  Continue anyway? (y/n) " -n 1 -r < /dev/tty
                    echo
                    if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 1; fi
                else
                    echo -e "${RED}x Non-interactive install cannot prompt for DNS mismatch. Aborting.${NC}"
                    exit 1
                fi
            else
                echo -e "${GREEN}  ✓ DNS looks correct.${NC}"
            fi
        fi
    else
        DOMAIN="$PUBLIC_IP"
        echo -e "${BLUE}  → Using IP Mode: $PUBLIC_IP${NC}"
    fi

    # ─── Wildcard Subdomain & Cloudflare Setup (SSL mode only) ────────────
    WILDCARD_SUBDOMAINS="false"
    CLOUDFLARE_API_TOKEN=""
    if [ "$USE_SSL" = "true" ] && [ -n "$DOMAIN" ] && [ "$DOMAIN" != "$PUBLIC_IP" ]; then
        echo ""
        echo -e "${BLUE}  Wildcard subdomains allow deployed services to get automatic SSL.${NC}"
        echo -e "  e.g., myapp-abc123.${DOMAIN} will automatically have HTTPS."
        echo -e "  This requires a Cloudflare API Token with DNS:Edit permission.\n"

        PRESET_WILDCARD="${WILDCARD_SUBDOMAINS:-}"
        PRESET_CF_TOKEN="${CLOUDFLARE_API_TOKEN:-}"

        if [ -n "$PRESET_WILDCARD" ] && [ -n "$PRESET_CF_TOKEN" ]; then
            WILDCARD_SUBDOMAINS="$PRESET_WILDCARD"
            CLOUDFLARE_API_TOKEN="$PRESET_CF_TOKEN"
            echo -e "${BLUE}  → Preset detected: wildcard=$WILDCARD_SUBDOMAINS${NC}"
        elif [ -e /dev/tty ]; then
            read -p "  Enable wildcard subdomains? (y/n) [n]: " WILDCARD_CHOICE < /dev/tty
            WILDCARD_CHOICE=${WILDCARD_CHOICE:-n}
            if [[ $WILDCARD_CHOICE =~ ^[Yy]$ ]]; then
                WILDCARD_SUBDOMAINS="true"
                while [ -z "$CLOUDFLARE_API_TOKEN" ]; do
                    read -sp "  Enter Cloudflare API Token (DNS:Edit): " CLOUDFLARE_API_TOKEN < /dev/tty
                    echo
                done
                echo -e "${GREEN}  ✓ Wildcard subdomains enabled.${NC}"
            fi
        fi
    fi

    # ─── Generate Secrets (Python-only, NO invalid fallback) ────────────────
    echo -e "${BLUE}  → Generating secure credentials...${NC}"

    # Install cryptography lib (--break-system-packages for Python 3.12+ on Ubuntu 24.04)
    pip3 install cryptography -q --break-system-packages 2>/dev/null || \
        pip3 install cryptography -q 2>/dev/null || true

    # Generate secrets — Python is the ONLY source of truth for Fernet keys
    SECRETS_GENERATED=false
    if python3 -c "
import secrets, string
from cryptography.fernet import Fernet

chars = string.ascii_letters + string.digits
secret_key = ''.join(secrets.choice(chars) for _ in range(50))
fernet_key = Fernet.generate_key().decode()
pg_pass = secrets.token_hex(16)
redis_pass = secrets.token_hex(16)
gateway_secret = secrets.token_hex(32)
webhook_secret = secrets.token_hex(32)
autoscaler_token = secrets.token_hex(32)

# Validate the Fernet key before outputting
Fernet(fernet_key.encode())

print(f'SECRET_KEY={secret_key}')
print(f'FIELD_ENCRYPTION_KEY={fernet_key}')
print(f'POSTGRES_PASSWORD={pg_pass}')
print(f'REDIS_PASSWORD={redis_pass}')
print(f'GATEWAY_SECRET={gateway_secret}')
print(f'GITHUB_WEBHOOK_SECRET={webhook_secret}')
print(f'AUTOSCALER_API_TOKEN={autoscaler_token}')
" > "$INSTALL_DIR/.secrets.tmp" 2>/dev/null; then
        source "$INSTALL_DIR/.secrets.tmp"
        rm -f "$INSTALL_DIR/.secrets.tmp"
        SECRETS_GENERATED=true
        echo -e "${GREEN}  ✓ Secrets generated (Fernet key validated)${NC}"
    fi

    if [ "$SECRETS_GENERATED" != "true" ]; then
        echo -e "${RED}  ✗ CRITICAL: Cannot generate valid Fernet encryption key.${NC}"
        echo -e "${RED}    Install Python 3 and the 'cryptography' package, then re-run.${NC}"
        echo -e "${RED}    pip3 install cryptography${NC}"
        exit 1
    fi

    # Create .env
    cat <<EOF > "$INSTALL_DIR/.env"
# SMSLY Hosting Configuration — Generated $(date -Iseconds)
ENVIRONMENT=production
DEBUG=False
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$FIELD_ENCRYPTION_KEY
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_USER=smsly_admin
POSTGRES_DB=smsly_hosting
DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@pgbouncer:5432/smsly_hosting

REDIS_PASSWORD=$REDIS_PASSWORD
REDIS_URL=redis://:$REDIS_PASSWORD@redis:6379/0
CELERY_BROKER_URL=redis://:$REDIS_PASSWORD@redis:6379/0

DOMAIN=$DOMAIN
ACME_EMAIL=${ACME_EMAIL:-}
USE_SSL=$USE_SSL

# Inter-service HMAC authentication secret
GATEWAY_SECRET=$GATEWAY_SECRET

# GitHub webhook signature verification
GITHUB_WEBHOOK_SECRET=$GITHUB_WEBHOOK_SECRET

# Security
ALLOWED_HOSTS=$DOMAIN,$PUBLIC_IP,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://$PUBLIC_IP:8090,https://$DOMAIN,http://$DOMAIN,http://localhost:8090,http://$PUBLIC_IP
CORS_ALLOWED_ORIGINS=http://$PUBLIC_IP:8090,https://$DOMAIN,http://$DOMAIN,http://$PUBLIC_IP

# Docker networking
# Ensure addon containers and deployed app containers share the same network for connectivity.
DOCKER_NETWORK=smsly-net

# Wildcard subdomain SSL (Cloudflare DNS challenge)
WILDCARD_SUBDOMAINS=$WILDCARD_SUBDOMAINS
CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN:-}
CADDY_CONFIG_DIR=/caddy-config
PUBLIC_IP=$PUBLIC_IP

# Autoscaler API authentication (shared with smsly-autoscaler.service)
AUTOSCALER_API_TOKEN=$AUTOSCALER_API_TOKEN
EOF

    chmod 600 "$INSTALL_DIR/.env"
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}  x Generated .env failed validation. Aborting install.${NC}"
        exit 1
    fi

    echo -e "${GREEN}  ✓ Configuration saved to .env (chmod 600)${NC}"
fi

# -----------------------------------------------------------------------------
# 4. Deployment
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[4/9] Deploying Container Stack...${NC}"

# Ensure networks exist
docker network create smsly-net 2>/dev/null || true
docker network create smsly-proxy 2>/dev/null || true

# ─── BLINDSPOT FIX: Ensure entrypoint.sh has execute permissions ────────────
# Windows git can strip +x bits. Fix before building.
#
# NOTE: backend/Dockerfile already runs `chmod +x entrypoint.sh` inside the image.
# Avoid mutating the git working tree on the host (file mode flips can block `git pull`).
#

# Both IP and SSL modes use the same compose stack.
# Caddy (step 7) handles public-facing HTTP/HTTPS termination.
# Traefik is NOT used — Caddy natively handles Let's Encrypt SSL.
# Ensure bind-mounted config paths exist before `docker compose up`.
mkdir -p "$INSTALL_DIR/caddy-config"
chmod 777 "$INSTALL_DIR/caddy-config"
echo -e "${BLUE}  → Starting App Stack...${NC}"
docker compose -f "$COMPOSE_FILE" up -d --build --force-recreate --remove-orphans

# -----------------------------------------------------------------------------
# 5. Database Setup
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[5/9] Initializing Database...${NC}"

echo -e "${BLUE}  → Waiting for Database...${NC}"
DB_READY=false
for i in $(seq 1 24); do
    if docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U smsly_admin >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ Database is ready (attempt $i).${NC}"
        DB_READY=true
        break
    fi
    printf "."
    sleep 5
done
echo ""

if [ "$DB_READY" != "true" ]; then
    echo -e "${RED}  ✗ Database failed to become ready after 2 minutes.${NC}"
    echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs db${NC}"
    exit 1
fi

# ─── Sync DB password to match .env (handles volume from previous install) ──
# The DB volume persists with the password from FIRST init.
# Always reset the password inside PostgreSQL to match the current .env.
source "$INSTALL_DIR/.env" 2>/dev/null || true
echo -e "${BLUE}  → Syncing database password...${NC}"

# Try local trust auth first (Docker default), then try with PGPASSWORD
if docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U postgres -c "ALTER USER smsly_admin WITH PASSWORD '${POSTGRES_PASSWORD}';" \
    >/dev/null 2>&1; then
    echo -e "${GREEN}  ✓ Database password synced${NC}"
elif docker compose -f "$COMPOSE_FILE" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
    psql -U smsly_admin -d smsly_hosting -c "SELECT 1;" >/dev/null 2>&1; then
    echo -e "${GREEN}  ✓ Database password already matches${NC}"
else
    echo -e "${YELLOW}  ⚠ Password mismatch — resetting via postgres superuser...${NC}"
    # Last resort: the Docker postgres container always accepts local postgres user
    docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U postgres -c "ALTER USER smsly_admin WITH PASSWORD '${POSTGRES_PASSWORD}';" \
        2>&1 || echo -e "${RED}  ✗ Could not sync password. Check pg_hba.conf${NC}"
fi

# ─── Restart backend so it picks up the correct DB credentials ──────────────
echo -e "${BLUE}  → Restarting backend with synced credentials...${NC}"
docker compose -f "$COMPOSE_FILE" restart backend >/dev/null 2>&1
sleep 5

echo -e "${BLUE}  → Running Migrations...${NC}"
# Generate any pending migration files first (handles uncommitted model changes)
docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py makemigrations --noinput 2>/dev/null || true
MIGRATE_OK=false
for attempt in 1 2 3; do
    if docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py migrate --noinput 2>&1; then
        MIGRATE_OK=true
        break
    fi
    WAIT=$((attempt * 10))
    echo -e "${YELLOW}  ⚠ Migration attempt $attempt/3 failed — retrying in ${WAIT}s...${NC}"
    docker compose -f "$COMPOSE_FILE" restart backend >/dev/null 2>&1
    sleep "$WAIT"
done

if [ "$MIGRATE_OK" != "true" ]; then
    echo -e "${RED}  ✗ Migrations failed after 3 attempts.${NC}"
    echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs backend${NC}"
    exit 1
fi

echo -e "${BLUE}  → Collecting Static Files...${NC}"
# Fix volume ownership — Docker creates named volumes as root
docker compose -f "$COMPOSE_FILE" exec -T --user root backend chown -R 1000:1000 /app/staticfiles /app/media 2>/dev/null || true
docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py collectstatic --noinput

# -----------------------------------------------------------------------------
# 6. Admin User (IDEMPOTENT — skips if admin already exists)
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[6/9] Creating Admin User...${NC}"
ADMIN_EXISTS=$(echo "from django.contrib.auth import get_user_model; User = get_user_model(); print('1' if User.objects.filter(username='admin').exists() else '0')" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1)

if [ "${ADMIN_EXISTS:-0}" = "1" ]; then
    echo -e "${GREEN}  ✓ Admin user already exists — skipping${NC}"
    if [ -f "$CREDENTIALS_FILE" ]; then
        echo -e "${GREEN}  ✓ Credentials file exists — leaving unchanged${NC}"
    else
        # Best effort: don't overwrite an unknown existing password.
        cat > "$CREDENTIALS_FILE" <<CREDS
# SMSLY Hosting Admin Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE
Username: admin
Password: <existing — not changed by installer>
CREDS
        chmod 600 "$CREDENTIALS_FILE"
    fi
else
    # Production hardening: never ship with a default admin password.
    # Use a shell-safe hex password (avoids quoting issues in manage.py shell).
    ADMIN_PASS="$(gen_hex_secret 16)"
    echo "from django.contrib.auth import get_user_model; User = get_user_model(); User.objects.create_superuser('admin', 'admin@smsly.cloud', '$ADMIN_PASS'); print('CREATED')" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1 >/dev/null
    echo -e "${GREEN}  ✓ Admin user created${NC}"

    # ─── Save credentials to secure file (NOT echoed to terminal) ───────────────
    cat > "$CREDENTIALS_FILE" <<CREDS
# SMSLY Hosting Admin Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE
Username: admin
Password: $ADMIN_PASS
CREDS
    chmod 600 "$CREDENTIALS_FILE"
fi

# -----------------------------------------------------------------------------
# 6b. Ensure Local Cloud Provider exists (required for deployments)
# -----------------------------------------------------------------------------
echo -e "${BLUE}  → Ensuring Local Docker cloud provider exists...${NC}"
echo "
from apps.cloud.models import CloudProvider
cp, created = CloudProvider.objects.get_or_create(
    provider_type='LOCAL',
    defaults={'name': 'Local Docker', 'is_active': True}
)
if not created and not cp.is_active:
    cp.is_active = True
    cp.save()
print('CREATED' if created else 'EXISTS')
" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1 >/dev/null
echo -e "${GREEN}  ✓ Local Docker cloud provider ready${NC}"

# -----------------------------------------------------------------------------
# 7. Caddy Reverse Proxy (Public Access)
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[7/9] Setting up Caddy Reverse Proxy...${NC}"

# ─── Build Caddy with Cloudflare DNS plugin ───────────────────────────────────
# Always build custom Caddy with Cloudflare DNS support, even in IP mode.
# This ensures users can enable SSL + wildcard from the web UI later without SSH.
if caddy list-modules 2>/dev/null | grep -q 'dns.providers.cloudflare'; then
    echo -e "${GREEN}  ✓ Caddy already has cloudflare DNS module${NC}"
elif command -v caddy &> /dev/null; then
    echo -e "${BLUE}  → Caddy found but missing Cloudflare DNS plugin — rebuilding...${NC}"
    _BUILD_CADDY=true
else
    echo -e "${BLUE}  → Installing Caddy with Cloudflare DNS plugin...${NC}"
    _BUILD_CADDY=true
fi

if [ "${_BUILD_CADDY:-}" = "true" ]; then
    if ! command -v xcaddy &> /dev/null; then
        # xcaddy needs Go 1.21+. Ubuntu apt repos ship Go 1.18 which is
        # too old (go.mod 'toolchain' directive is unsupported). Use snap
        # or direct binary download to get a compatible version.
        _GO_OK=false
        if command -v go &> /dev/null; then
            _GO_VER=$(go version | grep -oP 'go1\.(\d+)' | grep -oP '\d+$')
            [ "${_GO_VER:-0}" -ge 21 ] && _GO_OK=true
        fi
        if [ "$_GO_OK" != "true" ]; then
            echo -e "${BLUE}  → Installing Go 1.22 (xcaddy requires Go 1.21+)...${NC}"
            GO_TAR="go1.22.10.linux-amd64.tar.gz"
            curl -fsSL "https://go.dev/dl/$GO_TAR" -o "/tmp/$GO_TAR"
            rm -rf /usr/local/go
            tar -C /usr/local -xzf "/tmp/$GO_TAR"
            rm -f "/tmp/$GO_TAR"
            export PATH="/usr/local/go/bin:$PATH"
            echo -e "${GREEN}  ✓ Go $(go version | awk '{print $3}') installed${NC}"
        fi
        export GOPATH="${GOPATH:-/root/go}"
        export PATH="$PATH:$GOPATH/bin"
        go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
    fi

    # Build custom Caddy with Cloudflare DNS
    CADDY_TMP=$(mktemp -d)
    cd "$CADDY_TMP"
    if xcaddy build --with github.com/caddy-dns/cloudflare 2>&1 | tail -5; then
        # Replace system Caddy
        systemctl stop caddy 2>/dev/null || true
        mv ./caddy /usr/bin/caddy
        chmod +x /usr/bin/caddy
        echo -e "${GREEN}  ✓ Custom Caddy built with Cloudflare DNS plugin${NC}"
    else
        echo -e "${YELLOW}  ⚠ Custom Caddy build failed — trying pre-built download...${NC}"
        # Fallback 1: Download pre-built Caddy with Cloudflare DNS from Caddy's download API
        if curl -fsSL -o /usr/bin/caddy \
            "https://caddyserver.com/api/download?os=linux&arch=amd64&p=github.com/caddy-dns/cloudflare" 2>/dev/null; then
            chmod +x /usr/bin/caddy
            echo -e "${GREEN}  ✓ Pre-built Caddy with Cloudflare DNS downloaded${NC}"
        elif ! command -v caddy &> /dev/null; then
            # Fallback 2: Install stock Caddy from apt (no wildcard SSL, but basic HTTPS works)
            echo -e "${YELLOW}  ⚠ Download also failed — installing stock Caddy (no wildcard SSL)...${NC}"
            apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null 2>&1
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
            apt-get update >/dev/null 2>&1
            apt-get install -y caddy >/dev/null 2>&1
        fi
    fi
    cd "$INSTALL_DIR"
    rm -rf "$CADDY_TMP"
fi

# ─── Configure Caddyfile ──────────────────────────────────────────────────────
echo -e "${BLUE}  → Configuring Caddyfile...${NC}"
mkdir -p /var/log/caddy
touch /var/log/caddy/access.log
if id caddy >/dev/null 2>&1; then
    chown -R caddy:caddy /var/log/caddy
fi
chmod 755 /var/log/caddy
chmod 640 /var/log/caddy/access.log

CADDY_OVERRIDE_DIR="/etc/systemd/system/caddy.service.d"
CADDY_OVERRIDE_FILE="$CADDY_OVERRIDE_DIR/override.conf"

if [ "$USE_SSL" = "true" ] && [ -n "$DOMAIN" ] && [ "$DOMAIN" != "$PUBLIC_IP" ]; then
    # Ensure token is sourced from .env if present (idempotent runs)
    if [ -z "$CLOUDFLARE_API_TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
        CLOUDFLARE_API_TOKEN="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    fi

    if [ "$WILDCARD_SUBDOMAINS" = "true" ] && [ -n "$CLOUDFLARE_API_TOKEN" ]; then
        # ─── Full wildcard mode: domain + *.domain with Cloudflare DNS ────
        cat > /etc/caddy/Caddyfile <<CADDYEOF
# CloudNeuron Reverse Proxy — Auto-generated
# Domain: $DOMAIN → HTTPS (auto Let's Encrypt)
# Wildcard: *.$DOMAIN → HTTPS (Cloudflare DNS challenge)

$DOMAIN {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

*.$DOMAIN {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
    reverse_proxy localhost:8081
}

:80 {
    reverse_proxy localhost:8090
}
CADDYEOF

        # Set Cloudflare token in systemd environment
        mkdir -p "$CADDY_OVERRIDE_DIR"
        cat > "$CADDY_OVERRIDE_FILE" <<ENVEOF
[Service]
ExecStart=
ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile
Environment="CLOUDFLARE_API_TOKEN=$CLOUDFLARE_API_TOKEN"
ENVEOF
        chmod 600 "$CADDY_OVERRIDE_FILE"
        systemctl daemon-reload

        echo -e "${GREEN}  ✓ Caddy configured: HTTPS ($DOMAIN) + Wildcard (*.$DOMAIN) + HTTP fallback → 8090${NC}"
    else
        # ─── Standard SSL (no wildcard) ──────────────────────────────────
        cat > /etc/caddy/Caddyfile <<CADDYEOF
# CloudNeuron Reverse Proxy — Auto-generated
# Domain: $DOMAIN → HTTPS (auto Let's Encrypt)

$DOMAIN {
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

:80 {
    reverse_proxy localhost:8090
}
CADDYEOF
        if [ -f "$CADDY_OVERRIDE_FILE" ]; then
            rm -f "$CADDY_OVERRIDE_FILE"
            rmdir "$CADDY_OVERRIDE_DIR" 2>/dev/null || true
            systemctl daemon-reload
        fi
        echo -e "${GREEN}  ✓ Caddy configured: HTTPS ($DOMAIN) + HTTP (:80 fallback) → 8090${NC}"
    fi
else
    cat > /etc/caddy/Caddyfile <<CADDYEOF
# CloudNeuron Reverse Proxy — Auto-generated
:80 {
    reverse_proxy localhost:8090
}
CADDYEOF
    if [ -f "$CADDY_OVERRIDE_FILE" ]; then
        rm -f "$CADDY_OVERRIDE_FILE"
        rmdir "$CADDY_OVERRIDE_DIR" 2>/dev/null || true
        systemctl daemon-reload
    fi
    echo -e "${GREEN}  ✓ Caddy configured for HTTP: :80 → 8090${NC}"
fi

# ─── Create caddy-config volume directory for Settings UI writes ──────────────
mkdir -p /opt/smsly-hosting/caddy-config
chmod 750 /opt/smsly-hosting/caddy-config

# ─── Install caddy-watcher service (picks up UI-driven Caddyfile changes) ─────
if [ -f "$INSTALL_DIR/scripts/caddy-reload.sh" ]; then
    chmod +x "$INSTALL_DIR/scripts/caddy-reload.sh"
    cat > /etc/systemd/system/caddy-watcher.service <<WATCHEREOF
[Unit]
Description=Caddy Config Watcher (SMSLY)
After=caddy.service

[Service]
Type=simple
ExecStart=$INSTALL_DIR/scripts/caddy-reload.sh /opt/smsly-hosting/caddy-config
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
WATCHEREOF
    systemctl daemon-reload
    systemctl enable caddy-watcher >/dev/null 2>&1
    systemctl restart caddy-watcher
    echo -e "${GREEN}  ✓ Caddy watcher service installed and running${NC}"
fi

# Kill non-Caddy/non-Docker processes holding port 80/443 before Caddy binds
for port in 80 443; do
    PID=$(lsof -ti :$port 2>/dev/null || ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
    if [ -n "$PID" ] && [ "$PID" != "0" ]; then
        PNAME=$(ps -p "$PID" -o comm= 2>/dev/null || echo "unknown")
        # Don't kill Caddy or Docker processes
        if [[ "$PNAME" != "caddy" ]] && [[ "$PNAME" != "docker"* ]]; then
            echo -e "${YELLOW}  → Killing $PNAME (PID: $PID) holding port $port${NC}"
            kill -9 $PID 2>/dev/null || true
            sleep 1
        fi
    fi
done

systemctl restart caddy
systemctl enable caddy >/dev/null 2>&1

# Verify Caddy is running
sleep 2
if systemctl is-active --quiet caddy; then
    echo -e "${GREEN}  ✓ Caddy reverse proxy active${NC}"
else
    echo -e "${RED}  ✗ Caddy failed to start. Check: journalctl -u caddy --no-pager -n 20${NC}"
    exit 1
fi

# -----------------------------------------------------------------------------
# 8. System Memory Hardening (Prevents OOM kills)
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[8/9] Hardening System Memory...${NC}"

# ─── Swap: Ensure at least 2GB total swap ────────────────────────────────────
CURRENT_SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')
if [ "$CURRENT_SWAP_MB" -lt 2000 ]; then
    NEEDED_MB=$((2048 - CURRENT_SWAP_MB))
    echo -e "${BLUE}  → Current swap: ${CURRENT_SWAP_MB}MB. Adding ${NEEDED_MB}MB...${NC}"
    SWAPFILE="/swapfile-smsly"
    if [ ! -f "$SWAPFILE" ]; then
        fallocate -l ${NEEDED_MB}M "$SWAPFILE" 2>/dev/null || dd if=/dev/zero of="$SWAPFILE" bs=1M count=$NEEDED_MB status=none
        chmod 600 "$SWAPFILE"
        mkswap "$SWAPFILE" >/dev/null 2>&1
        swapon "$SWAPFILE" 2>/dev/null || true
        # Make permanent (idempotent)
        if ! grep -q "$SWAPFILE" /etc/fstab 2>/dev/null; then
            echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
        fi
        echo -e "${GREEN}  ✓ Swap file created and activated (${NEEDED_MB}MB)${NC}"
    else
        # Swap file exists but may not be active
        swapon "$SWAPFILE" 2>/dev/null || true
        echo -e "${GREEN}  ✓ Existing swap file activated${NC}"
    fi
else
    echo -e "${GREEN}  ✓ Swap already sufficient (${CURRENT_SWAP_MB}MB)${NC}"
fi

# ─── Sysctl tuning (idempotent) ──────────────────────────────────────────────
SYSCTL_UPDATED=false

ensure_sysctl() {
    local key="$1" value="$2" desc="$3"
    CURRENT=$(sysctl -n "$key" 2>/dev/null || echo "")
    if [ "$CURRENT" != "$value" ]; then
        sysctl -w "$key=$value" >/dev/null 2>&1 || true
        # Make permanent (idempotent)
        if grep -q "^$key" /etc/sysctl.conf 2>/dev/null; then
            sed -i "s|^$key.*|$key = $value|" /etc/sysctl.conf
        else
            echo "# $desc" >> /etc/sysctl.conf
            echo "$key = $value" >> /etc/sysctl.conf
        fi
        SYSCTL_UPDATED=true
        echo -e "${GREEN}  ✓ $key = $value ($desc)${NC}"
    fi
}

ensure_sysctl "vm.overcommit_memory" "1" "Redis background save fix"
ensure_sysctl "vm.swappiness" "10" "Prefer RAM over swap"
ensure_sysctl "net.core.somaxconn" "511" "Redis connection backlog"

if [ "$SYSCTL_UPDATED" = "false" ]; then
    echo -e "${GREEN}  ✓ Sysctl settings already optimal${NC}"
fi

# ─── OOM Protection for critical containers ──────────────────────────────────
echo -e "${BLUE}  → Setting OOM protection for critical containers...${NC}"
for CONTAINER in smsly-hosting-nginx-1 smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgbouncer-1; do
    CPID=$(docker inspect --format '{{.State.Pid}}' "$CONTAINER" 2>/dev/null || echo "")
    if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
        echo -500 > "/proc/$CPID/oom_score_adj" 2>/dev/null || true
    fi
done
echo -e "${GREEN}  ✓ OOM protection set (nginx, backend, db, pgbouncer)${NC}"

echo -e "${GREEN}  ✓ System memory hardening complete${NC}"

# -----------------------------------------------------------------------------
# 9. Verification
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[9/9] Verifying Deployment...${NC}"
VERIFY_PASS_COUNT=0
VERIFY_TOTAL=5
sleep 5

# ─── Check 1: Verify nginx loaded custom config (not default) ──────────────
echo -e "${BLUE}  → [1/5] Verifying nginx configuration...${NC}"
NGINX_CONFIG_CHECK=$(docker exec smsly-hosting-nginx-1 head -1 /etc/nginx/nginx.conf 2>/dev/null || echo "FAIL")
if echo "$NGINX_CONFIG_CHECK" | grep -q "events"; then
    echo -e "${GREEN}  ✓ Nginx config verified (custom proxy config loaded)${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  ⚠ Nginx may have default config — force-recreating...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps nginx
    docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true
    sleep 3
    NGINX_CONFIG_CHECK=$(docker exec smsly-hosting-nginx-1 head -1 /etc/nginx/nginx.conf 2>/dev/null || echo "FAIL")
    if echo "$NGINX_CONFIG_CHECK" | grep -q "events"; then
        echo -e "${GREEN}  ✓ Nginx config fixed after force-recreate${NC}"
        VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Nginx config still incorrect. Manual fix needed.${NC}"
    fi
fi

# ─── Check 2: Health check ─────────────────────────────────────────────────
echo -e "${BLUE}  → [2/5] Running health check...${NC}"
HEALTH_OK=false
for attempt in 1 2 3 4 5; do
    if curl -sfL http://127.0.0.1/health >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    elif curl -sfL http://127.0.0.1:8090/health >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    fi
    echo -e "${YELLOW}  → Health check attempt $attempt/5 — waiting...${NC}"
    if [ "$attempt" -eq 1 ]; then
        docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true
    fi
    sleep 5
done

if [ "$HEALTH_OK" = "true" ]; then
    echo -e "${GREEN}  ✓ Health Check Passed!${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  ⚠ Health check did not respond — services may still be starting.${NC}"
fi

# ─── Check 3: All containers running ──────────────────────────────────────
echo -e "${BLUE}  → [3/5] Checking container status...${NC}"
RUNNING_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --status running -q 2>/dev/null | wc -l)
TOTAL_COUNT=$(docker compose -f "$COMPOSE_FILE" ps -q 2>/dev/null | wc -l)
if [ "$RUNNING_COUNT" -eq "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt 0 ]; then
    echo -e "${GREEN}  ✓ All $TOTAL_COUNT containers running${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Only $RUNNING_COUNT/$TOTAL_COUNT containers running${NC}"
fi

# ─── Check 4: Swap is sufficient ──────────────────────────────────────────
echo -e "${BLUE}  → [4/5] Checking swap...${NC}"
SWAP_TOTAL=$(free -m | awk '/^Swap:/{print $2}')
if [ "$SWAP_TOTAL" -ge 1500 ]; then
    echo -e "${GREEN}  ✓ Swap sufficient (${SWAP_TOTAL}MB)${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  ⚠ Swap low (${SWAP_TOTAL}MB) — recommend 2GB+${NC}"
fi

# ─── Check 5: Caddy running ───────────────────────────────────────────────
echo -e "${BLUE}  → [5/5] Checking Caddy...${NC}"
if systemctl is-active --quiet caddy 2>/dev/null; then
    echo -e "${GREEN}  ✓ Caddy reverse proxy active${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Caddy is not running${NC}"
fi

# Show container status
echo -e "\n${BLUE}Container Status:${NC}"
docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
    docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true

echo -e "\n${BLUE}Verification Score: $VERIFY_PASS_COUNT/$VERIFY_TOTAL${NC}"

# ─── Install Autoscaler as systemd service ──────────────────────────────────
echo -e "${BLUE}  → Installing smsly-autoscaler systemd service...${NC}"
cp "$INSTALL_DIR/smsly-autoscaler.py" /opt/smsly/autoscaler.py 2>/dev/null || {
    mkdir -p /opt/smsly
    cp "$INSTALL_DIR/smsly-autoscaler.py" /opt/smsly/autoscaler.py
}
chmod +x /opt/smsly/autoscaler.py

# Source .env for the token
AUTOSCALER_API_TOKEN="$(env_get_value "$INSTALL_DIR/.env" "AUTOSCALER_API_TOKEN")"

cat <<SVCEOF > /etc/systemd/system/smsly-autoscaler.service
[Unit]
Description=SMSLY VPS Autoscaler — Cross-Service Resource Manager
After=docker.service
Requires=docker.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/smsly/autoscaler.py
Restart=always
RestartSec=10
Environment=AUTOSCALER_API_TOKEN=${AUTOSCALER_API_TOKEN}
Environment=AUTOSCALER_API_BIND=127.0.0.1
Environment=AUTOSCALER_API_PORT=9876
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable smsly-autoscaler 2>/dev/null || true
systemctl restart smsly-autoscaler 2>/dev/null || true
echo -e "${GREEN}  ✓ smsly-autoscaler service installed and started${NC}"

# ─── Remove rollback trap (installation succeeded) ─────────────────────────
trap - EXIT

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   ✓ INSTALLATION SUCCESSFUL!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"

if [ "$USE_SSL" = "true" ]; then
    echo -e "   URL:         https://$DOMAIN"
else
    echo -e "   URL:         http://$PUBLIC_IP"
fi
echo -e "   Admin:       /admin"
echo -e "   Credentials: $CREDENTIALS_FILE"
echo -e "   Install Log: $LOG_FILE"
echo -e "   Location:    $INSTALL_DIR"
echo -e "   Memory:      $(free -m | awk '/^Mem:/{print $7}')MB available"
echo -e "   Swap:        $(free -m | awk '/^Swap:/{print $2}')MB total"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  View credentials:   cat $CREDENTIALS_FILE${NC}"
echo -e "${YELLOW}  View logs:          cat $LOG_FILE${NC}"
echo -e "${YELLOW}  Update frontend:    sudo bash install.sh --update-frontend${NC}"
echo -e "${YELLOW}  Update backend:     sudo bash install.sh --update-backend${NC}"
echo -e "${YELLOW}  Full update:        sudo bash install.sh --update${NC}"
echo -e "${YELLOW}  Runtime recovery:   sudo bash install.sh --recover${NC}"
echo -e "${YELLOW}  Debug snapshot:     sudo bash install.sh --debug${NC}"
echo -e "${YELLOW}  Wipe install:       sudo bash install.sh --wipe${NC}"

# ─── Conditional Auto-Reboot (only if ALL checks passed) ────────────────────
if [ "$VERIFY_PASS_COUNT" -eq "$VERIFY_TOTAL" ]; then
    echo -e "\n${GREEN}  ✓ All $VERIFY_TOTAL/$VERIFY_TOTAL verification checks passed.${NC}"
    if [ -e /dev/tty ] && [ -z "${SKIP_REBOOT:-}" ]; then
        echo -e "${YELLOW}  System will reboot in 30 seconds to apply sysctl changes.${NC}"
        echo -e "${YELLOW}  Press Ctrl+C to cancel, or wait...${NC}"
        for i in $(seq 30 -1 1); do
            printf "\r${YELLOW}  Rebooting in %2d seconds... ${NC}" "$i"
            sleep 1
        done
        echo -e "\n${BLUE}  → Rebooting now...${NC}"
        reboot
    else
        echo -e "${YELLOW}  Non-interactive mode — skipping auto-reboot.${NC}"
        echo -e "${YELLOW}  Run 'sudo reboot' manually to apply sysctl changes.${NC}"
    fi
else
    echo -e "\n${RED}  ⚠ Only $VERIFY_PASS_COUNT/$VERIFY_TOTAL checks passed — skipping auto-reboot.${NC}"
    echo -e "${YELLOW}  Fix the failed checks above, then run: sudo reboot${NC}"
    if [ "${SMSLY_STRICT_VERIFY:-0}" = "1" ]; then
        echo -e "${RED}  ✗ Strict verification is enabled; failing installation.${NC}"
        exit 1
    fi
fi
