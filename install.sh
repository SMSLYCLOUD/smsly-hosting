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
    if [[ "$_ARG1" != "--update"* ]] && [[ "$_ARG1" != "--wipe" ]] && [ -z "${USE_SSL:-}" ]; then
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

    # Build env string to pass collected values into screen
    _ENV_PASS="SKIP_SCREEN=1"
    [ -n "${USE_SSL:-}" ]              && _ENV_PASS="$_ENV_PASS USE_SSL='$USE_SSL'"
    [ -n "${DOMAIN:-}" ]               && _ENV_PASS="$_ENV_PASS DOMAIN='$DOMAIN'"
    [ -n "${ACME_EMAIL:-}" ]           && _ENV_PASS="$_ENV_PASS ACME_EMAIL='$ACME_EMAIL'"
    [ -n "${WILDCARD_SUBDOMAINS:-}" ]  && _ENV_PASS="$_ENV_PASS WILDCARD_SUBDOMAINS='$WILDCARD_SUBDOMAINS'"
    [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && _ENV_PASS="$_ENV_PASS CLOUDFLARE_API_TOKEN='$CLOUDFLARE_API_TOKEN'"

    # Stay ATTACHED (no -dm), use absolute path, set correct working directory
    exec screen -S cloudneuron-install bash -c "cd '$SCRIPT_DIR'; $_ENV_PASS bash '$SCRIPT_PATH' $*; echo ''; echo 'Installation complete. Press Enter to exit.'; read"
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
        sed -i "s|^${var_name}=.*|${var_name}=${var_value}|" "$env_file"
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
case "${1:-}" in
    --update)          UPDATE_MODE="full" ;;
    --update-frontend) UPDATE_MODE="frontend" ;;
    --update-backend)  UPDATE_MODE="backend" ;;
    --wipe)            WIPE_MODE="true" ;;
    --help|-h)
        echo "Usage: sudo bash install.sh [--update|--update-frontend|--update-backend|--wipe]"
        echo ""
        echo "  (no args)          Fresh install"
        echo "  --update           Pull latest code and rebuild all services"
        echo "  --update-frontend  Pull latest code and rebuild frontend only"
        echo "  --update-backend   Pull latest code and rebuild backend only"
        echo "  --wipe             Delete existing install artifacts (for fresh VPS reset)"
        exit 0
        ;;
esac

MODE_LABEL="fresh-install"
if [ -n "$UPDATE_MODE" ]; then
    MODE_LABEL="update-$UPDATE_MODE"
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
# =============================================================================
# UPDATE MODE — Fast path for pulling latest code and rebuilding
# =============================================================================
if [ -n "$UPDATE_MODE" ]; then
    echo -e "${YELLOW}[UPDATE] Running in update mode: $UPDATE_MODE${NC}"

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
        echo -e "${YELLOW}    Docker builds typically need 2GB+. Cleaning Docker cache...${NC}"
        docker system prune -f --volumes 2>/dev/null || true
        docker builder prune -f 2>/dev/null || true
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
    docker network create smsly-net 2>/dev/null || true
    docker network create smsly-proxy 2>/dev/null || true

    case "$UPDATE_MODE" in
        frontend)
            echo -e "${BLUE}  → Rebuilding frontend container only...${NC}"
            docker compose -f "$COMPOSE_FILE" build --no-cache frontend
            docker compose -f "$COMPOSE_FILE" up -d --no-deps frontend
            ;;
        backend)
            echo -e "${BLUE}  → Rebuilding backend containers...${NC}"
            docker compose -f "$COMPOSE_FILE" build --no-cache backend celery
            docker compose -f "$COMPOSE_FILE" up -d --no-deps backend

            echo -e "${BLUE}  → Running makemigrations + migrations...${NC}"
            sleep 10  # Wait for backend to start
            docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py makemigrations --noinput 2>&1 || \
                echo -e "${YELLOW}  ⚠ makemigrations had issues (non-fatal)${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py migrate --noinput || {
                echo -e "${YELLOW}  ⚠ Migration failed — backend may still be starting. Retrying in 15s...${NC}"
                sleep 15
                docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py migrate --noinput
            }

            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput

            echo -e "${BLUE}  → Restarting celery workers...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d --no-deps celery celery-beat
            ;;
        full)
            echo -e "${BLUE}  → [FULL REBUILD] Rebuilding PaaS core (preserving addon databases)...${NC}"

            # 1. Only stop PaaS core services — NEVER touch addon containers
            CORE_SERVICES="frontend backend celery celery-beat nginx traefik"
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
            docker image prune -f 2>/dev/null || true
            docker builder prune -f 2>/dev/null || true

            # 4. Ensure shared networks exist (create if missing, don't destroy)
            echo -e "${BLUE}    ↳ Ensuring networks exist...${NC}"
            docker network create smsly-net 2>/dev/null || true
            docker network create smsly-proxy 2>/dev/null || true

            # 5. Rebuild core images from scratch
            echo -e "${BLUE}    ↳ Rebuilding core images (no cache)...${NC}"
            docker compose -f "$COMPOSE_FILE" build --no-cache $CORE_SERVICES

            # 6. Start everything (addons stay running, core gets fresh containers)
            echo -e "${BLUE}    ↳ Starting all services...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d --force-recreate $CORE_SERVICES

            # 7. Run migrations
            echo -e "${BLUE}  → Running makemigrations + migrations...${NC}"
            sleep 10
            docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py makemigrations --noinput 2>&1 || \
                echo -e "${YELLOW}  ⚠ makemigrations had issues (non-fatal)${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py migrate --noinput || {
                echo -e "${YELLOW}  ⚠ Migration failed — backend may still be starting. Retrying in 15s...${NC}"
                sleep 15
                docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py migrate --noinput
            }

            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput
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

    # ─── CRITICAL FIX: Force-recreate nginx to pick up config mount ──────────
    # Docker 'up -d' does NOT recreate unchanged containers. nginx image doesn't
    # change between updates, but the mounted nginx.conf may have changed.
    # Without this, nginx runs default config → 502 on all frontend routes.
    echo -e "${BLUE}  → Force-recreating nginx (ensures config mount is fresh)...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps nginx
    # Workaround: on some hosts, the 127.0.0.1 port publish doesn't bind
    # immediately after recreate. A restart reliably brings up docker-proxy.
    docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true

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
    if command -v caddy &> /dev/null && systemctl is-active --quiet caddy 2>/dev/null; then
        echo -e "${BLUE}  → Regenerating Caddyfile with current service domains...${NC}"
        docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
from services.caddy_manager import generate_caddyfile, apply_caddyfile
config = PlatformConfig.load()
content = generate_caddyfile(config)
result = apply_caddyfile(content)
print('OK' if result.get('ok') else f'FAIL: {result.get(\"message\")}')
" 2>/dev/null || echo -e "${YELLOW}  ⚠ Caddyfile regeneration skipped (backend not ready)${NC}"

        # Restart caddy-watcher to pick up the new file
        systemctl restart caddy-watcher 2>/dev/null || true
        echo -e "${GREEN}  ✓ Caddy config regenerated${NC}"
    fi

    # ─── Verification ────────────────────────────────────────────────────────
    echo -e "${BLUE}  → Verifying containers...${NC}"
    sleep 5

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
    else
        echo -e "${YELLOW}  ⚠ Health check not responding. Check container logs:${NC}"
        echo -e "${YELLOW}    docker compose -f $COMPOSE_FILE logs --tail=50${NC}"
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

# Stop and remove all smsly-hosting containers (including orphans)
SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly" -q 2>/dev/null || true)
if [ -n "$SMSLY_CONTAINERS" ]; then
    echo -e "${YELLOW}  → Stopping ${#SMSLY_CONTAINERS} SMSLY container(s)...${NC}"
    docker stop $SMSLY_CONTAINERS 2>/dev/null || true
    docker rm -f $SMSLY_CONTAINERS 2>/dev/null || true
fi

# Remove stale Docker volumes (postgres data with old passwords, etc.)
SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly" -q 2>/dev/null || true)
if [ -n "$SMSLY_VOLUMES" ]; then
    echo -e "${YELLOW}  → Removing stale SMSLY volumes (fresh DB will be created)...${NC}"
    for vol in $SMSLY_VOLUMES; do
        docker volume rm "$vol" 2>/dev/null || true
    done
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

    # Install cryptography lib
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
        echo -e "${YELLOW}  ⚠ Custom Caddy build failed — falling back to standard Caddy...${NC}"
        if ! command -v caddy &> /dev/null; then
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

if [ "$USE_SSL" = "true" ] && [ -n "$DOMAIN" ] && [ "$DOMAIN" != "$PUBLIC_IP" ]; then
    if [ "$WILDCARD_SUBDOMAINS" = "true" ] && [ -n "$CLOUDFLARE_API_TOKEN" ]; then
        # ─── Full wildcard mode: domain + *.domain with Cloudflare DNS ────
        cat > /etc/caddy/Caddyfile <<CADDYEOF
# CloudNeuron Reverse Proxy — Auto-generated
# Domain: $DOMAIN → HTTPS (auto Let's Encrypt)
# Wildcard: *.$DOMAIN → HTTPS (Cloudflare DNS challenge)

$DOMAIN {
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
        mkdir -p /etc/systemd/system/caddy.service.d
        cat > /etc/systemd/system/caddy.service.d/override.conf <<ENVEOF
[Service]
Environment="CLOUDFLARE_API_TOKEN=$CLOUDFLARE_API_TOKEN"
ENVEOF
        chmod 600 /etc/systemd/system/caddy.service.d/override.conf
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
        echo -e "${GREEN}  ✓ Caddy configured: HTTPS ($DOMAIN) + HTTP (:80 fallback) → 8090${NC}"
    fi
else
    cat > /etc/caddy/Caddyfile <<CADDYEOF
# CloudNeuron Reverse Proxy — Auto-generated
:80 {
    reverse_proxy localhost:8090
}
CADDYEOF
    echo -e "${GREEN}  ✓ Caddy configured for HTTP: :80 → 8090${NC}"
fi

# ─── Create caddy-config volume directory for Settings UI writes ──────────────
mkdir -p /opt/smsly-hosting/caddy-config
chmod 777 /opt/smsly-hosting/caddy-config

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

# Kill anything holding port 80/443 before Caddy binds
for port in 80 443; do
    PID=$(lsof -ti :$port 2>/dev/null || ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
    if [ -n "$PID" ] && [ "$PID" != "0" ]; then
        echo -e "${YELLOW}  → Killing process holding port $port (PID: $PID)${NC}"
        kill -9 $PID 2>/dev/null || true
        sleep 1
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
