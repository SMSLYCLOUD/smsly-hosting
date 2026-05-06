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
#   Runtime refresh:  sudo bash install.sh --refresh
#   Wipe install:     sudo bash install.sh --wipe
#
#   Rust Twin:        sudo bash install.sh --rust
#                     sudo bash install.sh --update --rust
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

# ─── Lock File Check ─────────────────────────────────────────────────────────
LOCK_FILE="/tmp/smsly-install.lock"
if [ -f "$LOCK_FILE" ]; then
    PID=$(cat "$LOCK_FILE")
    if [ "$PID" != "$$" ] && kill -0 "$PID" 2>/dev/null; then
        echo -e "\033[0;31mERROR: Another installer instance (PID $PID) is already running.\033[0m"
        echo -e "If you are sure no other instance is running, remove $LOCK_FILE and try again."
        exit 1
    fi
fi
echo $$ > "$LOCK_FILE"
trap "rm -f $LOCK_FILE" EXIT

# ─── Parse flags early ───────────────────────────────────────────────────────
NON_INTERACTIVE=false
MODE_AGENT_LITE=false
RESUME_MODE=false
for arg in "$@"; do
  case "$arg" in
    --non-interactive) NON_INTERACTIVE=true ;;
    --mode=agent-lite|--agent-lite) MODE_AGENT_LITE=true ;;
    --resume) RESUME_MODE=true ;;
    --wipe) rm -f "/opt/smsly-hosting/.smsly_install_state" ;;
  esac
done

# ─── Resolve script path BEFORE any cd (screen guard needs absolute path) ────
SCRIPT_PATH="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

# ─── Screen Session Guard (survives SSH disconnects) ─────────────────────────
# Collect ALL interactive input FIRST (before screen), then re-launch inside
# a screen session with the collected values as env vars.
# To reattach after disconnect: screen -r cloudneuron-install
if [ -z "${STY:-}" ] && [ -z "${SKIP_SCREEN:-}" ] && [ "$NON_INTERACTIVE" != "true" ] && [[ "${1:-}" != "--verify" ]] && [[ "${1:-}" != "--debug" ]] && [ -t 0 ]; then
    # Install screen if missing
    if ! command -v screen &> /dev/null; then
        apt-get update -qq && apt-get install -y screen > /dev/null 2>&1
    fi

    # ── Pre-collect interactive input (only for fresh installs) ──────────
    # Skip collection if values are already pre-seeded via env vars, or if
    # this is an --update / --wipe run (those don't need interactive input).
    _ARG1="${1:-}"
    if [[ "$_ARG1" != "--update"* ]] && [[ "$_ARG1" != "--wipe" ]] && [[ "$_ARG1" != "--recover" ]] && [[ "$_ARG1" != "--refresh" ]] && [[ "$_ARG1" != "--debug" ]] && [[ "$_ARG1" != "--verify" ]] && [ -z "${USE_SSL:-}" ]; then
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
        echo -e "\033[0;34mSelect Backend Architecture:\033[0m"
        echo -e "  1) \033[0;32mLegacy Python\033[0m (Stable monolith)"
        echo -e "  2) \033[0;36mNext-Gen Rust\033[0m (High-performance microservices, Beta)"

        if [ -e /dev/tty ]; then
            read -p "Enter choice [1]: " _ARCH_CHOICE < /dev/tty
            if [ "$_ARCH_CHOICE" = "2" ]; then
                RUST_TWIN_MODE="true"
                COMPOSE_FILE="rust_twin/docker-compose.yml"
            fi
        fi

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

        # ── Agent Lite Selection ──────────────────────────────────────────
        if [ "$MODE_AGENT_LITE" = "true" ]; then
            echo ""
            echo -e "\033[1;35m═══════════════════════════════════════════════════════════"
            echo "  CONFIGURING LITE AGENT NODE"
            echo "═══════════════════════════════════════════════════════════\033[0m"
            
            _M_IP=""
            while [ -z "$_M_IP" ]; do
                read -p "  Enter Master VPS IP Address: " _M_IP < /dev/tty
            done
            export MASTER_IP="$_M_IP"

            _M_DB_PASS=""
            while [ -z "$_M_DB_PASS" ]; do
                read -sp "  Enter Master Database Password: " _M_DB_PASS < /dev/tty
                echo ""
            done
            export MASTER_DB_PASSWORD="$_M_DB_PASS"

            _M_MQ_PASS=""
            while [ -z "$_M_MQ_PASS" ]; do
                read -sp "  Enter Master RabbitMQ Password: " _M_MQ_PASS < /dev/tty
                echo ""
            done
            export MASTER_MQ_PASSWORD="$_M_MQ_PASS"
            
            export COMPOSE_FILE="infrastructure/docker/docker-compose.agent-lite.yml"
        fi
    fi

    if [ "${RUST_TWIN_MODE:-false}" = "true" ]; then
        echo -e "\033[1;36m"
        echo "═══════════════════════════════════════════════════════════"
        echo "  INITIALIZING NEXT-GEN RUST TWIN MODE"
        echo "  This will deploy the high-performance Rust stack."
        echo "═══════════════════════════════════════════════════════════"
        echo -e "\033[0m"
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
    exec screen -S cloudneuron-install bash -c "cd $(printf '%q' "$SCRIPT_DIR"); $_ENV_PASS bash $(printf '%q' "$SCRIPT_PATH") $*"
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

configure_docker_mirror() {
    # Ensure COMPOSE_FILE is defined for this scope
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    
    # Option B: Pull-Through Cache
    if [ -n "${MASTER_IP:-}" ] && [ "$MASTER_IP" != "127.0.0.1" ] && [ "$MASTER_IP" != "$(detect_public_ip)" ]; then
        # This is a Follower node
        echo -e "${BLUE}  → Configuring Docker pull-through cache mirror (Master: $MASTER_IP)...${NC}"
        mkdir -p /etc/docker
        
        # Build the daemon.json
        cat > /etc/docker/daemon.json <<EOF
{
  "registry-mirrors": ["http://${MASTER_IP}:5001"],
  "insecure-registries": ["${MASTER_IP}:5000", "${MASTER_IP}:5001"]
}
EOF
        systemctl restart docker || true
        echo -e "${GREEN}  ✓ Docker mirror configured${NC}"
    else
        # This is the Master node (or MASTER_IP matches local IP)
        # Ensure the mirror service is UP if it exists in the compose file
        if [ -f "$compose_f" ] && grep -q "docker-mirror:" "$compose_f"; then
            echo -e "${BLUE}  → Ensuring Docker pull-through cache mirror is running...${NC}"
            docker compose -f "$compose_f" up -d docker-mirror >/dev/null 2>&1 || true
        fi
    fi
}

# ─── Pre-flight Validators ──────────────────────────────────────────────────
check_internet() {
    echo -e "${BLUE}  → Checking internet connectivity...${NC}"
    if ! curl -Is --connect-timeout 5 https://google.com >/dev/null; then
        echo -e "${RED}  ✗ No internet access. Check your firewall/network settings.${NC}"
        exit 1
    fi
    if ! host github.com >/dev/null 2>&1; then
         # Fallback to ping if host is missing
         if ! ping -c 1 github.com >/dev/null 2>&1; then
             echo -e "${RED}  ✗ DNS resolution failed for github.com.${NC}"
             exit 1
         fi
    fi
    echo -e "${GREEN}  ✓ Internet & DNS OK${NC}"
}

check_hardware() {
    echo -e "${BLUE}  → Checking hardware requirements...${NC}"
    local ram_kb
    ram_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    local ram_mb=$((ram_kb / 1024))
    echo -e "${BLUE}  RAM: ${ram_mb}MB${NC}"
    if [ "$ram_mb" -lt 950 ]; then # Allow some margin for 1GB VPS
        echo -e "${RED}  ✗ Insufficient RAM ($ram_mb MB). CloudNeuron requires at least 1GB.${NC}"
        exit 1
    fi
    
    local cores
    cores=$(nproc)
    echo -e "${BLUE}  CPU Cores: ${cores}${NC}"
    if [ "$cores" -lt 1 ]; then
        echo -e "${RED}  ✗ CPU detection failed.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ Hardware requirements met${NC}"
}

# ─── Installation State Machine ──────────────────────────────────────────────
STATE_FILE="/opt/smsly-hosting/.smsly_install_state"

set_checkpoint() {
    local name="$1"
    mkdir -p "$(dirname "$STATE_FILE")"
    # Ensure name is unique in the file to avoid duplicates on resume
    if ! grep -q "^$name$" "$STATE_FILE" 2>/dev/null; then
        echo "$name" >> "$STATE_FILE"
    fi
    echo -e "${GREEN}  ✓ Checkpoint reached: $name${NC}"
}

is_checkpoint_done() {
    local name="$1"
    if [ "$RESUME_MODE" != "true" ]; then
        return 1
    fi
    if [ -f "$STATE_FILE" ] && grep -q "^$name$" "$STATE_FILE"; then
        echo -e "${BLUE}  → Skipping already completed step: $name${NC}"
        return 0
    fi
    return 1
}

# ─── Constants ───────────────────────────────────────────────────────────────
SMSLY_BRANCH="${SMSLY_BRANCH:-main}"
SMSLY_GIT_REMOTE="${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"
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
    python3 - "$env_file" "$var_name" "$var_value" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
prefix = f"{key}="

if not env_path.exists():
    env_path.write_text(f"{key}={value}\n")
    sys.exit(0)

lines = env_path.read_text().splitlines()
updated = []
found = False

for line in lines:
    if line.startswith(prefix):
        if not found:
            updated.append(f"{key}={value}")
            found = True
        # Skip any subsequent duplicates
        continue
    updated.append(line)

if not found:
    updated.append(f"{key}={value}")

env_path.write_text("\n".join(updated) + "\n")
PY
}

env_ensure_var() {
    local env_file="$1"
    local var_name="$2"
    local var_value="$3"
    local var_comment="${4:-}"
    local current_val
    current_val="$(env_get_value "$env_file" "$var_name")"
    
    if [ -z "$current_val" ]; then
        echo -e "${BLUE}  -> Setting $var_name in .env${NC}"
        [ -n "$var_comment" ] && ! grep -q "# $var_comment" "$env_file" 2>/dev/null && echo "# $var_comment" >> "$env_file"
        env_set_value "$env_file" "$var_name" "$var_value"
        echo -e "${GREEN}  OK $var_name set${NC}"
    fi
}

dump_diagnostic_logs() {
    local env_file="${1:-$INSTALL_DIR/.env}"
    echo -e "\n${RED}════════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}   DIAGNOSTIC LOG DUMP (FAILURE ANALYSIS)${NC}"
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
    
    echo -e "${YELLOW}  → System Resource Snapshot:${NC}"
    free -m
    df -h /
    
    echo -e "\n${YELLOW}  → Container Status:${NC}"
    docker compose -f "$COMPOSE_FILE" ps
    
    echo -e "\n${YELLOW}  → Backend Logs (Last 50 lines):${NC}"
    docker compose -f "$COMPOSE_FILE" logs --tail=50 backend || true
    
    echo -e "\n${YELLOW}  → Nginx Logs (Last 50 lines):${NC}"
    docker compose -f "$COMPOSE_FILE" logs --tail=50 nginx || true

    echo -e "\n${YELLOW}  → Redis Logs (Last 50 lines):${NC}"
    docker compose -f "$COMPOSE_FILE" logs --tail=50 redis || true
    
    echo -e "\n${YELLOW}  → Database Logs (Last 50 lines):${NC}"
    docker compose -f "$COMPOSE_FILE" logs --tail=50 db || true
    
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}\n"
}

apply_env_platform_overrides() {
    local env_file="$1"
    local changed=false
    local current_domain current_use_ssl current_acme_email current_wildcard current_cf_token current_public_ip
    local desired_domain desired_use_ssl desired_acme_email desired_wildcard desired_cf_token desired_public_ip

    [ -f "$env_file" ] || return 0

    current_domain="$(env_get_value "$env_file" "DOMAIN")"
    current_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
    current_acme_email="$(env_get_value "$env_file" "ACME_EMAIL")"
    current_wildcard="$(env_get_value "$env_file" "WILDCARD_SUBDOMAINS")"
    current_cf_token="$(env_get_value "$env_file" "CLOUDFLARE_API_TOKEN")"
    current_public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"

    if [ "${DOMAIN+x}" = "x" ]; then
        desired_domain="${DOMAIN}"
    else
        desired_domain="${current_domain}"
    fi
    if [ "${USE_SSL+x}" = "x" ]; then
        desired_use_ssl="${USE_SSL}"
    else
        desired_use_ssl="${current_use_ssl}"
    fi
    if [ "${ACME_EMAIL+x}" = "x" ]; then
        desired_acme_email="${ACME_EMAIL}"
    else
        desired_acme_email="${current_acme_email}"
    fi
    if [ "${WILDCARD_SUBDOMAINS+x}" = "x" ]; then
        desired_wildcard="${WILDCARD_SUBDOMAINS}"
    else
        desired_wildcard="${current_wildcard}"
    fi
    if [ "${CLOUDFLARE_API_TOKEN+x}" = "x" ]; then
        desired_cf_token="${CLOUDFLARE_API_TOKEN}"
    else
        desired_cf_token="${current_cf_token}"
    fi
    if [ "${PUBLIC_IP+x}" = "x" ]; then
        desired_public_ip="${PUBLIC_IP}"
    else
        desired_public_ip="${current_public_ip}"
    fi

    if [ -z "$desired_public_ip" ]; then
        desired_public_ip="$(detect_public_ip)"
    fi

    if [ "$desired_domain" != "$current_domain" ]; then
        env_set_value "$env_file" "DOMAIN" "$desired_domain"
        changed=true
    fi
    if [ "$desired_use_ssl" != "$current_use_ssl" ]; then
        env_set_value "$env_file" "USE_SSL" "$desired_use_ssl"
        changed=true
    fi
    if [ "$desired_acme_email" != "$current_acme_email" ]; then
        env_set_value "$env_file" "ACME_EMAIL" "$desired_acme_email"
        changed=true
    fi
    if [ "$desired_wildcard" != "$current_wildcard" ]; then
        env_set_value "$env_file" "WILDCARD_SUBDOMAINS" "$desired_wildcard"
        changed=true
    fi
    if [ "$desired_cf_token" != "$current_cf_token" ]; then
        env_set_value "$env_file" "CLOUDFLARE_API_TOKEN" "$desired_cf_token"
        changed=true
    fi
    if [ "$desired_public_ip" != "$current_public_ip" ]; then
        env_set_value "$env_file" "PUBLIC_IP" "$desired_public_ip"
        changed=true
    fi

    DOMAIN="$desired_domain"
    USE_SSL="$desired_use_ssl"
    ACME_EMAIL="$desired_acme_email"
    WILDCARD_SUBDOMAINS="$desired_wildcard"
    CLOUDFLARE_API_TOKEN="$desired_cf_token"
    PUBLIC_IP="$desired_public_ip"

    if [ "$changed" = true ]; then
        echo -e "${GREEN}  ✓ Applied platform/domain overrides to .env${NC}"
        echo -e "${BLUE}    DOMAIN=${DOMAIN} USE_SSL=${USE_SSL} WILDCARD_SUBDOMAINS=${WILDCARD_SUBDOMAINS}${NC}"
    fi
}

DOMAIN_SYNC_UPDATED_COUNT=0
DOMAIN_SYNC_REDEPLOY_REQUIRED=0
DOMAIN_SYNC_SERVICE_IDS=""

sync_platform_domain_state() {
    local env_file="${1:-$INSTALL_DIR/.env}"
    local sync_domain sync_use_ssl sync_wildcard sync_cf_token sync_public_ip
    local sync_json=""

    [ -f "$env_file" ] || return 0

    sync_domain="$(env_get_value "$env_file" "DOMAIN")"
    sync_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
    sync_wildcard="$(env_get_value "$env_file" "WILDCARD_SUBDOMAINS")"
    sync_cf_token="$(env_get_value "$env_file" "CLOUDFLARE_API_TOKEN")"
    sync_public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"

    [ -n "$sync_public_ip" ] || sync_public_ip="$(detect_public_ip)"

    echo -e "${BLUE}  → Syncing PlatformConfig + public domains from installer state...${NC}"
    sync_json="$(
        docker compose -f "$COMPOSE_FILE" exec -T \
            -e SMSLY_SYNC_DOMAIN="$sync_domain" \
            -e SMSLY_SYNC_USE_SSL="$sync_use_ssl" \
            -e SMSLY_SYNC_WILDCARD="$sync_wildcard" \
            -e SMSLY_SYNC_CF_TOKEN="$sync_cf_token" \
            -e SMSLY_SYNC_PUBLIC_IP="$sync_public_ip" \
            backend python manage.py shell <<'PY'
import json
import os

from apps.deployments.models import EnvironmentVariable, PlatformConfig, Service


def parse_bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_platform_domain(value: str) -> str:
    raw = str(value or "").strip().lower().rstrip(".")
    if raw in {"", "localhost", "127.0.0.1"}:
        return ""
    parts = raw.split(".")
    if len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
        return ""
    return raw


def rewrite_public_domain(current_domain: str, old_base: str, new_base: str):
    current = str(current_domain or "").strip().lower().rstrip(".")
    old_base = str(old_base or "").strip().lower().rstrip(".")
    new_base = str(new_base or "").strip().lower().rstrip(".")
    if not current or not old_base or not new_base or old_base == new_base:
        return None
    if current == old_base:
        return new_base
    suffix = f".{old_base}"
    if not current.endswith(suffix):
        return None
    prefix = current[:-len(suffix)].rstrip(".")
    return f"{prefix}.{new_base}" if prefix else new_base


cfg = PlatformConfig.load()
old_base = Service.default_public_base_domain()
original_domain = (cfg.domain or "").strip().lower().rstrip(".")

cfg.domain = normalize_platform_domain(os.environ.get("SMSLY_SYNC_DOMAIN", ""))
cfg.use_ssl = parse_bool(os.environ.get("SMSLY_SYNC_USE_SSL", "false"))
cfg.wildcard_subdomains = parse_bool(os.environ.get("SMSLY_SYNC_WILDCARD", "false"))
cfg.cloudflare_api_token = str(os.environ.get("SMSLY_SYNC_CF_TOKEN", "") or "").strip()
cfg.server_ip = str(os.environ.get("SMSLY_SYNC_PUBLIC_IP", "") or "").strip() or None
cfg.save()

new_base = (cfg.domain or "").strip().lower().rstrip(".")
host_keys = ("ALLOWED_HOSTS", "DJANGO_ALLOWED_HOSTS", "MARKETER_ALLOWED_HOSTS")
updated = 0
service_ids = []

if new_base and new_base != old_base:
    for service in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain="").iterator():
        current_domain = str(service.public_domain or "").strip().lower().rstrip(".")
        next_domain = rewrite_public_domain(current_domain, old_base, new_base)
        if not next_domain or next_domain == current_domain:
            continue
        if Service.objects.exclude(pk=service.pk).filter(public_domain=next_domain).exists():
            continue

        service.public_domain = next_domain
        service.save(update_fields=["public_domain"])
        EnvironmentVariable.objects.filter(service=service, key="PUBLIC_DOMAIN").update(value=next_domain)

        for env_var in EnvironmentVariable.objects.filter(service=service, key__in=host_keys):
            value = str(env_var.value or "")
            if current_domain in value and next_domain not in value:
                env_var.value = value.replace(current_domain, next_domain)
                env_var.save(update_fields=["value"])

        updated += 1
        service_ids.append(str(service.id))

result = {
    "domain": cfg.domain,
    "use_ssl": cfg.use_ssl,
    "wildcard_subdomains": cfg.wildcard_subdomains,
    "server_ip": cfg.server_ip or "",
    "old_base_domain": old_base,
    "original_domain": original_domain,
    "updated_service_domains": updated,
    "redeploy_required": bool(updated),
    "service_ids": service_ids,
}
print(json.dumps(result))
PY
    )"

    sync_json="$(echo "$sync_json" | tr -d '\r' | tail -n 1)"
    if [ -z "$sync_json" ]; then
        echo -e "${YELLOW}  ⚠ PlatformConfig sync did not return a result. Continuing with host-level config.${NC}"
        return 0
    fi

    DOMAIN_SYNC_UPDATED_COUNT="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('updated_service_domains', 0))" 2>/dev/null || echo 0)"
    DOMAIN_SYNC_REDEPLOY_REQUIRED="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(1 if json.load(sys.stdin).get('redeploy_required') else 0)" 2>/dev/null || echo 0)"
    DOMAIN_SYNC_SERVICE_IDS="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(','.join(json.load(sys.stdin).get('service_ids', [])))" 2>/dev/null || true)"

    echo -e "${GREEN}  ✓ PlatformConfig synced: domain=$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('domain', ''))" 2>/dev/null)${NC}"
    if [ "${DOMAIN_SYNC_UPDATED_COUNT:-0}" -gt 0 ]; then
        echo -e "${GREEN}  ✓ Rewrote ${DOMAIN_SYNC_UPDATED_COUNT} existing service public domain(s)${NC}"
    fi
}

queue_active_service_redeploys() {
    local reason="${1:-Installer-triggered redeploy}"
    local service_ids="${2:-}"

    docker compose -f "$COMPOSE_FILE" exec -T \
        -e SMSLY_REDEPLOY_REASON="$reason" \
        -e SMSLY_SERVICE_IDS="$service_ids" \
        backend python manage.py shell <<'PY'
import os
import traceback

from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.deployments.tasks import smart_deploy_task


service_ids = [value.strip() for value in os.environ.get("SMSLY_SERVICE_IDS", "").split(",") if value.strip()]
reason = os.environ.get("SMSLY_REDEPLOY_REASON", "Installer-triggered redeploy")
provider = CloudProvider.objects.filter(is_active=True).first()
if not provider:
    print("WARN: No active cloud provider")
else:
    try:
        queryset = Service.objects.filter(id__in=service_ids) if service_ids else Service.objects.all()
        count = 0
        for svc in queryset:
            dep = svc.deployments.filter(status="ACTIVE").order_by("-created_at").first()
            if not dep or not dep.commit_hash:
                continue
            svc.deployments.filter(status="ACTIVE").update(
                status="CANCELLED",
                finished_at=timezone.now(),
            )
            new_dep = Deployment.objects.create(
                service=svc,
                status="QUEUED",
                commit_hash=dep.commit_hash,
                commit_message=reason,
            )
            smart_deploy_task.delay(str(new_dep.id), str(provider.id), skip_review=True)
            count += 1
            print(f"  Queued: {svc.name} ({dep.commit_hash[:7]})")
        print(f"OK: {count} service(s) queued for redeploy")
    except Exception as exc:  # pragma: no cover - installer runtime path
        print(f"WARN: {exc}")
        traceback.print_exc()
PY
}

ensure_env_runtime_defaults() {
    local env_file="$1"
    local redis_password=""
    local postgres_password=""
    local current_domain=""
    local current_public_ip=""
    local current_tunnel_domain=""
    local expected_tunnel_domain="tunnel.localhost"
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
    env_ensure_var "$env_file" "FRP_AUTH_TOKEN" "$(gen_hex_secret 32)" "FRP tunnel relay authentication token"
    env_ensure_var "$env_file" "SMSLY_DISABLE_TIER_GATES" "true" "Disable owner-tier paywall gates in this edition"
    env_ensure_var "$env_file" "PGCAT_ADMIN_PASSWORD" "$(gen_hex_secret 24)" "PgCat administration password (mandatory for 1.2+)"

    redis_password="$(env_get_value "$env_file" "REDIS_PASSWORD")"
    rabbitmq_password="$(env_get_value "$env_file" "RABBITMQ_PASSWORD")"
    postgres_password="$(env_get_value "$env_file" "POSTGRES_PASSWORD")"
    current_domain="$(env_get_value "$env_file" "DOMAIN")"
    current_public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"
    current_tunnel_domain="$(env_get_value "$env_file" "TUNNEL_DOMAIN")"

    if [ -n "$current_domain" ] && [ "$current_domain" != "localhost" ] && ! echo "$current_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        expected_tunnel_domain="tunnel.${current_domain}"
    elif [ -n "$current_public_ip" ] && ! echo "$current_public_ip" | grep -qE '^(127\.0\.0\.1|0\.0\.0\.0)$'; then
        expected_tunnel_domain="tunnel.${current_public_ip}.sslip.io"
    fi

    env_ensure_var "$env_file" "TUNNEL_DOMAIN" "$expected_tunnel_domain" "Base domain for FRP development tunnels"
    if [ -z "$current_tunnel_domain" ] || [ "$current_tunnel_domain" = "tunnel.localhost" ] || [[ "$current_tunnel_domain" == tunnel.* ]]; then
        if [ "$current_tunnel_domain" != "$expected_tunnel_domain" ]; then
            echo -e "${BLUE}  -> Syncing TUNNEL_DOMAIN with platform domain${NC}"
            env_set_value "$env_file" "TUNNEL_DOMAIN" "$expected_tunnel_domain"
            echo -e "${GREEN}  OK TUNNEL_DOMAIN synced${NC}"
        fi
    fi

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

        if [[ "$current_redis_url" =~ ^redis://:.*@redis:6379/0$ ]] && [ "$current_redis_url" != "$expected_redis_url" ]; then
            echo -e "${BLUE}  -> Syncing REDIS_URL with REDIS_PASSWORD${NC}"
            env_set_value "$env_file" "REDIS_URL" "$expected_redis_url"
            echo -e "${GREEN}  OK REDIS_URL synced${NC}"
        fi
    fi

    if [ -n "$rabbitmq_password" ]; then
        expected_celery_broker_url="amqp://smsly_user:${rabbitmq_password}@rabbitmq:5672//"
        current_celery_broker_url="$(env_get_value "$env_file" "CELERY_BROKER_URL")"

        env_ensure_var "$env_file" "CELERY_BROKER_URL" "$expected_celery_broker_url" "Celery broker (RabbitMQ with auth)"

        if [[ "$current_celery_broker_url" =~ ^amqp://smsly_user:.*@rabbitmq:5672//$ ]] && [ "$current_celery_broker_url" != "$expected_celery_broker_url" ]; then
            echo -e "${BLUE}  -> Syncing CELERY_BROKER_URL with RABBITMQ_PASSWORD${NC}"
            env_set_value "$env_file" "CELERY_BROKER_URL" "$expected_celery_broker_url"
            echo -e "${GREEN}  OK CELERY_BROKER_URL synced${NC}"
        fi
    fi

    if [ -n "$postgres_password" ]; then
        # Route through PgCat for connection pooling
        expected_database_url="postgresql://smsly_admin:${postgres_password}@pgcat:5432/smsly_hosting"
        current_database_url="$(env_get_value "$env_file" "DATABASE_URL")"

        # Migrate legacy @db:5432 URLs to @pgcat:5432
        if [[ "$current_database_url" =~ @db:5432 ]]; then
            echo -e "${BLUE}  -> Migrating DATABASE_URL from db to pgcat${NC}"
            local migrated_url="${current_database_url/@db:5432/@pgcat:5432}"
            env_set_value "$env_file" "DATABASE_URL" "$migrated_url"
            current_database_url="$migrated_url"
            echo -e "${GREEN}  OK DATABASE_URL migrated to pgcat${NC}"
        fi

        # Migrate legacy @pgbouncer:5432 URLs to @pgcat:5432
        if [[ "$current_database_url" =~ @pgbouncer:5432 ]]; then
            echo -e "${BLUE}  -> Migrating DATABASE_URL from pgbouncer to pgcat${NC}"
            local migrated_url="${current_database_url/@pgbouncer:5432/@pgcat:5432}"
            env_set_value "$env_file" "DATABASE_URL" "$migrated_url"
            current_database_url="$migrated_url"
            echo -e "${GREEN}  OK DATABASE_URL migrated to pgcat${NC}"
        fi

        # Migrate legacy @pgcat:5432 URLs to @pgcat:5432 (sanity check)
        if [[ "$current_database_url" =~ @pgcat:5432 ]]; then
            echo -e "${BLUE}  -> Migrating DATABASE_URL from pgcat to pgcat${NC}"
            local migrated_url="${current_database_url/@pgcat:5432/@pgcat:5432}"
            env_set_value "$env_file" "DATABASE_URL" "$migrated_url"
            current_database_url="$migrated_url"
            echo -e "${GREEN}  OK DATABASE_URL migrated to pgcat${NC}"
        fi

        if [ -z "$current_database_url" ]; then
            env_ensure_var "$env_file" "DATABASE_URL" "$expected_database_url" "PostgreSQL connection string (via PgCat)"
        elif [[ "$current_database_url" =~ ^postgresql://smsly_admin:.*@pgcat:5432/smsly_hosting$ ]] && [ "$current_database_url" != "$expected_database_url" ]; then
            echo -e "${BLUE}  -> Fixing DATABASE_URL to match POSTGRES_PASSWORD${NC}"
            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            echo -e "${GREEN}  OK DATABASE_URL password synced${NC}"
        fi

        # Direct DB connection for migrations (bypasses PgCat transaction pooling)
        local expected_direct_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
        env_ensure_var "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url" "Direct PostgreSQL connection (migrations only)"
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
        "RABBITMQ_PASSWORD"
        "CELERY_BROKER_URL"
        "GATEWAY_SECRET"
        "GITHUB_WEBHOOK_SECRET"
        "FRP_AUTH_TOKEN"
        "TUNNEL_DOMAIN"
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
            if [ "$var_name" = "RABBITMQ_PASSWORD" ]; then
                local new_rabbitmq_pass
                new_rabbitmq_pass=$(gen_hex_secret 16)
                echo -e "${BLUE}  -> Generating missing RABBITMQ_PASSWORD for upgrade...${NC}"
                echo "RABBITMQ_PASSWORD=$new_rabbitmq_pass" >> "$env_file"
                # Update celery broker URL immediately to use this new password
                env_set_value "$env_file" "CELERY_BROKER_URL" "amqp://smsly_user:${new_rabbitmq_pass}@rabbitmq:5672//"
            elif [ "$var_name" = "GATEWAY_SECRET" ]; then
                echo -e "${BLUE}  -> Generating missing GATEWAY_SECRET...${NC}"
                env_set_value "$env_file" "GATEWAY_SECRET" "$(gen_hex_secret 32)"
            elif [ "$var_name" = "FRP_AUTH_TOKEN" ]; then
                echo -e "${BLUE}  -> Generating missing FRP_AUTH_TOKEN...${NC}"
                env_set_value "$env_file" "FRP_AUTH_TOKEN" "$(gen_hex_secret 32)"
            elif [ "$var_name" = "TUNNEL_DOMAIN" ]; then
                echo -e "${BLUE}  -> Setting missing TUNNEL_DOMAIN...${NC}"
                env_set_value "$env_file" "TUNNEL_DOMAIN" "tunnel.localhost"
            else
                missing_vars+=("$var_name")
            fi
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
    if [ -n "$celery_broker_url" ] && [[ ! "$celery_broker_url" =~ ^amqp:// ]]; then
        invalid_vars+=("CELERY_BROKER_URL (must start with amqp://)")
    fi

    var_value="$(env_get_value "$env_file" "TUNNEL_DOMAIN")"
    if [ -n "$var_value" ] && [[ "$var_value" =~ [[:space:]] ]]; then
        invalid_vars+=("TUNNEL_DOMAIN (must not contain spaces)")
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
# The production compose file already includes socket-proxy and traefik.
# Do not layer docker-compose.socket-proxy.yml on top of it or Docker Compose
# will reject the config due to duplicate services.
ROLLBACK_NEEDED=false

# ─── Parse Arguments ─────────────────────────────────────────────────────────
UPDATE_MODE=""
WIPE_MODE="false"
RECOVER_MODE="false"
REFRESH_MODE="false"
DEBUG_MODE="false"
RUST_TWIN_MODE="false"

# Simple loop to parse multiple arguments like `--update --rust`
for arg in "$@"; do
    case "$arg" in
        --update)          UPDATE_MODE="full" ;;
        --update-frontend) UPDATE_MODE="frontend" ;;
        --update-backend)  UPDATE_MODE="backend" ;;
        --wipe)            WIPE_MODE="true" ;;
        --recover)         RECOVER_MODE="true" ;;
        --refresh)         REFRESH_MODE="true" ;;
        --debug)           DEBUG_MODE="true" ;;
        --verify)          VERIFY_MODE="true" ;;
        --rust)            RUST_TWIN_MODE="true" ;;
        --clear)           CLEAR_MODE="true" ;;
        --help|-h)
            echo "Usage: sudo bash install.sh [--rust] [--update|--update-frontend|--update-backend|--refresh|--recover|--debug|--wipe|--clear]"
            echo ""
            echo "  (no args)          Fresh install (Legacy Python)"
            echo "  --rust             Deploy the Next-Gen Rust Twin instead of Python"
            echo "  --update           Pull latest code and rebuild all services"
            echo "  --clear            Wipes stale addons and frees up docker resources"
            exit 0
            ;;
    esac
done

if [ "$RUST_TWIN_MODE" = "true" ]; then
    COMPOSE_FILE="rust_twin/docker-compose.yml"
fi


MODE_LABEL="fresh-install"
if [ -n "$UPDATE_MODE" ]; then
    MODE_LABEL="update-$UPDATE_MODE"
elif [ "$REFRESH_MODE" = "true" ]; then
    MODE_LABEL="refresh"
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

ensure_infrastructure_permissions() {
    local caddy_config_dir="/opt/smsly-hosting/caddy-config"
    local staticfiles_dir="/opt/smsly-hosting/backend/staticfiles"

    echo -e "${BLUE}  -> Ensuring infrastructure permissions...${NC}"

    # 1. Handle Bind-Mounts (Caddy Config & Staticfiles)
    mkdir -p "$caddy_config_dir"
    mkdir -p "$staticfiles_dir"

    # UID 1000 is the "smsly" user inside the containers.
    if id smsly >/dev/null 2>&1; then
        chown -R smsly:smsly "$caddy_config_dir" "$staticfiles_dir" 2>/dev/null || true
    else
        chown -R 1000:1000 "$caddy_config_dir" "$staticfiles_dir" 2>/dev/null || true
    fi

    chmod -R u+rwX,g+rwX "$caddy_config_dir" "$staticfiles_dir" 2>/dev/null || true
    find "$caddy_config_dir" -type d -exec chmod 2775 {} + 2>/dev/null || true
    find "$staticfiles_dir" -type d -exec chmod 2775 {} + 2>/dev/null || true

    # Caddy-specific file permissions
    [ -f "$caddy_config_dir/Caddyfile" ] && chmod 664 "$caddy_config_dir/Caddyfile" 2>/dev/null || true
    [ -f "$caddy_config_dir/.reload" ] && chmod 664 "$caddy_config_dir/.reload" 2>/dev/null || true

    # 2. Handle Named Volumes (repo_cache_data, backups_data)
    # We use a one-off container to safely chown existing named volumes.
    if command -v docker >/dev/null 2>&1; then
        for vol in repo_cache_data backups_data; do
            if docker volume inspect "$vol" >/dev/null 2>&1; then
                echo -e "${BLUE}     ↳ Setting permissions for volume: $vol...${NC}"
                docker run --rm -v "${vol}:/data" alpine chown -R 1000:1000 /data 2>/dev/null || true
            fi
        done
    fi

    # Fast write probe for Caddy
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

    # 2. Discover ALL deployed service domains from DB (public + custom)
    local svc_blocks=""
    svc_blocks="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
for svc in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain=''):
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
    echo -e "${YELLOW}  ⚠ Wildcard HTTPS disabled. Individual service domains have HTTP-01 SSL.${NC}"
}

# Returns 0 if Caddy config needs fixing, 1 if it's fine.
caddy_needs_fix() {
    # Export CF token from systemd override so caddy validate can resolve {env.CLOUDFLARE_API_TOKEN}
    if [ -f /etc/systemd/system/caddy.service.d/override.conf ]; then
        local cf_val
        cf_val="$(grep 'CLOUDFLARE_API_TOKEN=' /etc/systemd/system/caddy.service.d/override.conf 2>/dev/null | sed 's/.*CLOUDFLARE_API_TOKEN=//;s/"//g' || true)"
        if [ -n "$cf_val" ]; then
            export CLOUDFLARE_API_TOKEN="$cf_val"
        fi
    fi

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
    for svc in frontend backend celery celery-deploy celery-fast celery-beat; do
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
    
    # NEW: Prune old unused images older than 7 days to prevent disk space exhaustion.
    echo -e "${BLUE}  -> Pruning deeply stale images (>7 days old)...${NC}"
    docker image prune -a -f --filter "until=168h" >/dev/null 2>&1 || true
    
    echo -e "${GREEN}  OK Cache bust complete (targeted images + build cache + deep prune)${NC}"
}

restart_edge_stack() {
    local edge_services="socket-proxy traefik route-fallback nginx"

    echo -e "${BLUE}  -> Refreshing edge proxy stack (nginx/traefik/socket-proxy/route-fallback)...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps $edge_services >/dev/null 2>&1 || \
        docker compose -f "$COMPOSE_FILE" up -d --force-recreate $edge_services >/dev/null 2>&1 || true

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
        if systemctl is-active --quiet caddy 2>/dev/null; then
            systemctl reload caddy >/dev/null 2>&1 || true
        else
            systemctl restart caddy >/dev/null 2>&1 || true
        fi
        systemctl restart caddy-watcher >/dev/null 2>&1 || true
    fi
    echo -e "${GREEN}  OK Edge stack refreshed${NC}"
}

refresh_runtime_services() {
    # Ensure Docker mirror is configured (Option B)
    configure_docker_mirror

    local app_services_requested=(
        pgcat
        backend
        celery
        celery-deploy
        celery-fast
        celery-beat
        frontend
        frps
    )
    local edge_services_requested=(
        nginx
        socket-proxy
        route-fallback
        traefik
    )
    local app_services=()
    local edge_services=()
    local runtime_services=()
    local failed_services=()
    local svc=""
    local container_name=""
    local timeout_seconds=120

    echo -e "${BLUE}  -> Performing clean runtime refresh (non-data services only)...${NC}"
    ensure_update_networks
    ensure_infrastructure_permissions

    for svc in "${app_services_requested[@]}"; do
        if docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null | grep -qx "$svc"; then
            app_services+=("$svc")
        fi
    done

    for svc in "${edge_services_requested[@]}"; do
        if docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null | grep -qx "$svc"; then
            edge_services+=("$svc")
        fi
    done

    runtime_services=("${app_services[@]}" "${edge_services[@]}")

    if [ "${#runtime_services[@]}" -eq 0 ]; then
        echo -e "${YELLOW}  ⚠ No runtime services found to refresh${NC}"
        return 0
    fi

    if [ "${#app_services[@]}" -gt 0 ]; then
        docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "${app_services[@]}" >/dev/null 2>&1 || \
            docker compose -f "$COMPOSE_FILE" up -d --force-recreate "${app_services[@]}" >/dev/null 2>&1 || true
    fi

    ensure_container_on_network "smsly-net" "smsly-hosting-pgcat-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-backend-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-celery-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-celery-beat-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-celery-deploy-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-celery-fast-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-frontend-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-nginx-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-frps-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    for svc in "${app_services[@]}"; do
        container_name="smsly-hosting-${svc}-1"
        case "$svc" in
            backend|frontend)
                timeout_seconds=180
                ;;
            *)
                timeout_seconds=120
                ;;
        esac
        if ! wait_for_container_ready "$container_name" "$timeout_seconds"; then
            failed_services+=("$svc")
        fi
    done

    if [ "${#failed_services[@]}" -eq 0 ] && [ "${#edge_services[@]}" -gt 0 ]; then
        docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "${edge_services[@]}" >/dev/null 2>&1 || \
            docker compose -f "$COMPOSE_FILE" up -d --force-recreate "${edge_services[@]}" >/dev/null 2>&1 || true

        ensure_container_on_network "smsly-net" "smsly-hosting-nginx-1"
        ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
        ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
        ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
        ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

        for svc in "${edge_services[@]}"; do
            container_name="smsly-hosting-${svc}-1"
            if ! wait_for_container_ready "$container_name" 120; then
                failed_services+=("$svc")
            fi
        done
    fi

    if [ "${#failed_services[@]}" -gt 0 ]; then
        echo -e "${YELLOW}  WARN Runtime refresh left services unready: ${failed_services[*]}${NC}"
        docker compose -f "$COMPOSE_FILE" ps "${failed_services[@]}" 2>/dev/null || true
        docker compose -f "$COMPOSE_FILE" logs --tail=80 "${failed_services[@]}" 2>/dev/null || true
        return 1
    fi

    if systemctl is-active --quiet caddy 2>/dev/null; then
        systemctl reload caddy >/dev/null 2>&1 || true
    else
        systemctl restart caddy >/dev/null 2>&1 || true
    fi
    systemctl restart caddy-watcher >/dev/null 2>&1 || true
    systemctl restart smsly-autoscaler >/dev/null 2>&1 || true
    echo -e "${GREEN}  OK Clean runtime refresh complete${NC}"
}

safe_refresh_runtime_services() {
    if refresh_runtime_services; then
        return 0
    fi

    echo -e "${YELLOW}  -> Runtime refresh incomplete. Running one recovery pass...${NC}"
    recover_runtime_stack || true
    refresh_runtime_services
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
    ensure_infrastructure_permissions

    if systemctl list-unit-files docker.service >/dev/null 2>&1; then
        echo -e "${BLUE}    -> Restarting Docker daemon...${NC}"
        systemctl restart docker >/dev/null 2>&1 || true
        sleep 8
        ensure_update_networks
    fi

    echo -e "${BLUE}    -> Starting dependency services...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d db pgcat redis socket-proxy registry || true
    wait_for_container_ready "smsly-hosting-db-1" 120 || true
    wait_for_container_ready "smsly-hosting-pgcat-1" 120 || true
    wait_for_container_ready "smsly-hosting-redis-1" 120 || true

    if command -v caddy >/dev/null 2>&1; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "recover_runtime_stack"
        fi
    fi

    echo -e "${BLUE}    -> Refreshing runtime services...${NC}"
    if ! refresh_runtime_services; then
        echo -e "${YELLOW}  WARN Runtime recovery could not fully refresh all runtime services${NC}"
        return 1
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
    curl -iSsf http://127.0.0.1/health 2>/dev/null | head -20 || echo "http://127.0.0.1/health failed"
    echo ""

    echo "---- Backend DNS Checks ----"
    docker compose -f "$COMPOSE_FILE" exec -T backend getent hosts db pgcat redis 2>/dev/null || echo "backend DNS check failed"
    echo ""

    echo "---- Key Logs (tail 120) ----"
    docker compose -f "$COMPOSE_FILE" logs --tail=120 backend frontend nginx traefik pgcat redis 2>/dev/null || true
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
    RECOVER_STATUS=0
    recover_runtime_stack || RECOVER_STATUS=$?
    debug_platform_status
    exit "$RECOVER_STATUS"
fi

if [ "$REFRESH_MODE" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --refresh)${NC}"
        exit 1
    fi
    if [ ! -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
        echo -e "${RED}x Missing $INSTALL_DIR/$COMPOSE_FILE. Run fresh install first.${NC}"
        exit 1
    fi
    cd "$INSTALL_DIR"
    ensure_env_runtime_defaults "$INSTALL_DIR/.env" || true
    REFRESH_STATUS=0
    refresh_runtime_services || REFRESH_STATUS=$?
    debug_platform_status
    exit "$REFRESH_STATUS"
fi

# =============================================================================
# CLEAR MODE — Remove stale addons and cache
# =============================================================================
if [ "${CLEAR_MODE:-false}" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --clear)${NC}"
        exit 1
    fi
    echo -e "\n${BLUE}  🧹 Running Maintenance Clear...${NC}"

    # Prune unused docker resources
    echo -e "  → Pruning unused Docker containers and images..."
    docker container prune -f >/dev/null 2>&1
    docker image prune -af >/dev/null 2>&1

    # Stop and remove all stale smsly-addon-* containers (only those NOT running)
    echo -e "  → Removing stale/orphaned service addons (protecting active databases)..."
    ADDON_IDS=$(docker ps -a -q --filter "name=smsly-addon" --filter "status=exited" --filter "status=created" --filter "status=dead")
    if [ -n "$ADDON_IDS" ]; then
        docker rm -f $ADDON_IDS >/dev/null 2>&1 || true
        echo -e "${GREEN}  ✓ Removed inactive orphaned addon containers.${NC}"
    else
        echo -e "${YELLOW}  - No inactive orphaned addons found.${NC}"
    fi

    # Stop and remove all stale deployment/blue-green containers
    echo -e "  → Removing stale deployment containers (protecting active routes)..."
    GREEN_IDS=$(docker ps -a -q --filter "name=-green-" --filter "status=exited" --filter "status=created" --filter "status=dead")
    ROUTER_IDS=$(docker ps -a -q --filter "name=ai-router" --filter "status=exited" --filter "status=created" --filter "status=dead")

    if [ -n "$GREEN_IDS" ]; then
        docker rm -f $GREEN_IDS >/dev/null 2>&1 || true
        echo -e "${GREEN}  ✓ Removed inactive deployment containers.${NC}"
    fi
    if [ -n "$ROUTER_IDS" ]; then
        docker rm -f $ROUTER_IDS >/dev/null 2>&1 || true
        echo -e "${GREEN}  ✓ Removed inactive AI routers.${NC}"
    fi

    # Clean caches
    echo -e "  → Cleaning system caches..."
    rm -rf /opt/smsly-cache/* 2>/dev/null || true
    echo -e "${GREEN}  ✓ Cleared /opt/smsly-cache/.${NC}"

    echo -e "\n${GREEN}  ✨ Maintenance complete. You can now re-run deployments.${NC}"
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

    echo -e "\n${BLUE}  ⟳ Syncing Proxy Configurations...${NC}"
    if systemctl is-active --quiet caddy 2>/dev/null; then
        systemctl reload caddy >/dev/null 2>&1 || true
    else
        systemctl restart caddy >/dev/null 2>&1 || true
    fi
    systemctl restart caddy-watcher >/dev/null 2>&1 || true
    sleep 3

    echo -e "\n${BLUE}  → Running endpoint verification...${NC}"
    PASS_COUNT=0
    FAIL_COUNT=0

    # Backend health (internal)
    EP1_URL="http://127.0.0.1/health"
    EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP1_URL" 2>/dev/null) || EP1_CODE="000"
    case "$EP1_CODE" in
        2*|3*)
        echo -e "${GREEN}  ✓ Backend (local): HTTP $EP1_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
        ;;
    *)
        echo -e "${RED}  ✗ Backend (local): HTTP $EP1_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
        ;;
    esac

    # Platform domain (public-facing — tests Caddy → nginx → backend chain)
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ]; then
        EP_PUB_URL="http://${DOMAIN}/health"
        EP_PUB_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 10 "$EP_PUB_URL" 2>/dev/null) || EP_PUB_CODE="000"
        if [ "$EP_PUB_CODE" = "200" ] || [ "$EP_PUB_CODE" = "301" ] || [ "$EP_PUB_CODE" = "308" ]; then
            echo -e "${GREEN}  ✓ Platform (${DOMAIN}): HTTP $EP_PUB_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
        else
            echo -e "${RED}  ✗ Platform (${DOMAIN}): HTTP $EP_PUB_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    fi

    # HTTPS domain (skip for raw IP addresses — certs can't be issued for IPs)
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ] && ! echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EP2_URL="https://${DOMAIN}/health"
        EP2_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 10 "$EP2_URL" 2>/dev/null) || EP2_CODE="000"
        case "$EP2_CODE" in
            2*|3*)
            echo -e "${GREEN}  ✓ HTTPS: HTTP $EP2_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
            ;;
        *)
            echo -e "${RED}  ✗ HTTPS: HTTP $EP2_CODE ($EP2_URL)${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
            ;;
        esac
    elif echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' 2>/dev/null; then
        echo -e "${YELLOW}  ⊘ HTTPS: Skipped (IP Mode — SSL requires a domain name)${NC}"
    fi

    # Traefik
    EP3_URL="http://127.0.0.1:8081/"
    EP3_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP3_URL" 2>/dev/null) || EP3_CODE="000"
    if [ "$EP3_CODE" != "000" ] && [ "$EP3_CODE" != "502" ]; then
        echo -e "${GREEN}  ✓ Traefik: HTTP $EP3_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Traefik: HTTP $EP3_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    # Post-install smoke (HTTP/HTTPS/wildcard) if domain provided
    if [ -n "${DOMAIN:-}" ] && [ -x "/opt/smsly-hosting/scripts/smoke_routes.sh" ]; then
        echo -e "${YELLOW}  ⟳ Smoke-testing routes for ${DOMAIN}${NC}"
        /opt/smsly-hosting/scripts/smoke_routes.sh "$DOMAIN" "*.$DOMAIN" || true
    fi

    # Deployed service domains
    ALL_SVC_DOMAINS="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
for s in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain=''):
    print(f'{s.name}|{s.public_domain.strip()}')
" 2>/dev/null | tr -d '\r' || true)"

    if [ -n "$ALL_SVC_DOMAINS" ]; then
        while IFS='|' read -r svc_name svc_domain; do
            [ -z "$svc_domain" ] && continue
            svc_url="https://${svc_domain}/"
            svc_code=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$svc_url" 2>/dev/null) || svc_code="000"
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

    check_internet
    check_hardware

    ensure_infrastructure_permissions

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
    set_checkpoint "update_preflight_done"

if ! is_checkpoint_done "update_git_synced"; then


    # ─── Git Stash + Pull (CRITICAL BLINDSPOT FIX) ───────────────────────────
    echo -e "${BLUE}  → Checking for local changes...${NC}"
    if ! git diff --quiet HEAD 2>/dev/null || ! git diff --cached --quiet HEAD 2>/dev/null; then
        echo -e "${YELLOW}  ⚠ Local changes detected — stashing before pull${NC}"
        git stash push -m "install-update-$(date +%s)"
        touch "$INSTALL_DIR/.git-stash-marker"
    fi

    echo -e "${BLUE}  → Force-pulling latest code from GitHub ($SMSLY_BRANCH)...${NC}"
    
    # Track if git update succeeded
    GIT_UPDATE_OK=true
    
    if ! git fetch origin "$SMSLY_BRANCH" >/dev/null 2>&1; then
        echo -e "${YELLOW}  ⚠️ Git fetch failed for $SMSLY_BRANCH.${NC}"
        GIT_UPDATE_OK=false
    fi
    
    if [ "$GIT_UPDATE_OK" = "true" ]; then
        if ! git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" >/dev/null 2>&1; then
            echo -e "${YELLOW}  ⚠️ Git reset failed.${NC}"
            GIT_UPDATE_OK=false
        else
            git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
        fi
    fi

    # Fallback if git failed but a local bundle was provided
    if [ "$GIT_UPDATE_OK" = "false" ]; then
        if [ -n "${SMSLY_INSTALL_WORKDIR:-}" ] && [ -d "${SMSLY_INSTALL_WORKDIR}" ]; then
            echo -e "${BLUE}  → Fallback: Synchronizing from pre-uploaded source bundle...${NC}"
            # Use rsync if available, otherwise cp. Exclude .git to preserve local repo state if any.
            if command -v rsync >/dev/null 2>&1; then
                rsync -rtv --exclude='.git' "${SMSLY_INSTALL_WORKDIR}/" "$INSTALL_DIR/"
            else
                cp -rv "${SMSLY_INSTALL_WORKDIR}/"* "$INSTALL_DIR/" 2>/dev/null || true
            fi
            echo -e "${GREEN}  ✓ Fallback synchronization complete.${NC}"
        else
            echo -e "${RED}✗ Git update failed and no local fallback bundle available. Update may be incomplete.${NC}"
        fi
    fi
    set_checkpoint "update_git_synced"
fi

    # ─── Self-Update Check ──────────────────────────────────────────────────
    # If the installer itself was updated, we MUST re-execute it to pick up
    # new service names (e.g., celery-deploy) and self-healing logic.
    if [[ "${SMSLY_REEXEC:-}" != "1" ]]; then
        echo -e "${GREEN}  → Installer updated. Re-executing for safe synchronization...${NC}"
        export SMSLY_REEXEC=1
        exec bash "$SCRIPT_PATH" "$@"
    fi

    echo -e "${BLUE}  → Applying platform/domain overrides...${NC}"
    apply_env_platform_overrides "$INSTALL_DIR/.env"
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x .env validation failed after applying overrides. Fix the values and retry.${NC}"
        exit 1
    fi

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
    if [ "$DISK_AVAIL_MB" -lt 5000 ]; then
        echo -e "${YELLOW}  ⚠ Disk space low (${DISK_AVAIL_MB}MB). Running Docker prune...${NC}"
        docker container prune -f || true
        docker image prune -f || true # Only dangling images by default
        
        if [ "$DISK_AVAIL_MB" -lt 2000 ]; then
            echo -e "${RED}  ⚠ Disk space CRITICAL. Running aggressive prune...${NC}"
            docker image prune -af || true
            bust_core_build_cache
        fi
        
        DISK_AVAIL_MB=$(df -BM "$INSTALL_DIR" | tail -1 | awk '{print $4}' | tr -d 'M')
        echo -e "${BLUE}  → Disk space after cleanup: ${DISK_AVAIL_MB}MB${NC}"
        if [ "$DISK_AVAIL_MB" -lt 1000 ]; then
            echo -e "${RED}  ✗ Still insufficient disk space (${DISK_AVAIL_MB}MB). Need at least 1GB.${NC}"
            exit 1
        fi
    fi

    # ─── Targeted Rebuild (CRITICAL BLINDSPOT FIX: --no-deps) ────────────────
    # Using --no-deps prevents cascade restart of unrelated services
    if ! is_checkpoint_done "update_containers_rebuilt"; then

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
            echo -e "${BLUE}  → Rebuilding frontend container (cached)...${NC}"
            docker compose -f "$COMPOSE_FILE" build frontend
            docker compose -f "$COMPOSE_FILE" up -d --no-deps frontend
            ;;
        backend)
            echo -e "${BLUE}  → Rebuilding backend containers (cached)...${NC}"
            docker compose -f "$COMPOSE_FILE" build backend celery
            echo -e "${BLUE}  → Ensuring backend dependencies are running...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d db pgcat redis socket-proxy
            docker compose -f "$COMPOSE_FILE" up -d --no-deps backend

            echo -e "${BLUE}  → Running migrations...${NC}"
            sleep 10  # Wait for backend to start
            # Note: Do NOT run makemigrations here — migrations are committed in the repo.
            # Running makemigrations auto-generates files inside the container that conflict
            # with committed migrations on subsequent deploys.
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py migrate --noinput || {
                echo -e "${YELLOW}  ⚠ Migration failed — backend may still be starting. Retrying in 15s...${NC}"
                sleep 15
                docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py migrate --noinput
            }

            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput

            set_checkpoint "update_db_migrated"

            # Clean stale celerybeat-schedule (prevents Permission denied crash loop)
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true
