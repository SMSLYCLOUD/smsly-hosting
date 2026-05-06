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
