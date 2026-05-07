#!/bin/bash

# =============================================================================
# Grid by SMSLY - Universal Installer v3.2.4 (Production Hardened)
# VERSION: 2026-05-07-0219
# =============================================================================
# Supports: Ubuntu 20.04/22.04/24.04 LTS
# Modes:
#   1. IP Mode (HTTP :80)   - Quick start, no domain needed.
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

# ─── Root Check ─────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo -e "\033[0;31mERROR: This script must be run as root.\033[0m"
    echo -e "Please use: sudo bash $0 $*"
    exit 1
fi

# ─── Parse flags early ───────────────────────────────────────────────────────
NON_INTERACTIVE=false
MODE_AGENT_LITE=false
RESUME_MODE=false
RUST_TWIN_MODE="${RUST_TWIN_MODE:-false}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
NO_SCREEN="${NO_SCREEN:-false}"

case "${SKIP_SCREEN:-}" in
  1|true|TRUE|yes|YES|on|ON) NO_SCREEN=true ;;
esac

_EXPECT_MODE_VALUE=false
for arg in "$@"; do
  case "$arg" in
    --non-interactive) NON_INTERACTIVE=true ;;
    --mode=agent-lite|--agent-lite) MODE_AGENT_LITE=true ;;
    --mode)           _EXPECT_MODE_VALUE=true ;;
    agent-lite)
                       if [ "$_EXPECT_MODE_VALUE" = "true" ]; then MODE_AGENT_LITE=true; fi
                       _EXPECT_MODE_VALUE=false ;;
    --rust)            RUST_TWIN_MODE="true" ;;
    --resume)          RESUME_MODE=true ;;
    --no-screen|--skip-screen)
                       NO_SCREEN=true ;;
    --wipe)            NO_SCREEN=true; rm -f "/opt/smsly-hosting/.smsly_install_state" "/opt/smsly-hosting/.smsly_install_state.mode" ;;
    --recover|--refresh|--debug|--verify|--clear|--help|-h)
                       NO_SCREEN=true ;;
  esac
done
unset _EXPECT_MODE_VALUE

# ─── Resolve script path BEFORE any cd (screen guard needs absolute path) ────
SCRIPT_PATH="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

# ─── Screen Guard ────────────────────────────────────────────────────────────
# Protect against SSH disconnects by running inside a screen session.
if [ "${NO_SCREEN:-false}" != "true" ] && [ "$NON_INTERACTIVE" != "true" ] && [ -t 0 ] && [ -z "${STY:-}" ] && [[ "${TERM:-}" != screen* ]] && [ -z "${TMUX:-}" ]; then
    if command -v screen >/dev/null 2>&1; then
        echo -e "\033[0;34m  → Protecting session with 'screen' (safety against disconnects)...\033[0m"
        SCREEN_SESSION="${SMSLY_SCREEN_SESSION:-smsly-install-$$}"
        if screen -help 2>&1 | grep -q -- '-Logfile'; then
            exec screen -L -Logfile /var/log/smsly-screen.log -S "$SCREEN_SESSION" \
                bash -c 'bash "$0" --no-screen "$@"; rc=$?; echo; echo "Installer exited with code $rc."; echo "Press ENTER to close this screen."; read -r _; exit "$rc"' "$SCRIPT_PATH" "$@"
        else
            exec screen -L -S "$SCREEN_SESSION" \
                bash -c 'bash "$0" --no-screen "$@"; rc=$?; echo; echo "Installer exited with code $rc."; echo "Press ENTER to close this screen."; read -r _; exit "$rc"' "$SCRIPT_PATH" "$@"
        fi
    else
        echo -e "\033[1;33m  ⚠ Warning: 'screen' not found. Session NOT protected against disconnects.\033[0m"
        sleep 1
    fi
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
export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"
export NEEDRESTART_MODE="${NEEDRESTART_MODE:-a}"
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
        echo -e "${RED}  ✗ Insufficient RAM ($ram_mb MB). Grid requires at least 1GB.${NC}"
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

wait_for_apt_lock() {
    local lock_files=(
        "/var/lib/dpkg/lock-frontend"
        "/var/lib/dpkg/lock"
        "/var/cache/apt/archives/lock"
    )
    local max_wait="${SMSLY_APT_LOCK_TIMEOUT:-600}"
    local elapsed=0
    local lock_file
    local active_locks=()
    local pids
    local pid

    while true; do
        active_locks=()
        pids=""

        if command -v fuser >/dev/null 2>&1; then
            for lock_file in "${lock_files[@]}"; do
                [ -e "$lock_file" ] || continue
                if fuser "$lock_file" >/dev/null 2>&1; then
                    active_locks+=("$lock_file")
                    pids="$pids $(fuser "$lock_file" 2>/dev/null || true)"
                fi
            done
        fi

        if [ "${#active_locks[@]}" -eq 0 ]; then
            if [ "$elapsed" -gt 0 ]; then
                echo
                echo -e "${GREEN}  ✓ APT system ready${NC}"
            fi
            return 0
        fi

        if [ "$elapsed" -eq 0 ]; then
            echo -e "${BLUE}  → Checking for background system updates (APT lock)...${NC}"
        fi

        if [ "$elapsed" -ge "$max_wait" ]; then
            echo
            echo -e "${RED}  x APT lock is still held after ${max_wait}s.${NC}"
            echo -e "${YELLOW}  Holding process(es):${NC}"
            for pid in $(printf "%s\n" $pids | sort -u); do
                ps -p "$pid" -o pid=,comm=,etime=,args= 2>/dev/null || true
            done
            echo -e "${YELLOW}  Wait for those processes to finish, then rerun the installer.${NC}"
            echo -e "${YELLOW}  If no apt/dpkg processes are running, repair with: sudo dpkg --configure -a${NC}"
            return 1
        fi

        if [ $((elapsed % 30)) -eq 0 ]; then
            echo
            echo -e "${YELLOW}  Waiting for APT lock (${elapsed}s/${max_wait}s). Active lock(s): ${active_locks[*]}${NC}"
            for pid in $(printf "%s\n" $pids | sort -u); do
                ps -p "$pid" -o pid=,comm=,etime=,args= 2>/dev/null || true
            done
        else
            printf "."
        fi

        sleep 5
        elapsed=$((elapsed + 5))
    done
}

apt_run() {
    local max_attempts="${SMSLY_APT_ATTEMPTS:-6}"
    local attempt=1
    local output=""
    local rc=0

    while [ "$attempt" -le "$max_attempts" ]; do
        wait_for_apt_lock || return 1
        set +e
        output="$("$@" 2>&1)"
        rc=$?
        set -e

        if [ "$rc" -eq 0 ]; then
            [ -n "$output" ] && printf '%s\n' "$output"
            return 0
        fi

        if printf '%s\n' "$output" | grep -qiE 'Could not get lock|Unable to acquire.*lock|dpkg frontend lock|/var/lib/dpkg/lock|/var/cache/apt/archives/lock'; then
            echo -e "${YELLOW}  APT lock appeared during command; retrying ($attempt/$max_attempts)...${NC}"
            sleep $((attempt * 5))
            attempt=$((attempt + 1))
            continue
        fi

        printf '%s\n' "$output"
        return "$rc"
    done

    printf '%s\n' "$output"
    return "$rc"
}

ensure_system_swap() {
    echo -e "${BLUE}  → Ensuring system swap is sufficient (Target: 3x-4x RAM)...${NC}"
    local current_ram_mb
    current_ram_mb=$(free -m | awk '/^Mem:/{print $2}')

    # Strictly enforce 4x RAM target for maximum stability
    local target_swap_mb=$((current_ram_mb * 4))

    # Cap at 64GB max for sanity, but floor at 4x RAM for the user's requirement
    [ "$target_swap_mb" -gt 65536 ] && target_swap_mb=65536

    local current_swap_mb
    current_swap_mb=$(free -m | awk '/^Swap:/{print $2}')

    # Check for ACTIVE swap (sometimes free -m reports phantom swap from host)
    local active_swap_count
    active_swap_count=$(grep -c / /proc/swaps || echo 0)

    # If swap is insufficient or missing, provision it.
    if [ "$current_swap_mb" -lt "$target_swap_mb" ] || [ "$active_swap_count" -eq 0 ]; then
        local needed_mb=$target_swap_mb
        [ "$current_swap_mb" -gt 0 ] && [ "$active_swap_count" -gt 0 ] && needed_mb=$((target_swap_mb - current_swap_mb))

        echo -e "${BLUE}  → Provisioning ${needed_mb}MB local swap (RAM: ${current_ram_mb}MB, Target: 4x)...${NC}"
        local swapfile="/swapfile-smsly"

        # If the file already exists but is too small, we need to recreate it
        if [ -f "$swapfile" ]; then
            swapoff "$swapfile" 2>/dev/null || true
            rm -f "$swapfile"
            # Since we removed the old file, we need to create the full target amount
            needed_mb=$target_swap_mb
        fi

        fallocate -l ${needed_mb}M "$swapfile" 2>/dev/null || dd if=/dev/zero of="$swapfile" bs=1M count=$needed_mb status=none
        chmod 600 "$swapfile"
        mkswap "$swapfile" >/dev/null 2>&1
        swapon "$swapfile" 2>/dev/null || true
        # Make permanent (idempotent)
        if ! grep -q "$swapfile" /etc/fstab 2>/dev/null; then
            echo "$swapfile none swap sw 0 0" >> /etc/fstab
        fi
        echo -e "${GREEN}  ✓ Swap file created and activated (${needed_mb}MB)${NC}"
    else
        echo -e "${GREEN}  ✓ Swap already sufficient (${current_swap_mb}MB, >= 4x RAM)${NC}"
    fi
}

# ─── Installation State Machine ──────────────────────────────────────────────
STATE_FILE="/opt/smsly-hosting/.smsly_install_state"
STATE_MODE_FILE="${STATE_FILE}.mode"

install_flavor() {
    if [ "${RUST_TWIN_MODE:-false}" = "true" ]; then
        echo "rust"
    elif [ "${MODE_AGENT_LITE:-false}" = "true" ]; then
        echo "agent-lite"
    else
        echo "master"
    fi
}

sync_install_state_flavor() {
    local current_flavor
    local previous_flavor
    current_flavor="$(install_flavor)"
    mkdir -p "$(dirname "$STATE_FILE")"

    if [ "$RESUME_MODE" = "true" ] && [ -f "$STATE_FILE" ]; then
        previous_flavor="$(cat "$STATE_MODE_FILE" 2>/dev/null || echo "legacy")"
        if [ "$previous_flavor" != "$current_flavor" ]; then
            echo -e "${YELLOW}  -> Existing install checkpoints are for '$previous_flavor'; resetting state for '$current_flavor'.${NC}"
            rm -f "$STATE_FILE"
        fi
    fi

    printf '%s\n' "$current_flavor" > "$STATE_MODE_FILE"
}

set_checkpoint() {
    local name="$1"
    mkdir -p "$(dirname "$STATE_FILE")"
    printf '%s\n' "$(install_flavor)" > "$STATE_MODE_FILE"
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

sanitize_node_identifier() {
    local value="${1:-}"
    value="$(printf '%s' "$value" | tr -c 'A-Za-z0-9_.-' '-' | sed -E 's/^-+//; s/-+$//; s/-+/-/g' | cut -c1-96)"
    if [ -z "$value" ]; then
        value="$(hostname 2>/dev/null | tr -c 'A-Za-z0-9_.-' '-' | sed -E 's/^-+//; s/-+$//; s/-+/-/g' | cut -c1-96)"
    fi
    [ -n "$value" ] || value="agent"
    printf '%s' "$value"
}

env_append_csv_values() {
    local env_file="$1"
    local var_name="$2"
    shift 2

    python3 - "$env_file" "$var_name" "$@" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
key = sys.argv[2]
requested = [value.strip() for value in sys.argv[3:] if value.strip()]
prefix = f"{key}="

lines = env_path.read_text().splitlines() if env_path.exists() else []
updated = []
found = False
changed = False

for line in lines:
    if line.startswith(prefix):
        if not found:
            values = [value.strip() for value in line[len(prefix):].split(",") if value.strip()]
            seen = {value.lower() for value in values}
            for value in requested:
                if value.lower() not in seen:
                    values.append(value)
                    seen.add(value.lower())
                    changed = True
            updated.append(f"{key}={','.join(values)}")
            found = True
        else:
            changed = True
        continue
    updated.append(line)

if not found:
    updated.append(f"{key}={','.join(requested)}")
    changed = True

if changed:
    env_path.write_text("\n".join(updated) + "\n")

print("changed" if changed else "unchanged")
PY
}

sync_env_domain_allowlists() {
    local env_file="$1"
    local domain="${2:-}"
    local public_ip="${3:-}"
    local changed=false
    local result=""
    local allowed_hosts=("localhost" "127.0.0.1" "backend" "smsly-hosting-backend-1")
    local csrf_origins=("http://localhost:8090")
    local cors_origins=("http://localhost:8090")

    [ -f "$env_file" ] || return 0

    [ -n "$domain" ] || domain="$(env_get_value "$env_file" "DOMAIN")"
    [ -n "$public_ip" ] || public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"

    if [ -n "$domain" ]; then
        allowed_hosts+=("$domain")
        csrf_origins+=("https://${domain}" "http://${domain}")
        cors_origins+=("https://${domain}" "http://${domain}")
    fi

    if [ -n "$public_ip" ]; then
        allowed_hosts+=("$public_ip")
        csrf_origins+=("http://${public_ip}:8090" "http://${public_ip}")
        cors_origins+=("http://${public_ip}:8090" "http://${public_ip}")
    fi

    result="$(env_append_csv_values "$env_file" "ALLOWED_HOSTS" "${allowed_hosts[@]}")"
    [ "$result" = "changed" ] && changed=true
    result="$(env_append_csv_values "$env_file" "CSRF_TRUSTED_ORIGINS" "${csrf_origins[@]}")"
    [ "$result" = "changed" ] && changed=true
    result="$(env_append_csv_values "$env_file" "CORS_ALLOWED_ORIGINS" "${cors_origins[@]}")"
    [ "$result" = "changed" ] && changed=true

    if [ "$changed" = true ]; then
        echo -e "${GREEN}  ✓ Synced domain allowlists in .env${NC}"
    fi
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

apply_agent_lite_env_overrides() {
    local env_file="$1"

    [ "$MODE_AGENT_LITE" = "true" ] || return 0

    MASTER_IP="${MASTER_IP:?MASTER_IP is required for agent-lite mode}"
    MASTER_DB_USER="${MASTER_DB_USER:-smsly_admin}"
    MASTER_DB_PASSWORD="${MASTER_DB_PASSWORD:?MASTER_DB_PASSWORD is required for agent-lite mode}"
    MASTER_MQ_PASSWORD="${MASTER_MQ_PASSWORD:?MASTER_MQ_PASSWORD is required for agent-lite mode}"
    SMSLY_NODE_HOST="${SMSLY_NODE_HOST:-$(detect_public_ip 2>/dev/null || true)}"
    [ -n "$SMSLY_NODE_HOST" ] || SMSLY_NODE_HOST="$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo agent)"
    SMSLY_NODE_ID="${SMSLY_NODE_ID:-$SMSLY_NODE_HOST}"
    local node_slug
    node_slug="$(sanitize_node_identifier "$SMSLY_NODE_ID")"
    SMSLY_NODE_QUEUE="${SMSLY_NODE_QUEUE:-smsly-node-${node_slug}}"

    local redis_url="redis://${MASTER_IP}:6379/1"
    if [ -n "${MASTER_REDIS_PASSWORD:-}" ]; then
        redis_url="redis://:${MASTER_REDIS_PASSWORD}@${MASTER_IP}:6379/1"
    fi

    env_set_value "$env_file" "MODE" "agent"
    env_set_value "$env_file" "MASTER_IP" "$MASTER_IP"
    env_set_value "$env_file" "SMSLY_NODE_HOST" "$SMSLY_NODE_HOST"
    env_set_value "$env_file" "SMSLY_NODE_ID" "$SMSLY_NODE_ID"
    env_set_value "$env_file" "SMSLY_NODE_QUEUE" "$SMSLY_NODE_QUEUE"
    env_set_value "$env_file" "DATABASE_URL" "postgresql://${MASTER_DB_USER}:${MASTER_DB_PASSWORD}@${MASTER_IP}:5432/smsly_hosting"
    env_set_value "$env_file" "DIRECT_DATABASE_URL" "postgresql://${MASTER_DB_USER}:${MASTER_DB_PASSWORD}@${MASTER_IP}:5432/smsly_hosting"
    env_set_value "$env_file" "CELERY_BROKER_URL" "amqp://smsly_user:${MASTER_MQ_PASSWORD}@${MASTER_IP}:5672//"
    env_set_value "$env_file" "REDIS_URL" "$redis_url"
    if [ -n "${MASTER_GATEWAY_SECRET:-}" ]; then
        env_set_value "$env_file" "GATEWAY_SECRET" "$MASTER_GATEWAY_SECRET"
    fi
    env_set_value "$env_file" "SMSLY_DISABLE_LOCAL_SERVICES" "true"
    env_set_value "$env_file" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
    env_set_value "$env_file" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false"
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
    if command -v docker >/dev/null 2>&1 && [ -f "$env_file" ] && grep -q '^POSTGRES_PASSWORD=' "$env_file" 2>/dev/null; then
        docker compose -f "$COMPOSE_FILE" ps || true

        echo -e "\n${YELLOW}  -> Compose Logs (Last 50 lines):${NC}"
        docker compose -f "$COMPOSE_FILE" logs --tail=50 || true
    else
        echo -e "${YELLOW}  (Docker or .env not ready; skipping container logs)${NC}"
    fi

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
        # Protection: Do NOT allow an IP to overwrite a real domain unless forced or it's a fresh install.
        if echo "$desired_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
            if [ -n "$current_domain" ] && ! echo "$current_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
                echo -e "${YELLOW}  ⚠ WARNING: Attempted to overwrite domain ($current_domain) with IP ($desired_domain). Ignored to prevent lockout.${NC}"
                desired_domain="$current_domain"
            fi
        fi
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

    sync_env_domain_allowlists "$env_file" "$DOMAIN" "$PUBLIC_IP"

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
            -e SMSLY_DISABLE_STARTUP_TASKS=true \
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
        -e SMSLY_DISABLE_STARTUP_TASKS=true \
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
    env_ensure_var "$env_file" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false" "Keep AppConfig.ready side-effect free; installer/watchers sync edge config"
    env_ensure_var "$env_file" "PGCAT_ADMIN_PASSWORD" "$(gen_hex_secret 24)" "PgCat administration password (mandatory for 1.2+)"

    redis_password="$(env_get_value "$env_file" "REDIS_PASSWORD")"
    rabbitmq_password="$(env_get_value "$env_file" "RABBITMQ_PASSWORD")"
    postgres_password="$(env_get_value "$env_file" "POSTGRES_PASSWORD")"
    current_domain="$(env_get_value "$env_file" "DOMAIN")"
    current_public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"
    current_tunnel_domain="$(env_get_value "$env_file" "TUNNEL_DOMAIN")"

    sync_env_domain_allowlists "$env_file" "$current_domain" "$current_public_ip"

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

        # [EDGE NODE] Override for Lite Agent mode
        if [ "$MODE_AGENT_LITE" = "true" ] && [ -n "${MASTER_IP:-}" ]; then
            echo -e "${BLUE}  -> Configuring for Edge Node (Lite Agent) mode...${NC}"
            local db_user="${MASTER_DB_USER:-smsly_admin}"
            local db_pass="${MASTER_DB_PASSWORD:-$postgres_password}"
            local mq_pass="${MASTER_MQ_PASSWORD:-$rabbitmq_password}"

            # Use Master IP directly for external connections
            expected_database_url="postgresql://${db_user}:${db_pass}@${MASTER_IP}:5432/smsly_hosting"
            expected_direct_url="postgresql://${db_user}:${db_pass}@${MASTER_IP}:5432/smsly_hosting"
            expected_celery_broker_url="amqp://smsly_user:${mq_pass}@${MASTER_IP}:5672//"

            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            env_set_value "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url"
            env_set_value "$env_file" "CELERY_BROKER_URL" "$expected_celery_broker_url"

            # Sync local vars for consistent validation below
            current_database_url="$expected_database_url"
            current_celery_broker_url="$expected_celery_broker_url"
        fi

        # Migrate legacy @db:5432 URLs to @pgcat:5432
        if [[ "$current_database_url" =~ @db:5432 ]] && [ "$MODE_AGENT_LITE" != "true" ]; then
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

            # Ensure direct connection bypass for migrations exists
            local expected_direct_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
            env_ensure_var "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url" "Direct connection bypass for migrations"
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
        "PGCAT_ADMIN_PASSWORD"
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
            elif [ "$var_name" = "PGCAT_ADMIN_PASSWORD" ]; then
                echo -e "${BLUE}  -> Generating missing PGCAT_ADMIN_PASSWORD...${NC}"
                env_set_value "$env_file" "PGCAT_ADMIN_PASSWORD" "$(gen_hex_secret 32)"
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
LOCK_FILE="/tmp/smsly-install.lock"
# The production compose file already includes socket-proxy and traefik.
# Do not layer docker-compose.socket-proxy.yml on top of it or Docker Compose
# will reject the config due to duplicate services.
ROLLBACK_NEEDED=false
CADDY_LAST_GOOD="/etc/caddy/Caddyfile.smsly-last-good"

acquire_install_lock() {
    if command -v flock >/dev/null 2>&1; then
        exec 9<>"$LOCK_FILE"
        if ! flock -n 9; then
            local pid
            pid="$(cat "$LOCK_FILE" 2>/dev/null || true)"
            echo -e "${RED}ERROR: Another installer instance${pid:+ (PID $pid)} is already running.${NC}"
            echo -e "If you are sure no other instance is running, remove $LOCK_FILE and try again."
            exit 1
        fi
        : > "$LOCK_FILE"
        echo "$$" > "$LOCK_FILE"
    else
        if [ -f "$LOCK_FILE" ]; then
            local pid
            pid="$(cat "$LOCK_FILE" 2>/dev/null || true)"
            if [ "$pid" != "$$" ] && kill -0 "$pid" 2>/dev/null; then
                echo -e "${RED}ERROR: Another installer instance (PID $pid) is already running.${NC}"
                echo -e "If you are sure no other instance is running, remove $LOCK_FILE and try again."
                exit 1
            fi
        fi
        echo "$$" > "$LOCK_FILE"
    fi
}

release_install_lock() {
    if command -v flock >/dev/null 2>&1; then
        flock -u 9 2>/dev/null || true
        exec 9>&- 2>/dev/null || true
    fi
    rm -f "$LOCK_FILE" 2>/dev/null || true
}

get_migration_database_alias() {
    local migrate_db
    migrate_db="$(
        docker compose -f "$COMPOSE_FILE" run --rm --no-deps -T \
            -e SMSLY_DISABLE_STARTUP_TASKS=true \
            backend python manage.py shell -c \
            "from django.conf import settings; print('direct' if 'direct' in settings.DATABASES else ('session' if 'session' in settings.DATABASES else 'default'))" \
            2>/dev/null | tail -n 1 | tr -d '\r'
    )"

    case "$migrate_db" in
        direct|session|default) printf '%s\n' "$migrate_db" ;;
        *) printf '%s\n' "default" ;;
    esac
}

diagnose_migration_locks() {
    local env_file="${INSTALL_DIR:-.}/.env"
    [ -f "$env_file" ] && source "$env_file" 2>/dev/null || true

    echo -e "${YELLOW}  -> PostgreSQL activity snapshot (lock diagnosis):${NC}"
    docker compose -f "$COMPOSE_FILE" exec -T \
        -e PGPASSWORD="${POSTGRES_PASSWORD:-}" \
        db psql \
            -U "${POSTGRES_USER:-smsly_admin}" \
            -d "${POSTGRES_DB:-smsly_hosting}" \
            -v ON_ERROR_STOP=1 \
            -P pager=off \
            -c "SELECT pid, usename, application_name, state, wait_event_type, wait_event, now() - COALESCE(xact_start, query_start) AS age, left(regexp_replace(query, '\s+', ' ', 'g'), 180) AS query FROM pg_stat_activity WHERE datname = current_database() ORDER BY COALESCE(xact_start, query_start) NULLS LAST LIMIT 20;" \
        2>/dev/null || echo -e "${YELLOW}  -> Could not read pg_stat_activity.${NC}"
}

run_backend_migrations() {
    local user_args=()
    if [ "${1:-}" = "--root" ]; then
        user_args=(--user root)
    fi

    local migrate_db timeout_seconds rc
    migrate_db="$(get_migration_database_alias)"
    timeout_seconds="${MIGRATION_TIMEOUT_SECONDS:-900}"
    echo -e "${BLUE}  -> Migration database: ${migrate_db}${NC}"
    set +e
    docker compose -f "$COMPOSE_FILE" run --rm --no-deps -T \
        "${user_args[@]}" \
        -e SMSLY_DISABLE_STARTUP_TASKS=true \
        -e SMSLY_MIGRATION_MODE=true \
        backend timeout "$timeout_seconds" \
        python manage.py migrate --database="$migrate_db" --noinput
    rc=$?
    set -e
    if [ "$rc" -ne 0 ]; then
        if [ "$rc" -eq 124 ]; then
            echo -e "${RED}  x Migrations timed out after ${timeout_seconds}s.${NC}"
        else
            echo -e "${RED}  x Migrations exited with status ${rc}.${NC}"
        fi
        diagnose_migration_locks
        return "$rc"
    fi
    return 0
}

export_caddy_cloudflare_env() {
    local override_file="/etc/systemd/system/caddy.service.d/override.conf"
    local cf_val=""

    if [ -f "$override_file" ]; then
        cf_val="$(grep 'CLOUDFLARE_API_TOKEN=' "$override_file" 2>/dev/null | sed 's/.*CLOUDFLARE_API_TOKEN=//;s/"//g' || true)"
    fi
    if [ -n "$cf_val" ]; then
        export CLOUDFLARE_API_TOKEN="$cf_val"
    fi
}

restore_last_good_caddy() {
    if [ ! -f "$CADDY_LAST_GOOD" ]; then
        return 1
    fi
    echo -e "${YELLOW}  -> Restoring last known-good Caddyfile${NC}"
    cp "$CADDY_LAST_GOOD" /etc/caddy/Caddyfile
    if systemctl is-active --quiet caddy 2>/dev/null; then
        systemctl reload caddy >/dev/null 2>&1 || systemctl restart caddy >/dev/null 2>&1 || true
    else
        systemctl restart caddy >/dev/null 2>&1 || true
    fi
    sync_active_caddyfile_to_shared /etc/caddy/Caddyfile 2>/dev/null || true
}

reload_caddy_preserving_previous() {
    local previous_file="${1:-}"

    if systemctl is-active --quiet caddy 2>/dev/null; then
        systemctl reload caddy >/dev/null 2>&1 || systemctl restart caddy >/dev/null 2>&1
    else
        systemctl restart caddy >/dev/null 2>&1
    fi

    if systemctl is-active --quiet caddy 2>/dev/null; then
        [ -f /etc/caddy/Caddyfile ] && cp /etc/caddy/Caddyfile "$CADDY_LAST_GOOD" 2>/dev/null || true
        sync_active_caddyfile_to_shared /etc/caddy/Caddyfile 2>/dev/null || true
        return 0
    fi

    echo -e "${YELLOW}  WARN Caddy reload failed. Restoring previous config...${NC}"
    if [ -n "$previous_file" ] && [ -f "$previous_file" ]; then
        cp "$previous_file" /etc/caddy/Caddyfile
    else
        restore_last_good_caddy || true
    fi

    if systemctl is-active --quiet caddy 2>/dev/null; then
        systemctl reload caddy >/dev/null 2>&1 || systemctl restart caddy >/dev/null 2>&1 || true
    else
        systemctl restart caddy >/dev/null 2>&1 || true
    fi

    if systemctl is-active --quiet caddy 2>/dev/null; then
        [ -f /etc/caddy/Caddyfile ] && cp /etc/caddy/Caddyfile "$CADDY_LAST_GOOD" 2>/dev/null || true
        sync_active_caddyfile_to_shared /etc/caddy/Caddyfile 2>/dev/null || true
        return 0
    fi
    return 1
}

sync_active_caddyfile_to_shared() {
    local source="${1:-/etc/caddy/Caddyfile}"
    local shared_dir="${INSTALL_DIR:-/opt/smsly-hosting}/caddy-config"
    local shared_file="$shared_dir/Caddyfile"

    [ -f "$source" ] || return 0

    mkdir -p "$shared_dir" 2>/dev/null || true
    install -m 0664 "$source" "$shared_file" 2>/dev/null || cp "$source" "$shared_file" 2>/dev/null || true
    if id smsly >/dev/null 2>&1; then
        chown smsly:smsly "$shared_file" 2>/dev/null || true
    else
        chown 1000:1000 "$shared_file" 2>/dev/null || true
    fi

    # Prevent the watcher from immediately replaying a stale pending reload.
    rm -f "$shared_dir/.reload" 2>/dev/null || true
}

install_caddyfile_atomically() {
    local candidate="$1"
    local label="${2:-Caddyfile}"
    local previous="/etc/caddy/Caddyfile.prev.$$"

    if [ ! -f "$candidate" ]; then
        echo -e "${YELLOW}  WARN $label candidate missing: $candidate${NC}"
        return 1
    fi

    mkdir -p /etc/caddy
    [ -f /etc/caddy/Caddyfile ] && cp /etc/caddy/Caddyfile "$previous" 2>/dev/null || true
    export_caddy_cloudflare_env

    if command -v caddy >/dev/null 2>&1; then
        caddy fmt --overwrite "$candidate" >/dev/null 2>&1 || true
        if ! caddy validate --config "$candidate" >/dev/null 2>&1; then
            echo -e "${YELLOW}  WARN $label candidate failed validation. Keeping current Caddyfile.${NC}"
            rm -f "$previous"
            return 1
        fi
    fi

    install -m 0644 "$candidate" /etc/caddy/Caddyfile
    if reload_caddy_preserving_previous "$previous"; then
        [ -f /etc/caddy/Caddyfile ] && cp /etc/caddy/Caddyfile "$CADDY_LAST_GOOD" 2>/dev/null || true
        sync_active_caddyfile_to_shared /etc/caddy/Caddyfile
        rm -f "$previous"
        return 0
    fi

    rm -f "$previous"
    echo -e "${YELLOW}  WARN $label was not applied because Caddy could not reload safely.${NC}"
    return 1
}

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
        --mode=agent-lite|--agent-lite) MODE_AGENT_LITE="true" ;;
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

if [ "$MODE_AGENT_LITE" = "true" ]; then
    COMPOSE_FILE="infrastructure/docker/docker-compose.agent-lite.yml"
fi

if [ "$RUST_TWIN_MODE" = "true" ]; then
    COMPOSE_FILE="rust_twin/docker-compose.yml"
fi


MODE_LABEL="fresh-install"
if [ "$MODE_AGENT_LITE" = "true" ]; then
    MODE_LABEL="agent-lite-install"
fi
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
acquire_install_lock
trap 'release_install_lock' EXIT
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  SMSLY Hosting Install Log — $(date -Iseconds)"
echo "  Mode: $MODE_LABEL"
sync_install_state_flavor
echo "═══════════════════════════════════════════════════════════"

# ─── Rollback Trap ──────────────────────────────────────────────────────────
cleanup_on_failure() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        echo -e "\n${RED}════════════════════════════════════════════════════════════${NC}"
        echo -e "${RED}  INSTALLATION FAILED (exit code: $exit_code)${NC}"
        echo -e "${RED}════════════════════════════════════════════════════════════${NC}"

        # Capture diagnostics BEFORE rollback deletes the containers
        if [ -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
            cd "$INSTALL_DIR" 2>/dev/null || true
            dump_diagnostic_logs "$INSTALL_DIR/.env" || true
        fi

        echo -e "${YELLOW}  → Rolling back...${NC}"

        restore_last_good_caddy >/dev/null 2>&1 || true

        # Do not tear down a live platform on install/update failure by default.
        # Use SMSLY_ROLLBACK_DOWN=true only for intentionally destructive lab runs.
        if [ "${SMSLY_ROLLBACK_DOWN:-false}" = "true" ] && [ -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
            cd "$INSTALL_DIR" 2>/dev/null || true
            docker compose -f "$COMPOSE_FILE" down 2>/dev/null || true
        else
            echo -e "${YELLOW}  Runtime containers left running to avoid avoidable downtime.${NC}"
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

        # Keep screen session open for inspection if it failed
        if [ -n "${STY:-}" ]; then
            echo -e "\n${YELLOW}  [GUARD] Installation failed inside a screen session.${NC}"
            echo -e "${YELLOW}  Screen session will remain open for debugging.${NC}"
            echo -e "${YELLOW}  Type 'exit' to close this window.${NC}"
            release_install_lock
            # Re-exec bash to prevent screen from closing
            exec bash
        fi
    fi
    release_install_lock
}
trap cleanup_on_failure EXIT

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   Grid - Production Installer v3.1${NC}"
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
    release_install_lock
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
    local candidate="/etc/caddy/Caddyfile.safe.$$"
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
    cat > "$candidate" <<SAFECADDY
# Auto-generated safe fallback (reason: $reason)
{
    on_demand_tls {
        ask http://localhost:8090/api/v1/services/check-domain/
    }
}

${domain} {
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

:443 {
    tls {
        on_demand
    }
    reverse_proxy localhost:8090
}

:80 {
    reverse_proxy localhost:8090
}

${svc_blocks}
SAFECADDY
    if install_caddyfile_atomically "$candidate" "safe fallback Caddyfile"; then
        rm -f "$candidate"
        echo -e "${YELLOW}  Safe fallback Caddyfile applied.${NC}"
        return 0
    fi
    rm -f "$candidate"
    return 1
}

# Returns 0 if Caddy config needs fixing, 1 if it's fine.
caddy_needs_fix() {
    # Export CF token from systemd override so caddy validate can resolve {env.CLOUDFLARE_API_TOKEN}
    export_caddy_cloudflare_env

    if ! caddy validate --config /etc/caddy/Caddyfile 2>/dev/null; then
        return 0  # Syntax error
    fi
    if grep -q 'dns cloudflare' /etc/caddy/Caddyfile 2>/dev/null \
       && [ ! -f /etc/systemd/system/caddy.service.d/override.conf ]; then
        return 0  # dns cloudflare without token override = runtime crash
    fi
    return 1  # Config is fine
}

is_real_domain_name() {
    local host="${1:-}"
    [ -n "$host" ] \
        && [ "$host" != "localhost" ] \
        && ! echo "$host" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'
}

https_listener_active() {
    if command -v ss >/dev/null 2>&1; then
        ss -H -tln 2>/dev/null | awk '{print $4}' | grep -Eq ':443$'
    else
        lsof -iTCP:443 -sTCP:LISTEN >/dev/null 2>&1
    fi
}

ensure_caddy_https_listener() {
    local domain="${1:-}"
    local reason="${2:-https listener check}"
    local watcher_was_active="false"

    is_real_domain_name "$domain" || return 0

    if https_listener_active; then
        return 0
    fi

    echo -e "${YELLOW}  ⚠ Caddy is not listening on TCP 443 for ${domain}; applying HTTPS fallback config...${NC}"
    if systemctl is-active --quiet caddy-watcher 2>/dev/null; then
        watcher_was_active="true"
        systemctl stop caddy-watcher >/dev/null 2>&1 || true
    fi
    generate_safe_caddyfile "$reason: missing tcp/443 listener" || true
    sync_active_caddyfile_to_shared /etc/caddy/Caddyfile
    systemctl reset-failed caddy >/dev/null 2>&1 || true
    systemctl restart caddy >/dev/null 2>&1 || true
    sleep 3

    if https_listener_active; then
        echo -e "${GREEN}  ✓ Caddy HTTPS listener is active on TCP 443${NC}"
        [ "$watcher_was_active" = "true" ] && systemctl start caddy-watcher >/dev/null 2>&1 || true
        return 0
    fi

    echo -e "${YELLOW}  ⚠ TCP 443 is still not listening. Check: ss -tlnp | grep ':443' && journalctl -u caddy --no-pager -n 30${NC}"
    echo -e "${YELLOW}  ↳ Current Caddy listeners:${NC}"
    ss -H -tlnp 2>/dev/null | grep -E ':(80|443)\b' || true
    echo -e "${YELLOW}  ↳ Active /etc/caddy/Caddyfile:${NC}"
    sed -n '1,140p' /etc/caddy/Caddyfile 2>/dev/null || true
    echo -e "${YELLOW}  ↳ Recent Caddy journal:${NC}"
    journalctl -u caddy --no-pager -n 40 2>/dev/null || true
    [ "$watcher_was_active" = "true" ] && systemctl start caddy-watcher >/dev/null 2>&1 || true
    return 1
}

restart_caddy_watcher_safely() {
    local domain="${1:-}"
    local reason="${2:-caddy-watcher restart}"
    local shared_dir="${INSTALL_DIR:-/opt/smsly-hosting}/caddy-config"

    if [ -z "$domain" ] && [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
        domain="$(env_get_value "${INSTALL_DIR:-/opt/smsly-hosting}/.env" "DOMAIN" 2>/dev/null || true)"
    fi

    # The watcher consumes the shared Caddyfile only when .reload exists. Before
    # installer-driven restarts, mirror the active known-good config and clear
    # stale reload flags so a restart cannot replay an older :80-only file.
    sync_active_caddyfile_to_shared /etc/caddy/Caddyfile
    rm -f "$shared_dir/.reload" 2>/dev/null || true

    systemctl cat caddy-watcher >/dev/null 2>&1 || return 0
    systemctl restart caddy-watcher >/dev/null 2>&1 || systemctl start caddy-watcher >/dev/null 2>&1 || true
    sleep 2

    ensure_caddy_https_listener "$domain" "$reason" || true
    sync_active_caddyfile_to_shared /etc/caddy/Caddyfile
}

install_caddy_health_guard() {
    local domain="${1:-}"
    local guard_script="${INSTALL_DIR:-/opt/smsly-hosting}/scripts/caddy-health-guard.sh"
    local dropin_dir="/etc/systemd/system/caddy.service.d"

    [ -f "$guard_script" ] || return 0

    chmod +x "$guard_script" 2>/dev/null || true
    mkdir -p "$dropin_dir"
    cat > "$dropin_dir/10-smsly-reliability.conf" <<'CADDYRELIABILITY'
[Unit]
StartLimitIntervalSec=0

[Service]
Restart=always
RestartSec=3
CADDYRELIABILITY

    cat > /etc/systemd/system/smsly-caddy-health-guard.service <<GUARDSERVICE
[Unit]
Description=SMSLY Caddy Health Guard
After=network-online.target caddy.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$guard_script ${INSTALL_DIR:-/opt/smsly-hosting}
GUARDSERVICE

    cat > /etc/systemd/system/smsly-caddy-health-guard.timer <<'GUARDTIMER'
[Unit]
Description=Run SMSLY Caddy Health Guard

[Timer]
OnBootSec=45s
OnUnitActiveSec=30s
AccuracySec=5s
Unit=smsly-caddy-health-guard.service

[Install]
WantedBy=timers.target
GUARDTIMER

    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl enable --now smsly-caddy-health-guard.timer >/dev/null 2>&1 || true
    systemctl start smsly-caddy-health-guard.service >/dev/null 2>&1 || true
    ensure_caddy_https_listener "$domain" "Caddy health guard installation" || true
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
    docker compose -f "$COMPOSE_FILE" up -d --no-deps $edge_services >/dev/null 2>&1 || \
        docker compose -f "$COMPOSE_FILE" up -d $edge_services >/dev/null 2>&1 || true

    # Re-attach expected external networks (idempotent).
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-nginx-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -t >/dev/null 2>&1 && \
        docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -s reload >/dev/null 2>&1 || true

    # Validate Caddy config before restart (H1 fix)
    if command -v caddy >/dev/null 2>&1; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "restart_edge_stack validation"
        fi
        reload_caddy_preserving_previous "" >/dev/null 2>&1 || true
        restart_caddy_watcher_safely "${DOMAIN:-}" "restart_edge_stack Caddy watcher verification"
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
        docker compose -f "$COMPOSE_FILE" up -d --no-deps "${app_services[@]}" >/dev/null 2>&1 || \
            docker compose -f "$COMPOSE_FILE" up -d "${app_services[@]}" >/dev/null 2>&1 || true
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
        docker compose -f "$COMPOSE_FILE" up -d --no-deps "${edge_services[@]}" >/dev/null 2>&1 || \
            docker compose -f "$COMPOSE_FILE" up -d "${edge_services[@]}" >/dev/null 2>&1 || true

        ensure_container_on_network "smsly-net" "smsly-hosting-nginx-1"
        ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
        ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
        ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
        ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

        docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -t >/dev/null 2>&1 && \
            docker compose -f "$COMPOSE_FILE" exec -T nginx nginx -s reload >/dev/null 2>&1 || true

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

    install_caddy_health_guard "${DOMAIN:-}"
    reload_caddy_preserving_previous "" >/dev/null 2>&1 || true
    restart_caddy_watcher_safely "${DOMAIN:-}" "runtime refresh Caddy watcher verification"
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
    curl -iSsf http://127.0.0.1:8090/health 2>/dev/null | head -20 || echo "http://127.0.0.1:8090/health failed"
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
    reload_caddy_preserving_previous "" >/dev/null 2>&1 || true
    install_caddy_health_guard "$DOMAIN"
    restart_caddy_watcher_safely "$DOMAIN" "verify mode Caddy watcher verification"
    ensure_caddy_https_listener "$DOMAIN" "verify mode Caddy verification" || true
    sleep 3

    echo -e "\n${BLUE}  → Running endpoint verification...${NC}"
    PASS_COUNT=0
    FAIL_COUNT=0

    # Backend health (internal)
    EP1_URL="http://127.0.0.1:8090/health"
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
    ensure_system_swap

    # ─── Git Safety ──────────────────────────────────────────────────────────
    # Prevents "dubious ownership" errors on production VPS
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true

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
        export NO_SCREEN=true
        export SKIP_SCREEN=1
        release_install_lock
        exec bash "$SCRIPT_PATH" --no-screen "$@"
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
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — backend may still be starting. Retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput

            set_checkpoint "update_db_migrated"

            # Clean stale celerybeat-schedule (prevents Permission denied crash loop)
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true

            echo -e "${BLUE}  → Restarting celery workers...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d --no-deps celery celery-deploy celery-fast celery-beat
            ;;
        full)
            echo -e "${BLUE}  → [FULL REBUILD] Rebuilding PaaS core (preserving addon databases)...${NC}"

            # 1. Only stop PaaS core services — NEVER touch addon containers
            CORE_SERVICES="frontend backend celery celery-deploy celery-fast celery-beat"

            # 2. Remove old PaaS images (NOT addon images) to free up space BEFORE the build
            # We untag them so docker compose build has to make new ones. Running containers keep the actual image data alive.
            echo -e "${BLUE}    ↳ Untagging old core images...${NC}"
            for svc in $CORE_SERVICES; do
                img=$(docker compose -f "$COMPOSE_FILE" config --images 2>/dev/null | grep -i "$svc" || true)
                if [ -n "$img" ]; then
                    docker rmi "$img" 2>/dev/null || true
                fi
            done

            # 3. Prune dangling build cache
            echo -e "${BLUE}    ↳ Pruning build cache...${NC}"
            docker builder prune -af 2>/dev/null || true

            # 4. Ensure shared networks exist (create if missing, don't destroy)
            echo -e "${BLUE}    ↳ Ensuring networks exist...${NC}"
            ensure_update_networks

            # 5. Rebuild core images (CACHED unless --no-cache passed manually)
            echo -e "${BLUE}    ↳ Rebuilding core images...${NC}"
            docker compose -f "$COMPOSE_FILE" build $CORE_SERVICES

            # 6. Start everything (addons stay running, core gets fresh containers)
            # This does a graceful zero-downtime replacement instead of an explicit hard stop
            echo -e "${BLUE}    ↳ Starting all services...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d --no-deps $CORE_SERVICES

            # 7. Reconnect Traefik + socket-proxy to smsly-proxy network
            #    (recreation drops Docker DNS links — causes 502 gateway errors)
            echo -e "${BLUE}    ↳ Reconnecting proxy network...${NC}"
            for ctr in smsly-hosting-traefik-1 smsly-hosting-socket-proxy-1; do
                ensure_container_on_network "smsly-proxy" "$ctr"
            done
            docker restart smsly-hosting-traefik-1 2>/dev/null || true

            # 8. Run migrations
            echo -e "${BLUE}  → Running migrations...${NC}"
            echo -e "${BLUE}  → Ensuring backend dependencies are running...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d db pgcat redis socket-proxy
            sleep 10
            # Note: Do NOT run makemigrations — migrations are committed in the repo.
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — backend may still be starting. Retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput

            # 9. Clean celerybeat-schedule and restart beat
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true
            docker compose -f "$COMPOSE_FILE" restart celery-beat celery-deploy celery-fast 2>/dev/null || true
            set_checkpoint "update_db_migrated"
            ;;
    esac
    set_checkpoint "update_containers_rebuilt"
fi

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
    # ─── Self-Healing: Docker Socket Permissions ──────────────────────────────
    echo -e "${BLUE}  → Hardening Docker socket permissions...${NC}"
    chmod 666 /var/run/docker.sock 2>/dev/null || true
    if ! groups smsly 2>/dev/null | grep -q "docker"; then
        usermod -aG docker smsly 2>/dev/null || true
    fi

    # ─── Self-Healing: Cleanup Stale Resources ──────────────────────────────
    echo -e "${BLUE}  → Pruning stale deployment containers and BuildKit caches...${NC}"
    # Prune orphaned containers created by the deployment system (labeled)
    docker container prune -f --filter "label=com.smsly.managed=true" --filter "status=created" 2>/dev/null || true
    docker container prune -f --filter "label=com.docker.compose.project" --filter "status=exited" 2>/dev/null || true
    # Prune BuildKit build cache (saves significant disk space)
    docker builder prune -f --filter "until=24h" 2>/dev/null || true

    # ─── Self-Healing: Automatic Queue Restoration ──────────────────────────
    echo -e "${BLUE}  → Checking for stalled deployments/addons in QUEUED state...${NC}"
    docker exec -i smsly-hosting-backend-1 python manage.py shell -c "
from apps.deployments.models import Deployment
from apps.deployments.models_addons import Addon
from apps.deployments.tasks import smart_deploy_task, provision_addon_task
from django.db.models import Count

# Re-queue deployments
q_count = Deployment.objects.filter(status='QUEUED').count()
if q_count > 0:
    print(f'  [Jump-Start] Re-queueing {q_count} stalled deployments...')
    for d in Deployment.objects.filter(status='QUEUED'):
        smart_deploy_task.delay(str(d.id), str(d.service.provider.id))

# Re-queue addons
a_count = Addon.objects.filter(status='QUEUED').count()
if a_count > 0:
    print(f'  [Jump-Start] Re-queueing {a_count} stalled addons...')
    for a in Addon.objects.filter(status='QUEUED'):
        provision_addon_task.delay(str(a.id))
" 2>/dev/null || true

    # ─── Verification: Celery Worker Health ─────────────────────────────────
    echo -e "${BLUE}  → Verifying worker connectivity and queue bindings...${NC}"
    # Give workers a moment to connect to Redis and report active queues
    sleep 15
    DEPLOY_WORKER_HEALTH="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' smsly-hosting-celery-deploy-1 2>/dev/null || echo "")"
    if docker exec -i smsly-hosting-celery-deploy-1 celery -A config inspect active_queues --timeout=10 2>/dev/null | grep -q "deploy"; then
        echo -e "${GREEN}  ✓ Deployment worker successfully bound to 'deploy' queue${NC}"
    elif [ "$DEPLOY_WORKER_HEALTH" = "healthy" ]; then
        echo -e "${GREEN}  ✓ Deployment worker container is healthy (queue inspect timed out)${NC}"
    else
        echo -e "${YELLOW}  ⚠ WARNING: Deployment worker not detected on 'deploy' queue. Check logs.${NC}"
    fi

    echo -e "\n${GREEN}  ✨ Update complete. Self-healing applied.${NC}"

    sync_platform_domain_state "$INSTALL_DIR/.env"

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

    # ─── Caddy: Regenerate Caddyfile with service domains (writes directly to host) ──
    if command -v caddy &> /dev/null; then
        echo -e "${BLUE}  → Regenerating Caddyfile with current service domains...${NC}"

        # ── Step 1: Find the Cloudflare token FIRST (before generating Caddyfile) ──
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

        # ── Step 2: Generate Caddyfile WITH dns cloudflare if token exists ──
        if [ -n "$CF_TOKEN" ] && [ "$CF_TOKEN" != "fake" ]; then
            echo -e "${GREEN}  ✓ Cloudflare token available — generating Caddyfile with wildcard SSL${NC}"

            # Ensure systemd override is set
            mkdir -p "$CADDY_OVERRIDE_DIR"
            cat > "$CADDY_OVERRIDE_FILE" <<ENVEOF
[Service]
ExecStart=
ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile
Environment="CLOUDFLARE_API_TOKEN=$CF_TOKEN"
ENVEOF
            chmod 600 "$CADDY_OVERRIDE_FILE"
            systemctl daemon-reload

            # Discover domain
            cf_domain=""
            cf_domain="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
" 2>/dev/null | tr -d '[:space:]' || true)"
            if [ -z "$cf_domain" ]; then
                cf_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
            fi

            # Discover wildcard-covered hosts and non-wildcard service blocks.
            # - Wildcard-covered hosts route through Traefik via matcher.
            # - Unknown wildcard hosts route to /notice on frontend.
            # - External custom domains keep explicit direct on-demand TLS blocks with Host rewrite.
            cf_wildcard_known_hosts=""
            cf_wildcard_known_hosts="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
from apps.domains.models import Domain, DomainStatus
from django.db.models import Q
suffix = '.${cf_domain}'.lower().strip()
hosts = set()
for svc in Service.objects.all():
    d = (svc.public_domain or '').strip().lower()
    if d and suffix and d.endswith(suffix):
        hosts.add(d)
for domain in Domain.objects.filter(
    status__in=[DomainStatus.ACTIVE, DomainStatus.DNS_VERIFIED, DomainStatus.SSL_PROVISIONING],
).filter(Q(verified=True) | Q(status=DomainStatus.ACTIVE)):
    cd = (domain.domain_name or '').strip().lower()
    if cd and suffix and cd.endswith(suffix):
        hosts.add(cd)
print(' '.join(sorted(hosts)))
" 2>/dev/null | tr -d '\r' | tr -d '\n' || true)"

            cf_svc_blocks=""
            cf_svc_blocks="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
from apps.domains.models import Domain, DomainStatus
from django.db.models import Q
suffix = '.${cf_domain}'.lower().strip()
seen = set()
for svc in Service.objects.all():
    public_domain = (svc.public_domain or '').strip().lower()
    if public_domain and (not suffix or not public_domain.endswith(suffix)) and public_domain not in seen:
        seen.add(public_domain)
        print(f'{public_domain} {{\n    reverse_proxy localhost:8081\n    encode gzip\n}}\n')

for domain in Domain.objects.select_related('service').filter(
    status__in=[DomainStatus.ACTIVE, DomainStatus.DNS_VERIFIED, DomainStatus.SSL_PROVISIONING],
).filter(Q(verified=True) | Q(status=DomainStatus.ACTIVE)):
    custom_domain = (domain.domain_name or '').strip().lower()
    svc = domain.service
    public_domain = (svc.public_domain or '').strip().lower() if svc else ''
    if not custom_domain:
        continue
    if suffix and custom_domain.endswith(suffix):
        continue
    if custom_domain in seen:
        continue
    seen.add(custom_domain)

    if public_domain and public_domain != custom_domain:
        print(f'{custom_domain} {{\n    tls {{\n        on_demand\n    }}\n    reverse_proxy localhost:8081 {{\n        header_up Host {public_domain}\n    }}\n    encode gzip\n}}\n')
    else:
        print(f'{custom_domain} {{\n    tls {{\n        on_demand\n    }}\n    reverse_proxy localhost:8081\n    encode gzip\n}}\n')
" 2>/dev/null | tr -d '\r' || true)"

            # Only generate wildcard Caddyfile for real domains
            cf_is_real_domain=false
            if [ -n "$cf_domain" ] && [ "$cf_domain" != "localhost" ]; then
                if ! echo "$cf_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
                    cf_is_real_domain=true
                fi
            fi

            if [ "$cf_is_real_domain" = "true" ]; then
                cf_known_stanza=""
                if [ -n "$cf_wildcard_known_hosts" ]; then
                    cf_known_stanza="    @known_hosts host ${cf_wildcard_known_hosts}
    handle @known_hosts {
        reverse_proxy localhost:8081
    }"
                fi

                cat > /etc/caddy/Caddyfile.tmp <<CFCADDY
# Auto-generated with Cloudflare DNS challenge (wildcard SSL)
{
    on_demand_tls {
        ask http://localhost:8090/api/v1/services/check-domain/
    }
}

${cf_domain} {
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

*.${cf_domain} {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
${cf_known_stanza}
    @platform_assets path /_next/* /favicon.ico /images/* /logos/* /assets/* /static/* /media/*
    handle @platform_assets {
        reverse_proxy localhost:8090
    }
    handle {
        rewrite * /notice
        reverse_proxy localhost:8090
    }
}

:443 {
    tls {
        on_demand
    }
    reverse_proxy localhost:8090
}

:80 {
    reverse_proxy localhost:8090
}

${cf_svc_blocks}
CFCADDY
                if install_caddyfile_atomically /etc/caddy/Caddyfile.tmp "wildcard Caddyfile"; then
                    echo -e "${GREEN}  ✓ Caddyfile generated with wildcard SSL for *.${cf_domain}${NC}"
                else
                    echo -e "${YELLOW}  ⚠ Wildcard Caddyfile could not be applied. Falling back to standard HTTPS for ${cf_domain}.${NC}"
                    generate_safe_caddyfile "wildcard Caddyfile apply failed"
                fi
                rm -f /etc/caddy/Caddyfile.tmp
            else
                # IP mode or no domain — fall back to safe Caddyfile
                generate_safe_caddyfile "update flow (IP mode)"
            fi
        else
            # No valid token — generate safe Caddyfile (no dns cloudflare)
            generate_safe_caddyfile "update flow caddy regen"

            # Strip any leftover dns cloudflare blocks to prevent crash
            if false && grep -q 'dns cloudflare' /etc/caddy/Caddyfile 2>/dev/null; then
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

        # Final validation — if still broken, regenerate safe fallback
        if caddy_needs_fix; then
            generate_safe_caddyfile "post-update validation"
        fi

        reload_caddy_preserving_previous "" >/dev/null 2>&1 || true
        restart_caddy_watcher_safely "${cf_domain:-}" "post-update Caddy watcher verification"

        # Verify Caddy is running
        sleep 2
        if systemctl is-active --quiet caddy 2>/dev/null; then
            echo -e "${GREEN}  ✓ Caddy config regenerated and running${NC}"
        else
            echo -e "${YELLOW}  ⚠ Caddy failed to start. Run: journalctl -u caddy --no-pager -n 20${NC}"
        fi

        POST_CADDY_DOMAIN="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
" 2>/dev/null | tr -d '[:space:]' || true)"
        if [ -z "$POST_CADDY_DOMAIN" ]; then
            POST_CADDY_DOMAIN="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
        fi
        ensure_caddy_https_listener "$POST_CADDY_DOMAIN" "post-update Caddy verification" || true
        install_caddy_health_guard "$POST_CADDY_DOMAIN"
    fi

    safe_refresh_runtime_services

    # ─── Auto-redeploy active services when platform code or domain state changes ──
    GIT_CHANGES="$(cd "$INSTALL_DIR" && git diff HEAD@{1} --name-only 2>/dev/null | head -5 || true)"
    if [ -n "$GIT_CHANGES" ]; then
        echo -e "${BLUE}  → Auto-redeploying active services (platform code changed)...${NC}"
        queue_active_service_redeploys "Platform update auto-redeploy" "" \
            2>/dev/null || echo -e "${YELLOW}  ⚠ Auto-redeploy skipped (backend not ready)${NC}"
    elif [ "${DOMAIN_SYNC_REDEPLOY_REQUIRED:-0}" = "1" ]; then
        echo -e "${BLUE}  → Auto-redeploying rewritten services (platform domain changed)...${NC}"
        queue_active_service_redeploys "Platform domain change auto-redeploy" "${DOMAIN_SYNC_SERVICE_IDS}" \
            2>/dev/null || echo -e "${YELLOW}  ⚠ Domain-change redeploy skipped (backend not ready)${NC}"
    else
        echo -e "${GREEN}  ✓ No platform code or domain-driven redeploys required${NC}"
    fi

    # ─── Endpoint Verification (3 checks) ──────────────────────────────────
    echo -e "\n${BLUE}  → Running endpoint verification (3 checks)...${NC}"
    sleep 5
    PASS_COUNT=0
    FAIL_COUNT=0

    # ── Check 1: Backend API health (through local Nginx on port 8090) ──
    EP1_URL="http://127.0.0.1:8090/health"
    echo -e "${BLUE}  [1/3] Backend API health...${NC}"
    echo -e "${BLUE}        Endpoint: $EP1_URL${NC}"
    BACKEND_OK=false
    EP1_CODE="000"
    for attempt in 1 2 3 4 5; do
        EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP1_URL" 2>/dev/null) || EP1_CODE="000"
        case "$EP1_CODE" in
            2*|3*)
            BACKEND_OK=true
            break
            ;;
        esac
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
    if [ -n "$EP_DOMAIN" ] && [ "$EP_DOMAIN" != "localhost" ] && ! echo "$EP_DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EP2_URL="https://${EP_DOMAIN}/health"
        echo -e "${BLUE}        Endpoint: $EP2_URL${NC}"
        for attempt in 1 2 3; do
            EP2_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$EP2_URL" 2>/dev/null) || EP2_CODE="000"
            case "$EP2_CODE" in
                2*|3*)
                    HTTPS_OK=true
                    break
                    ;;
            esac
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
    elif [ -n "$EP_DOMAIN" ] && echo "$EP_DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EP2_URL="(skipped: IP mode)"
        EP2_RESULT="${YELLOW}SKIP${NC}"
        echo -e "${YELLOW}  [2/3] SKIPPED (HTTPS requires a domain name, not raw IP $EP_DOMAIN)${NC}"
    else
        EP2_RESULT="${YELLOW}SKIP${NC}"
        echo -e "${YELLOW}  ⊘ [2/3] SKIPPED (no domain configured)${NC}"
    fi

    # ── Check 3+: ALL deployed services (auto-discovered from DB) ──
    echo -e "${BLUE}  [3/N] Deployed services routing...${NC}"

    # Query ALL active service domains from the DB (public + custom)
    ALL_SVC_DOMAINS="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
for svc in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain='').order_by('name'):
    print(f'{svc.name}|{svc.public_domain.strip()}')
    for cd in (svc.custom_domains or []):
        cd = cd.strip()
        if cd:
            print(f'{svc.name} (custom)|{cd}')
" 2>/dev/null | tr -d '\r' || true)"

    # Also check Traefik port directly
    EP3_URL="http://127.0.0.1:8081/"
    EP3_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP3_URL" 2>/dev/null) || EP3_CODE="000"
    if [ "$EP3_CODE" != "000" ] && [ "$EP3_CODE" != "502" ]; then
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
                svc_code=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$svc_url" 2>/dev/null) || svc_code="000"
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
    if [ -f "$INSTALL_DIR/scripts/smsly-autoscaler.py" ]; then
        echo -e "${BLUE}  → Updating smsly-autoscaler service...${NC}"
        mkdir -p /opt/smsly
        cp "$INSTALL_DIR/scripts/smsly-autoscaler.py" /opt/smsly/autoscaler.py
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
    for CONTAINER in smsly-hosting-nginx-1 smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgcat-1 smsly-hosting-celery-1 smsly-hosting-celery-deploy-1 smsly-hosting-celery-fast-1 smsly-hosting-celery-beat-1 smsly-socket-proxy; do
        CPID=$(docker inspect --format '{{.State.Pid}}' "$CONTAINER" 2>/dev/null || echo "")
        if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
            echo -500 > "/proc/$CPID/oom_score_adj" 2>/dev/null || true
        fi
    done
    echo -e "${GREEN}  ✓ OOM protection set (core, database, celery, proxy)${NC}"

    trap - EXIT
    release_install_lock
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

# ─── Interactive Setup (Step 0) ──────────────────────────────────────────────
if [ "$NON_INTERACTIVE" != "true" ] && [ -t 0 ]; then
    # Architecture Selection
    if [ "${RUST_TWIN_MODE:-false}" = "false" ]; then
        echo -e "${BLUE}Select Backend Architecture:${NC}"
        echo -e "  1) ${GREEN}Legacy Python${NC} (Stable monolith)"
        echo -e "  2) ${GREEN}Next-Gen Rust${NC} (High-performance microservices, Beta)"
        read -p "Enter choice [1]: " _ARCH_CHOICE < /dev/tty
        if [ "${_ARCH_CHOICE:-1}" = "2" ]; then
            RUST_TWIN_MODE="true"
            COMPOSE_FILE="rust_twin/docker-compose.yml"
        fi
    fi

    # Agent Lite Selection
    if [ "$MODE_AGENT_LITE" = "true" ] && [ -z "${MASTER_IP:-}" ]; then
        echo -e "\n${BLUE}═══════════════════════════════════════════════════════════"
        echo "  CONFIGURING LITE AGENT NODE"
        echo "═══════════════════════════════════════════════════════════${NC}"
        read -p "  Enter Master VPS IP Address: " MASTER_IP < /dev/tty
        read -p "  Enter Master Database Password: " MASTER_DB_PASSWORD < /dev/tty
        echo ""
        read -p "  Enter Master RabbitMQ Password: " MASTER_MQ_PASSWORD < /dev/tty
        echo ""
        COMPOSE_FILE="infrastructure/docker/docker-compose.agent-lite.yml"
        export MASTER_IP MASTER_DB_PASSWORD MASTER_MQ_PASSWORD
    fi

    # ─── Deployment Mode Selection (Moved up) ──────────────────────────────
    # Initialize defaults
    MODE_CHOICE=1
    PUBLIC_IP="${PUBLIC_IP:-$(detect_public_ip)}"
    PRESET_DOMAIN="${DOMAIN:-}"
    PRESET_ACME_EMAIL="${ACME_EMAIL:-}"
    PRESET_USE_SSL="${USE_SSL:-}"

    # Deployment Mode Selection - Only prompt if not preset and in interactive shell
    if [ -n "${PRESET_USE_SSL}" ]; then
        if [ "${PRESET_USE_SSL}" = "true" ] && [ -n "${PRESET_DOMAIN}" ] && [ -n "${PRESET_ACME_EMAIL}" ]; then
            echo -e "${BLUE}  → Preset detected. Using SSL Mode for ${PRESET_DOMAIN}.${NC}"
            MODE_CHOICE=2
        elif [ "${PRESET_USE_SSL}" = "false" ]; then
            echo -e "${BLUE}  → Preset detected. Using IP Mode.${NC}"
            MODE_CHOICE=1
        fi
    elif [ "$NON_INTERACTIVE" != "true" ] && [ -t 0 ]; then
        echo -e "\n${BLUE}Select Deployment Mode:${NC}"
        echo -e "  1) ${GREEN}IP Mode${NC} (Easy) - http://$PUBLIC_IP"
        echo -e "  2) ${GREEN}SSL Mode${NC} (Prod) - https://your-domain.com (Requires DNS A Record pointing to $PUBLIC_IP)"
        read -p "Enter choice [1]: " MODE_CHOICE < /dev/tty
        MODE_CHOICE=${MODE_CHOICE:-1}
    fi

    # Set configuration based on choice or presets
    if [ "$MODE_CHOICE" -eq "2" ] || [ "${PRESET_USE_SSL}" = "true" ]; then
        USE_SSL="true"
        DOMAIN="${PRESET_DOMAIN:-}"
        ACME_EMAIL="${PRESET_ACME_EMAIL:-}"

        if [ "$NON_INTERACTIVE" != "true" ] && [ -t 0 ]; then
            while [ -z "$DOMAIN" ]; do
                read -p "Enter your Domain (e.g., app.example.com): " DOMAIN < /dev/tty
            done
            while [ -z "$ACME_EMAIL" ]; do
                read -p "Enter Email for SSL (e.g., admin@example.com): " ACME_EMAIL < /dev/tty
            done
        fi

        if [ -n "$DOMAIN" ]; then
            echo -e "${BLUE}  → Verifying DNS for $DOMAIN...${NC}"
            if command -v host &> /dev/null; then
                DETECTED_IP=$(host -t A "$DOMAIN" 2>/dev/null | awk '{print $NF}' | tail -n 1)
                if [[ "$DETECTED_IP" != "$PUBLIC_IP" && "$DETECTED_IP" != "127.0.0.1" ]]; then
                    echo -e "${YELLOW}  ⚠ WARNING: DNS for $DOMAIN ($DETECTED_IP) does not match this server ($PUBLIC_IP).${NC}"
                    echo -e "${YELLOW}  SSL generation may fail. Ensure your DNS A record is set.${NC}"
                    if [ "$NON_INTERACTIVE" != "true" ] && [ -t 0 ]; then
                        read -p "  Continue anyway? (y/n) " -n 1 -r < /dev/tty
                        echo
                        if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 1; fi
                    fi
                else
                    echo -e "${GREEN}  ✓ DNS looks correct.${NC}"
                fi
            fi
        fi
    else
        USE_SSL="false"
        DOMAIN="${DOMAIN:-$PUBLIC_IP}"
        echo -e "${BLUE}  → Using IP Mode: $DOMAIN${NC}"
    fi

    # ─── Wildcard Subdomain & Cloudflare Setup (Front-loaded) ────────────
    WILDCARD_SUBDOMAINS="false"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
    if [ "$USE_SSL" = "true" ] && [ -n "$DOMAIN" ] && [ "$DOMAIN" != "$PUBLIC_IP" ]; then
        echo ""
        echo -e "${BLUE}  Wildcard subdomains allow deployed services to get automatic SSL.${NC}"
        echo -e "  e.g., myapp-abc123.${DOMAIN} will automatically have HTTPS."
        echo -e "  This requires a Cloudflare API Token with DNS:Edit permission.\n"

        if [ -n "${CLOUDFLARE_API_TOKEN}" ]; then
            WILDCARD_SUBDOMAINS="true"
            echo -e "${BLUE}  → Preset Cloudflare token detected. Enabling wildcard subdomains.${NC}"
        elif [ "$NON_INTERACTIVE" != "true" ] && [ -t 0 ]; then
            read -p "  Enable wildcard subdomains? (y/n) [n]: " WILDCARD_CHOICE < /dev/tty
            WILDCARD_CHOICE=${WILDCARD_CHOICE:-n}
            if [[ $WILDCARD_CHOICE =~ ^[Yy]$ ]]; then
                WILDCARD_SUBDOMAINS="true"
                while [ -z "$CLOUDFLARE_API_TOKEN" ]; do
                    read -p "  Enter Cloudflare API Token (DNS:Edit): " CLOUDFLARE_API_TOKEN < /dev/tty
                    echo
                done
                echo -e "${GREEN}  ✓ Wildcard subdomains enabled.${NC}"
            fi
        fi
    fi
fi

# -----------------------------------------------------------------------------
# 1. Pre-flight Checks
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[1/9] Checking system requirements...${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}✗ Please run as root (sudo bash install.sh)${NC}"
    exit 1
fi

check_internet
check_hardware
ensure_system_swap

# Check OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo -e "${BLUE}  Detected: $NAME $VERSION_ID${NC}"
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
        echo -e "${YELLOW}⚠ Warning: This script is optimized for Ubuntu/Debian.${NC}"
        if [ -e /dev/tty ] && [ "$NON_INTERACTIVE" != "true" ]; then
             echo -e "${YELLOW}  Press ENTER to continue anyway, or Ctrl+C to abort.${NC}"
             read -r < /dev/tty
        else
             echo -e "${YELLOW}  ⚠ Automated mode: Continuing automatically...${NC}"
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

# ─── Git Initialization & Sync ──────────────────────────────────────────────
SMSLY_BRANCH="${SMSLY_BRANCH:-main}"
SMSLY_GIT_REMOTE="${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"

if [ -d "$INSTALL_DIR/.git" ]; then
    echo -e "${BLUE}  → Updating existing repository ($SMSLY_BRANCH)...${NC}"
    cd "$INSTALL_DIR"
    if ! git fetch origin "$SMSLY_BRANCH" >/dev/null 2>&1 || ! git reset --hard "origin/$SMSLY_BRANCH" >/dev/null 2>&1; then
        echo -e "${YELLOW}  ⚠️ Git update failed. If this is a private repo, ensure your token is valid.${NC}"
    fi
else
    echo -e "${BLUE}  → Cloning repository ($SMSLY_BRANCH)...${NC}"
    CLONE_SUCCESS=false
    if [ -d "$INSTALL_DIR" ] && [ "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
        echo -e "${YELLOW}  → Destination not empty. Initializing git...${NC}"
        cd "$INSTALL_DIR"
        git init -q
        git remote add origin "$SMSLY_GIT_REMOTE"
        if git fetch origin "$SMSLY_BRANCH" -q >/dev/null 2>&1 && git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" >/dev/null 2>&1; then
            git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
            CLONE_SUCCESS=true
        fi
    else
        if git clone -b "$SMSLY_BRANCH" "$SMSLY_GIT_REMOTE" "$INSTALL_DIR"; then
            CLONE_SUCCESS=true
        fi
    fi

    if [ "$CLONE_SUCCESS" = "false" ]; then
        echo -e "${YELLOW}  ⚠️ Git clone/fetch failed.${NC}"
        if [ -n "${SMSLY_INSTALL_WORKDIR:-}" ] && [ -d "${SMSLY_INSTALL_WORKDIR}" ]; then
            echo -e "${BLUE}  → Fallback: Initializing from pre-uploaded source bundle...${NC}"
            mkdir -p "$INSTALL_DIR"
            cp -rv "${SMSLY_INSTALL_WORKDIR}/"* "$INSTALL_DIR/" 2>/dev/null || true
            cd "$INSTALL_DIR"
            if [ ! -d ".git" ]; then
                git init -q
                git remote add origin "$SMSLY_GIT_REMOTE"
            fi
            echo -e "${GREEN}  ✓ Fallback initialization complete.${NC}"
        fi
    fi
fi

echo -e "${GREEN}  ✓ Pre-flight checks passed${NC}"
set_checkpoint "requirements_checked"

# -----------------------------------------------------------------------------
# 2. Dependency Management & cleanup
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "dependencies_installed"; then
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

apt_run apt-get update -qq
apt_run apt-get install -y curl wget git python3 python3-pip python3-venv openssl ca-certificates gnupg lsb-release dnsutils

# Install Docker if missing
if ! command -v docker &> /dev/null; then
    echo -e "${BLUE}  → Installing Docker...${NC}"
    mkdir -m 0755 -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt_run apt-get update -qq
    apt_run apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
    echo -e "${GREEN}  ✓ Docker already installed ($(docker --version | head -c 40))${NC}"
fi

# Ensure docker compose is available
if ! docker compose version >/dev/null 2>&1; then
    echo -e "${BLUE}  → Installing Docker Compose plugin...${NC}"
    apt_run apt-get install -y docker-compose-plugin || true
fi

# Apply mirror config if applicable (Only if docker is now present)
if command -v docker &> /dev/null; then
    configure_docker_mirror
fi

echo -e "${GREEN}  ✓ Dependencies installed${NC}"
    set_checkpoint "dependencies_installed"
fi

# -----------------------------------------------------------------------------
# 3. Configuration & Secrets (IDEMPOTENT)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "config_generated"; then
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
             echo -e "${BLUE}  → Updating existing repository...${NC}"
             cd "$INSTALL_DIR"
             if [ -n "${SMSLY_GIT_REMOTE:-}" ]; then
                 git remote set-url origin "$SMSLY_GIT_REMOTE" 2>/dev/null || true
             fi
             git fetch origin "$SMSLY_BRANCH" >/dev/null 2>&1 || true
             git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" >/dev/null 2>&1 || true
             git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
        else
             echo -e "${BLUE}  → Cloning repository...${NC}"
             if [ -d "$INSTALL_DIR" ] && [ "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
                 echo -e "${YELLOW}  → Destination not empty. Initializing git and pulling...${NC}"
                 cd "$INSTALL_DIR"
                 git init -q
                 git remote add origin "${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"
                 git fetch origin "$SMSLY_BRANCH" -q >/dev/null 2>&1 || true
                 git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" >/dev/null 2>&1 || true
                 git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
             else
                 git clone -b "$SMSLY_BRANCH" "${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}" "$INSTALL_DIR"
                 cd "$INSTALL_DIR"
                 git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
             fi
        fi
    fi
fi
cd "$INSTALL_DIR"

# ─── Git Initialization (for bundled installs) ──────────────────────────────
if [ ! -d ".git" ] && [ -n "${SMSLY_GIT_REMOTE:-}" ]; then
    echo -e "${BLUE}  -> Initializing Git repository...${NC}"
    git init -q
    git checkout -b "$SMSLY_BRANCH" >/dev/null 2>&1 || true
    git remote add origin "$SMSLY_GIT_REMOTE"
    git fetch origin "$SMSLY_BRANCH" -q --depth=1 || true
    git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
    # We don't reset --hard here to avoid losing the bundled files we just copied,
    # but the repo is now linked for future updates.
    echo -e "${GREEN}  ✓ Git origin set to ${SMSLY_GIT_REMOTE}${NC}"
fi

# ─── BLINDSPOT FIX: Validate required deployment files ──────────────────────
echo -e "${BLUE}  → Validating deployment files...${NC}"
MISSING_FILES=()
if [ "$MODE_AGENT_LITE" = "true" ]; then
    REQUIRED_FILES=("$COMPOSE_FILE" "backend/Dockerfile" "backend/entrypoint.sh" "backend/requirements.txt")
else
    REQUIRED_FILES=("$COMPOSE_FILE" "nginx.conf" "backend/Dockerfile" "frontend/Dockerfile" "backend/entrypoint.sh")
fi
for required_file in "${REQUIRED_FILES[@]}"; do
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
    apply_agent_lite_env_overrides "$INSTALL_DIR/.env"
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x Existing .env is invalid. Fix it or restore .env.backup and rerun.${NC}"
        exit 1
    fi

    # Source existing values for summary output.
    source "$INSTALL_DIR/.env" 2>/dev/null || true
    DOMAIN="${DOMAIN:-localhost}"
    USE_SSL="${USE_SSL:-false}"
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-false}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
    PUBLIC_IP="$(detect_public_ip)"


else
    # ─── Configuration Summary ──────────────────────────────────────────────
    PUBLIC_IP="${PUBLIC_IP:-$(detect_public_ip)}"
    DOMAIN="${DOMAIN:-$PUBLIC_IP}"
    USE_SSL="${USE_SSL:-false}"
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-false}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
    ACME_EMAIL="${ACME_EMAIL:-}"

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
rabbitmq_pass = secrets.token_hex(16)
gateway_secret = secrets.token_hex(32)
webhook_secret = secrets.token_hex(32)
autoscaler_token = secrets.token_hex(32)
frp_token = secrets.token_hex(32)
pgcat_admin_pass = secrets.token_hex(24)

# Validate the Fernet key before outputting
Fernet(fernet_key.encode())

print(f'SECRET_KEY={secret_key}')
print(f'FIELD_ENCRYPTION_KEY={fernet_key}')
print(f'POSTGRES_PASSWORD={pg_pass}')
print(f'REDIS_PASSWORD={redis_pass}')
print(f'RABBITMQ_PASSWORD={rabbitmq_pass}')
print(f'GATEWAY_SECRET={gateway_secret}')
print(f'GITHUB_WEBHOOK_SECRET={webhook_secret}')
print(f'AUTOSCALER_API_TOKEN={autoscaler_token}')
print(f'FRP_AUTH_TOKEN={frp_token}')
print(f'PGCAT_ADMIN_PASSWORD={pgcat_admin_pass}')
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

    # Create .env (Atomic)
    ENV_TMP="$INSTALL_DIR/.env.tmp"
    cat <<EOF > "$ENV_TMP"
# SMSLY Hosting Configuration — Generated $(date -Iseconds)
ENVIRONMENT=production
DEBUG=False
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$FIELD_ENCRYPTION_KEY
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_USER=smsly_admin
POSTGRES_DB=smsly_hosting
DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@pgcat:5432/smsly_hosting
DATABASE_CONNECT_TIMEOUT=5

REDIS_PASSWORD=$REDIS_PASSWORD
RABBITMQ_PASSWORD=$RABBITMQ_PASSWORD
REDIS_URL=redis://:$REDIS_PASSWORD@redis:6379/0
REDIS_SOCKET_TIMEOUT=5
CELERY_BROKER_URL=amqp://smsly_user:$RABBITMQ_PASSWORD@rabbitmq:5672//

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

# FRP Tunnel Relay Authentication Token
FRP_AUTH_TOKEN=$FRP_AUTH_TOKEN

# PgCat administration password
PGCAT_ADMIN_PASSWORD=$PGCAT_ADMIN_PASSWORD

# Direct database connection for migrations (bypasses PgCat pooler)
DIRECT_DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@db:5432/smsly_hosting

# The installer runs first-boot Django setup explicitly after the stack starts.
# Keep the web container from doing the same work while Compose is waiting on health.
SMSLY_RUN_ENTRYPOINT_TASKS=false

# AppConfig.ready() must stay side-effect free during installs and management commands.
# Edge/proxy sync is performed explicitly by the installer and watcher services.
SMSLY_ENABLE_STARTUP_CADDY_SYNC=false
EOF

    # ─── Dynamic Build Resource Allocation ──────────────────────────────
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        # Detect physical RAM for optimized build limits
        current_ram_mb=$(free -m | awk '/^Mem:/{print $2}')
        build_mem=2048
        if [ "$current_ram_mb" -ge 8192 ]; then
            build_mem=4096
        elif [ "$current_ram_mb" -ge 16384 ]; then
            build_mem=8192
        fi
        echo "FRONTEND_BUILD_MEMORY_MB=$build_mem" >> "$ENV_TMP"
        echo -e "${BLUE}  → Allocated ${build_mem}MB for frontend build (System RAM: ${current_ram_mb}MB)${NC}"
    else
        echo -e "${BLUE}  → Lite Agent mode: frontend build is not part of this node.${NC}"
    fi

    # Derive expected tunnel domain
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ] && ! echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EXPECTED_TUNNEL_DOMAIN="tunnel.${DOMAIN}"
    elif [ -n "$PUBLIC_IP" ] && ! echo "$PUBLIC_IP" | grep -qE '^(127\.0\.0\.1|0\.0\.0\.0)$'; then
        EXPECTED_TUNNEL_DOMAIN="tunnel.${PUBLIC_IP}.sslip.io"
    else
        EXPECTED_TUNNEL_DOMAIN="tunnel.localhost"
    fi
    echo "TUNNEL_DOMAIN=$EXPECTED_TUNNEL_DOMAIN" >> "$ENV_TMP"

    # ── Agent Lite Overrides ──────────────────────────────────────
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        apply_agent_lite_env_overrides "$ENV_TMP"
    else
        echo "MODE=master" >> "$ENV_TMP"
    fi

    # Atomic move and validation
    if validate_env_file "$ENV_TMP"; then
        mv "$ENV_TMP" "$INSTALL_DIR/.env"
        chmod 600 "$INSTALL_DIR/.env"
        echo -e "${GREEN}  ✓ Configuration saved to .env (chmod 600)${NC}"
    else
        echo -e "${RED}  x Generated .env failed validation. Aborting install.${NC}"
        rm -f "$ENV_TMP"
        exit 1
    fi
fi
    set_checkpoint "config_generated"
fi

# -----------------------------------------------------------------------------
# 4. Deployment
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "stack_deployed"; then
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
ensure_infrastructure_permissions
if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "${BLUE}  → Lite Agent mode: disabling master-only Caddy services before Traefik bind.${NC}"
    systemctl disable --now caddy caddy-watcher smsly-update-watcher >/dev/null 2>&1 || true
fi
if [ "$RUST_TWIN_MODE" != "true" ]; then
    echo -e "${BLUE}  → Disabling backend entrypoint bootstrap for installer-controlled migrations...${NC}"
    env_set_value "$INSTALL_DIR/.env" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
fi
    echo -e "${BLUE}  → Starting App Stack (Build + Deploy)...${NC}"
    ( while true; do sleep 30; echo -e "${BLUE}      ↳ Progress: Deployment in progress... $(date +%H:%M:%S)${NC}"; done ) &
    HEARTBEAT_PID=$!
    set +e
    docker compose -f "$COMPOSE_FILE" build
    DEPLOY_RC=$?
    if [ "$DEPLOY_RC" -eq 0 ]; then
        docker compose -f "$COMPOSE_FILE" up -d --remove-orphans
        DEPLOY_RC=$?
    fi
    set -e
    kill $HEARTBEAT_PID 2>/dev/null || true
    wait $HEARTBEAT_PID 2>/dev/null || true
    if [ "$DEPLOY_RC" -ne 0 ]; then
        echo -e "${RED}  ✗ Docker Compose failed during stack deployment (exit $DEPLOY_RC).${NC}"
        docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true
        docker compose -f "$COMPOSE_FILE" logs --tail=120 2>/dev/null || true
        exit "$DEPLOY_RC"
    fi
    set_checkpoint "stack_deployed"
fi

# -----------------------------------------------------------------------------
# 5. Database Setup
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "database_initialized"; then
    echo -e "\n${YELLOW}[5/9] Initializing Database...${NC}"

if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "${BLUE}  → Lite Agent mode: skipping local database initialization; using Master services.${NC}"
    set_checkpoint "database_initialized"
else
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

if [ "$RUST_TWIN_MODE" != "true" ]; then
    echo -e "${BLUE}  → Running Migrations...${NC}"
    # Note: Do NOT run makemigrations — migrations are committed in the repo.
    # Running makemigrations generates files inside the container that conflict on redeploy.
    MIGRATE_OK=false
    for attempt in 1 2 3; do
        if run_backend_migrations 2>&1; then
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
else
    echo -e "${BLUE}  → Rust Twin: Skipping Django manage.py migrations (handled via SeaORM/CLI in future steps)...${NC}"
fi

if [ "$RUST_TWIN_MODE" != "true" ]; then
    echo -e "${BLUE}  → Collecting Static Files...${NC}"
    # Fix volume ownership — Docker creates named volumes as root
    docker compose -f "$COMPOSE_FILE" exec -T --user root backend chown -R 1000:1000 /app/staticfiles /app/media /app/backups 2>/dev/null || true
    docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py collectstatic --noinput

    sync_platform_domain_state "$INSTALL_DIR/.env"
else
    echo -e "${BLUE}  → Rust Twin: Skipping static file collection (handled by Trunk WASM bundler)...${NC}"
fi
    set_checkpoint "database_initialized"
fi
fi

# -----------------------------------------------------------------------------
# 6. Admin User (IDEMPOTENT — skips if admin already exists)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "admin_created"; then
    echo -e "\n${YELLOW}[6/9] Creating Admin User...${NC}"

if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "${BLUE}  → Lite Agent mode: skipping master admin and Local Docker provider setup.${NC}"
    set_checkpoint "admin_created"
else
if [ "$RUST_TWIN_MODE" = "true" ]; then
    echo -e "${BLUE}  → Rust Twin: Skipping Python admin user creation (Use 'docker compose exec cli createsuperuser')...${NC}"
    ADMIN_EXISTS=1
else
    ADMIN_EXISTS=$(echo "from django.contrib.auth import get_user_model; User = get_user_model(); print('1' if User.objects.filter(username='admin').exists() else '0')" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1)
fi

if [ "${ADMIN_EXISTS:-0}" = "1" ]; then
    echo -e "${GREEN}  ✓ Admin user check bypassed or already exists — skipping${NC}"
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
    if [ "$MODE_AGENT_LITE" = "false" ]; then
        ADMIN_PASS="$(gen_hex_secret 16)"
        echo "
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
User = get_user_model()
admin = User.objects.create_superuser('admin', 'admin@smsly.cloud', '$ADMIN_PASS')
token = Token.objects.create(user=admin)
print(token.key)
" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1 > "$INSTALL_DIR/.token"
        echo -e "${GREEN}  ✓ Admin user created with API Token${NC}"
        chmod 600 "$INSTALL_DIR/.token"

        # ─── Save credentials to secure file (NOT echoed to terminal) ───────────────
        cat > "$CREDENTIALS_FILE" <<CREDS
# SMSLY Hosting Admin Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE
Username: admin
Password: $ADMIN_PASS
CREDS
        chmod 600 "$CREDENTIALS_FILE"

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
    fi
fi
if [ "$RUST_TWIN_MODE" != "true" ]; then
    echo -e "${BLUE}  → Keeping backend entrypoint bootstrap disabled; installer controls migrations...${NC}"
    env_set_value "$INSTALL_DIR/.env" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
fi
    set_checkpoint "admin_created"
fi
fi

# -----------------------------------------------------------------------------
# 7. Caddy Reverse Proxy (Public Access)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "caddy_configured"; then
if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "\n${YELLOW}[7/9] Configuring Lite Agent Edge...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d socket-proxy backend celery-worker traefik >/dev/null 2>&1 || true
    echo -e "${GREEN}  ✓ Lite Agent edge services are managed by Traefik; skipping Caddy.${NC}"
    set_checkpoint "caddy_configured"
else
    echo -e "\n${YELLOW}[7/9] Setting up Caddy Reverse Proxy...${NC}"

if [ "$RUST_TWIN_MODE" = "true" ]; then
    echo -e "${BLUE}  → Formatting Rust Twin Caddyfile...${NC}"
    cd rust_twin && export DOMAIN && export ACME_EMAIL && caddy fmt --overwrite Caddyfile 2>/dev/null || true
    cd ..
    # Swap the default Caddyfile path to point to the Rust Twin version
    install_caddyfile_atomically rust_twin/Caddyfile "Rust Twin Caddyfile" || true
fi

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
            apt_run apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null 2>&1
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
            apt_run apt-get update >/dev/null 2>&1
            apt_run apt-get install -y caddy >/dev/null 2>&1
        fi
    fi
    cd "$INSTALL_DIR"
    rm -rf "$CADDY_TMP"
fi

if ! systemctl list-unit-files caddy.service >/dev/null 2>&1; then
    echo -e "${BLUE}  → Installing Caddy systemd service...${NC}"
    # Add a dedicated caddy user and group
    groupadd --system caddy 2>/dev/null || true
    useradd --system --gid caddy --create-home --home-dir /var/lib/caddy \
        --shell /usr/sbin/nologin --comment "Caddy web server" caddy 2>/dev/null || true

    cat > /etc/systemd/system/caddy.service <<'CADDYSRV'
[Unit]
Description=Caddy
Documentation=https://caddyserver.com/docs/
After=network.target network-online.target
Requires=network-online.target

[Service]
Type=exec
User=caddy
Group=caddy
ExecStart=/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile --force
Restart=always
RestartSec=3
TimeoutStopSec=5s
LimitNOFILE=1048576
LimitNPROC=512
PrivateTmp=true
ProtectSystem=full
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
CADDYSRV
    systemctl daemon-reload
    echo -e "${GREEN}  ✓ Caddy systemd service installed${NC}"
fi

# ─── Configure Caddyfile ──────────────────────────────────────────────────────
echo -e "${BLUE}  → Configuring Caddyfile...${NC}"
mkdir -p /var/log/caddy
mkdir -p /etc/caddy
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
        cat > /etc/caddy/Caddyfile.tmp <<CADDYEOF
# Grid Reverse Proxy — Auto-generated
# Domain: $DOMAIN → HTTPS (auto Let's Encrypt)
# Wildcard: *.$DOMAIN → HTTPS (Cloudflare DNS challenge)

{
    on_demand_tls {
        ask http://localhost:8090/api/v1/services/check-domain/
    }
}

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
    @platform_assets path /_next/* /favicon.ico /images/* /logos/* /assets/* /static/* /media/*
    handle @platform_assets {
        reverse_proxy localhost:8090
    }
    handle {
        rewrite * /notice
        reverse_proxy localhost:8090
    }
}

:443 {
    tls {
        on_demand
    }
    reverse_proxy localhost:8090
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
        install_caddyfile_atomically /etc/caddy/Caddyfile.tmp "fresh wildcard Caddyfile" || generate_safe_caddyfile "fresh wildcard fallback"
        rm -f /etc/caddy/Caddyfile.tmp

        echo -e "${GREEN}  ✓ Caddy configured: HTTPS ($DOMAIN) + Wildcard (*.$DOMAIN) + HTTP fallback → 8090${NC}"
    else
        # ─── Standard SSL (no wildcard) ──────────────────────────────────
        cat > /etc/caddy/Caddyfile.tmp <<CADDYEOF
# Grid Reverse Proxy — Auto-generated
# Domain: $DOMAIN → HTTPS (auto Let's Encrypt)

{
    on_demand_tls {
        ask http://localhost:8090/api/v1/services/check-domain/
    }
}

$DOMAIN {
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

:443 {
    tls {
        on_demand
    }
    reverse_proxy localhost:8090
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
        install_caddyfile_atomically /etc/caddy/Caddyfile.tmp "fresh HTTPS Caddyfile" || generate_safe_caddyfile "fresh HTTPS fallback"
        rm -f /etc/caddy/Caddyfile.tmp
        echo -e "${GREEN}  ✓ Caddy configured: HTTPS ($DOMAIN) + HTTP (:80 fallback) → 8090${NC}"
    fi
else
    cat > /etc/caddy/Caddyfile.tmp <<CADDYEOF
# Grid Reverse Proxy — Auto-generated
:80 {
    reverse_proxy localhost:8090
}
CADDYEOF
    if [ -f "$CADDY_OVERRIDE_FILE" ]; then
        rm -f "$CADDY_OVERRIDE_FILE"
        rmdir "$CADDY_OVERRIDE_DIR" 2>/dev/null || true
        systemctl daemon-reload
    fi
    install_caddyfile_atomically /etc/caddy/Caddyfile.tmp "fresh IP-mode Caddyfile" || true
    rm -f /etc/caddy/Caddyfile.tmp
    echo -e "${GREEN}  ✓ Caddy configured for HTTP: :80 → 8090${NC}"
fi

# ─── Create caddy-config volume directory for Settings UI writes ──────────────
ensure_infrastructure_permissions

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
    restart_caddy_watcher_safely "${DOMAIN:-}" "fresh install Caddy watcher verification"
    install_caddy_health_guard "${DOMAIN:-}"
    echo -e "${GREEN}  ✓ Caddy watcher service installed and running${NC}"
fi

# ─── Install update-watcher service (picks up UI-driven platform updates) ─────
if [ -f "$INSTALL_DIR/scripts/platform-update.sh" ]; then
    chmod +x "$INSTALL_DIR/scripts/platform-update.sh"
    cat > /etc/systemd/system/smsly-update-watcher.service <<UPDATEWATCHEREOF
[Unit]
Description=Platform Update Watcher (SMSLY)
After=network.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/scripts/platform-update.sh /opt/smsly-hosting/caddy-config
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UPDATEWATCHEREOF
    systemctl daemon-reload
    systemctl enable smsly-update-watcher >/dev/null 2>&1
    systemctl restart smsly-update-watcher
    echo -e "${GREEN}  ✓ Platform update watcher service installed and running${NC}"
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

reload_caddy_preserving_previous "" || true
systemctl enable caddy >/dev/null 2>&1

# Verify Caddy is running
sleep 2
if systemctl is-active --quiet caddy; then
    echo -e "${GREEN}  ✓ Caddy reverse proxy active${NC}"
    fi

    safe_refresh_runtime_services
    set_checkpoint "caddy_configured"
fi
fi

# -----------------------------------------------------------------------------
# 8. System Memory Hardening (Prevents OOM kills)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "memory_hardened"; then
    echo -e "\n${YELLOW}[8/9] Hardening System Memory...${NC}"

# ─── Swap: Ensure swap is at least 4x RAM ────────────────────────────────────
ensure_system_swap

# ─── Auto-Maintenance: Install OOM Swap Adjuster ─────────────────────────────
OOM_SCRIPT="/opt/smsly/scripts/oom-swap-adjuster.sh"
mkdir -p /opt/smsly/scripts
cat << 'EOF' > "$OOM_SCRIPT"
#!/usr/bin/env bash
# oom-swap-adjuster.sh
#
# Monitors the system for Out Of Memory (OOM) kills. If one is detected within the last
# X minutes, it automatically increases the swap space by 200MB up to a maximum of 4x RAM.
# This serves as an auto-maintenance feature to prevent recurring build crashes.

set -euo pipefail

LOG_FILE="/var/log/smsly-oom-adjuster.log"
MINUTES_BACK=10
SWAPFILE_PREFIX="/swapfile-smsly-auto"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Check for OOM events in the last N minutes using journalctl
OOM_COUNT=$(journalctl -k --since "${MINUTES_BACK} minutes ago" | grep -i "out of memory" | wc -l || true)

if [ "$OOM_COUNT" -eq 0 ]; then
    # No OOM detected recently, exit quietly.
    exit 0
fi

log "Detected $OOM_COUNT OOM events in the last $MINUTES_BACK minutes. Evaluating swap size."

# Get RAM size in MB
RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
CURRENT_SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')

# Maximum allowed swap is 4x RAM
MAX_SWAP_MB=$((RAM_MB * 4))

if [ "$CURRENT_SWAP_MB" -ge "$MAX_SWAP_MB" ]; then
    log "Swap is already at or above the maximum allowed limit (4x RAM = ${MAX_SWAP_MB}MB). No further auto-adjustment will be made."
    exit 0
fi

# Calculate new swap chunk to add (200MB)
ADD_SWAP_MB=200
NEW_TOTAL_MB=$((CURRENT_SWAP_MB + ADD_SWAP_MB))

# Cap at max if we would overshoot
if [ "$NEW_TOTAL_MB" -gt "$MAX_SWAP_MB" ]; then
    ADD_SWAP_MB=$((MAX_SWAP_MB - CURRENT_SWAP_MB))
    NEW_TOTAL_MB=$MAX_SWAP_MB
fi

if [ "$ADD_SWAP_MB" -le 0 ]; then
    exit 0
fi

NEW_SWAPFILE="${SWAPFILE_PREFIX}-$(date '+%s')"
log "Increasing swap by ${ADD_SWAP_MB}MB. Creating ${NEW_SWAPFILE}..."

# Create the new swap file
if fallocate -l ${ADD_SWAP_MB}M "$NEW_SWAPFILE" 2>/dev/null; then
    chmod 600 "$NEW_SWAPFILE"
    mkswap "$NEW_SWAPFILE" >/dev/null 2>&1
    swapon "$NEW_SWAPFILE" 2>/dev/null || true

    # Make it permanent
    if ! grep -q "$NEW_SWAPFILE" /etc/fstab 2>/dev/null; then
        echo "$NEW_SWAPFILE none swap sw 0 0" >> /etc/fstab
    fi

    log "Successfully added ${ADD_SWAP_MB}MB of swap. Total swap is now approx ${NEW_TOTAL_MB}MB."
else
    # Fallback to dd if fallocate fails (e.g. some filesystems don't support it)
    log "fallocate failed, trying dd..."
    if dd if=/dev/zero of="$NEW_SWAPFILE" bs=1M count=$ADD_SWAP_MB status=none; then
        chmod 600 "$NEW_SWAPFILE"
        mkswap "$NEW_SWAPFILE" >/dev/null 2>&1
        swapon "$NEW_SWAPFILE" 2>/dev/null || true

        if ! grep -q "$NEW_SWAPFILE" /etc/fstab 2>/dev/null; then
            echo "$NEW_SWAPFILE none swap sw 0 0" >> /etc/fstab
        fi

        log "Successfully added ${ADD_SWAP_MB}MB of swap via dd. Total swap is now approx ${NEW_TOTAL_MB}MB."
    else
        log "Failed to create swap file."
        rm -f "$NEW_SWAPFILE"
        exit 1
    fi
fi
EOF
chmod +x "$OOM_SCRIPT"

# Add cron job to run the script every 5 minutes
CRON_JOB="*/5 * * * * root $OOM_SCRIPT"
if ! grep -q "$OOM_SCRIPT" /etc/crontab 2>/dev/null; then
    echo "$CRON_JOB" >> /etc/crontab
    echo -e "${GREEN}  ✓ OOM Auto-Adjuster installed and scheduled via cron${NC}"
else
    echo -e "${GREEN}  ✓ OOM Auto-Adjuster already scheduled${NC}"
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
# Security Hardening
ensure_sysctl "net.ipv4.conf.all.rp_filter" "1" "IP spoofing protection"
ensure_sysctl "net.ipv4.conf.default.rp_filter" "1" "IP spoofing protection"
ensure_sysctl "net.ipv4.icmp_echo_ignore_broadcasts" "1" "ICMP flood protection"
ensure_sysctl "net.ipv4.conf.all.accept_source_route" "0" "Disable source routing"
ensure_sysctl "net.ipv4.tcp_syncookies" "1" "SYN flood protection"

if [ "$SYSCTL_UPDATED" = "false" ]; then
    echo -e "${GREEN}  ✓ Sysctl settings already optimal${NC}"
fi

# ─── OOM Protection for critical containers ──────────────────────────────────
echo -e "${BLUE}  → Setting OOM protection for critical containers...${NC}"
if [ "$MODE_AGENT_LITE" = "true" ]; then
    CRITICAL_CONTAINERS=(smsly-hosting-traefik-1 smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-socket-proxy-1)
else
    CRITICAL_CONTAINERS=(smsly-hosting-nginx-1 smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgcat-1)
fi
for CONTAINER in "${CRITICAL_CONTAINERS[@]}"; do
    CPID=$(docker inspect --format '{{.State.Pid}}' "$CONTAINER" 2>/dev/null || echo "")
    if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
        echo -500 > "/proc/$CPID/oom_score_adj" 2>/dev/null || true
    fi
done
echo -e "${GREEN}  ✓ OOM protection set (${CRITICAL_CONTAINERS[*]})${NC}"

# ─── Firewall Hardening (UFW) ────────────────────────────────────────────────
if command -v ufw >/dev/null 2>&1; then
    echo -e "${BLUE}  → Configuring UFW firewall...${NC}"
    ufw default deny incoming >/dev/null 2>&1 || true
    ufw default allow outgoing >/dev/null 2>&1 || true
    ufw allow ssh >/dev/null 2>&1 || true
    ufw allow 80/tcp >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    # Allow FRP if active
    if [ -f "$INSTALL_DIR/.env" ] && grep -q "FRP_AUTH_TOKEN" "$INSTALL_DIR/.env"; then
        ufw allow 7000/tcp >/dev/null 2>&1 || true
    fi
    # Allow Docker Mirror (Option B) if this is the Master/Leader
    if [ -z "${MASTER_IP:-}" ] || [ "$MASTER_IP" = "127.0.0.1" ] || [ "$MASTER_IP" = "$(detect_public_ip)" ]; then
        ufw allow 5001/tcp >/dev/null 2>&1 || true
    fi
    echo "y" | ufw enable >/dev/null 2>&1 || true
    echo -e "${GREEN}  ✓ Firewall hardened (Inbound blocked, SSH/Web permitted)${NC}"
fi

echo -e "${GREEN}  ✓ System security hardening complete${NC}"
    set_checkpoint "memory_hardened"
fi

# -----------------------------------------------------------------------------
# 9. Verification
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[9/9] Verifying Deployment...${NC}"
VERIFY_PASS_COUNT=0
VERIFY_TOTAL=5
sleep 5

if [ "$MODE_AGENT_LITE" = "true" ]; then
VERIFY_TOTAL=4

echo -e "${BLUE}  → [1/4] Verifying Lite Agent compose profile...${NC}"
AGENT_SERVICES="$(docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null || true)"
if printf '%s\n' "$AGENT_SERVICES" | grep -qx "backend" \
   && printf '%s\n' "$AGENT_SERVICES" | grep -qx "celery-worker" \
   && printf '%s\n' "$AGENT_SERVICES" | grep -qx "traefik" \
   && ! printf '%s\n' "$AGENT_SERVICES" | grep -Eq "^(frontend|nginx|db|pgcat|redis|rabbitmq)$"; then
    echo -e "${GREEN}  ✓ Lite Agent profile selected; no frontend/control-plane services included${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Lite Agent compose profile is wrong. Services: ${AGENT_SERVICES//$'\n'/, }${NC}"
fi

echo -e "${BLUE}  → [2/4] Checking Lite Agent containers...${NC}"
RUNNING_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --status running -q 2>/dev/null | wc -l)
TOTAL_COUNT=$(docker compose -f "$COMPOSE_FILE" ps -q 2>/dev/null | wc -l)
if [ "$RUNNING_COUNT" -eq "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt 0 ]; then
    echo -e "${GREEN}  ✓ All $TOTAL_COUNT Lite Agent containers running${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Only $RUNNING_COUNT/$TOTAL_COUNT Lite Agent containers running${NC}"
fi

echo -e "${BLUE}  → [3/4] Checking Lite Agent backend...${NC}"
BACKEND_STATUS="$(docker compose -f "$COMPOSE_FILE" ps backend --format "{{.Status}}" 2>/dev/null || true)"
if echo "$BACKEND_STATUS" | grep -qi "unhealthy"; then
    echo -e "${RED}  ✗ Lite Agent backend is unhealthy${NC}"
    docker compose -f "$COMPOSE_FILE" logs --tail=80 backend 2>/dev/null || true
elif echo "$BACKEND_STATUS" | grep -qiE "healthy|running|Up"; then
    echo -e "${GREEN}  ✓ Lite Agent backend is running${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Lite Agent backend is not healthy/running${NC}"
    docker compose -f "$COMPOSE_FILE" logs --tail=80 backend 2>/dev/null || true
fi

echo -e "${BLUE}  → [4/4] Checking swap...${NC}"
SWAP_TOTAL=$(free -m | awk '/^Swap:/{print $2}')
if [ "$SWAP_TOTAL" -ge 1500 ]; then
    echo -e "${GREEN}  ✓ Swap sufficient (${SWAP_TOTAL}MB)${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  ⚠ Swap low (${SWAP_TOTAL}MB) — recommend 2GB+${NC}"
fi
else
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
# ZH-012 HARDENING: Increased from 12 (1m) to 36 attempts (3m) for slow VPS I/O
MAX_ATTEMPTS=36
for attempt in $(seq 1 $MAX_ATTEMPTS); do
    if curl -sfL http://127.0.0.1:8090/health >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    fi
    echo -ne "\r${YELLOW}  → Health check attempt $attempt/$MAX_ATTEMPTS — waiting...${NC}"
    if [ "$attempt" -eq 5 ]; then
        echo -e "\n${BLUE}  → Restarting Nginx to ensure upstream binding...${NC}"
        docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true
    fi
    sleep 5
done
echo ""

if [ "$HEALTH_OK" = "true" ]; then
    echo -e "${GREEN}  ✓ Health Check Passed!${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Health check failed after $MAX_ATTEMPTS attempts.${NC}"
    dump_diagnostic_logs
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
fi

# Show container status
echo -e "\n${BLUE}Container Status:${NC}"
docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
    docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true

echo -e "\n${BLUE}Verification Score: $VERIFY_PASS_COUNT/$VERIFY_TOTAL${NC}"

# ─── Install Autoscaler as systemd service ──────────────────────────────────
echo -e "${BLUE}  → Installing smsly-autoscaler systemd service...${NC}"
cp "$INSTALL_DIR/scripts/smsly-autoscaler.py" /opt/smsly/autoscaler.py 2>/dev/null || {
    mkdir -p /opt/smsly
    cp "$INSTALL_DIR/scripts/smsly-autoscaler.py" /opt/smsly/autoscaler.py
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

# -----------------------------------------------------------------------------
# 10. CLI Integration
# -----------------------------------------------------------------------------
if [ "$MODE_AGENT_LITE" = "true" ]; then
echo -e "\n${YELLOW}[10/10] Skipping SMSLY CLI on Lite Agent...${NC}"
else
echo -e "\n${YELLOW}[10/10] Integrating SMSLY CLI...${NC}"

if [ -d "$INSTALL_DIR/cli" ]; then
    echo -e "${BLUE}  → Installing 'smsly' CLI command globally...${NC}"
    # Use --break-system-packages for modern Python (Ubuntu 24.04+)
    pip3 install -q --break-system-packages "$INSTALL_DIR/cli" 2>/dev/null || \
        pip3 install -q "$INSTALL_DIR/cli" 2>/dev/null || true

    # Ensure binary is in path (pip usually puts it in /usr/local/bin)
    if command -v smsly &> /dev/null; then
        echo -e "${GREEN}  ✓ CLI installed: run 'smsly login' or 'smsly --help'${NC}"

        # Auto-configuration for local host
        if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ]; then
            URL_SCHEME="https" && [ "$USE_SSL" != "true" ] && URL_SCHEME="http"
            API_URL="${URL_SCHEME}://${DOMAIN}"
        else
            API_URL="http://127.0.0.1:8090"
        fi

        # Best effort: don't auto-login yet (token is in creds file),
        # but let the user know their URL is pre-linked.
        echo -e "${BLUE}  → Your local API URL: $API_URL${NC}"
    else
        echo -e "${YELLOW}  ⚠ CLI installation partially failed (could not find 'smsly' in PATH).${NC}"
    fi
else
    echo -e "${YELLOW}  ⚠ CLI directory not found — skipping integration.${NC}"
fi

# ─── Final Verification Sync ──────────────────────────────────────────────────
fi

if [ "$MODE_AGENT_LITE" != "true" ] && command -v smsly &> /dev/null; then
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    VERIFY_TOTAL=$((VERIFY_TOTAL + 1))
fi

# ─── Remove rollback trap (installation succeeded) ─────────────────────────
trap - EXIT
release_install_lock

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   ✓ INSTALLATION SUCCESSFUL!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"

if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "   Mode:        Lite Agent"
    echo -e "   Agent Edge:  http://$PUBLIC_IP"
    echo -e "   Master:      $MASTER_IP"
else
    if [ "$USE_SSL" = "true" ]; then
        echo -e "   URL:         https://$DOMAIN"
    else
        echo -e "   URL:         http://$PUBLIC_IP"
    fi
    echo -e "   Admin:       /admin"
    echo -e "   Credentials: $CREDENTIALS_FILE"
fi
echo -e "   Install Log: $LOG_FILE"
echo -e "   Location:    $INSTALL_DIR"
echo -e "   Memory:      $(free -m | awk '/^Mem:/{print $7}')MB available"
echo -e "   Swap:        $(free -m | awk '/^Swap:/{print $2}')MB total"
if [ "$MODE_AGENT_LITE" != "true" ]; then
    echo -e "   CLI:         'smsly services list'${NC}"
fi
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  View credentials:   cat $CREDENTIALS_FILE${NC}"
echo -e "${YELLOW}  View logs:          cat $LOG_FILE${NC}"
echo -e "${YELLOW}  Update frontend:    sudo bash install.sh --update-frontend${NC}"
echo -e "${YELLOW}  Update backend:     sudo bash install.sh --update-backend${NC}"
echo -e "${YELLOW}  Full update:        sudo bash install.sh --update${NC}"
echo -e "${YELLOW}  Runtime refresh:    sudo bash install.sh --refresh${NC}"
echo -e "${YELLOW}  Runtime recovery:   sudo bash install.sh --recover${NC}"
echo -e "${YELLOW}  Debug snapshot:     sudo bash install.sh --debug${NC}"
echo -e "${YELLOW}  Wipe install:       sudo bash install.sh --wipe${NC}"

# ─── Conditional Auto-Reboot (only if ALL checks passed) ────────────────────
if [ "$VERIFY_PASS_COUNT" -eq "$VERIFY_TOTAL" ]; then
    echo -e "\n${GREEN}  ✓ All $VERIFY_TOTAL/$VERIFY_TOTAL verification checks passed.${NC}"
    # Normalize NON_INTERACTIVE to true/false for easier shell testing
    _IS_NON_INTERACTIVE=false
    if [[ "${NON_INTERACTIVE:-}" =~ ^(1|true|yes)$ ]]; then _IS_NON_INTERACTIVE=true; fi

    if [ -e /dev/tty ] && [ -z "${SKIP_REBOOT:-}" ] && [ "$_IS_NON_INTERACTIVE" != "true" ]; then
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
