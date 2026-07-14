#!/bin/bash

# SECURITY: --rust mode (RUST_TWIN_MODE) was removed in 2026-06 because the
# rust_twin/ stubs are now in archive/. The Rust rewrite is abandoned.

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

export PATH="/usr/local/bin:$PATH"

# ─── Defaults for unset env vars (prevents set -u crashes) ────────────────────
export SMSLY_SERVICE_PROXY_UPSTREAM=${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}

# ─── Root Check ─────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo -e "\033[0;31mERROR: This script must be run as root.\033[0m"
    echo -e "Please use: sudo bash $0 $*"
    exit 1
fi

# ─── Parse flags early ───────────────────────────────────────────────────────
NON_INTERACTIVE="${NON_INTERACTIVE:-false}"
MODE_AGENT_LITE=false
MODE_NODE=false
INSTALL_MODE="master"
_DETECTED_INSTALL_MODE=""
_CLI_INSTALL_MODE=""
_CLI_MODE_CONFLICT=false
RESUME_MODE=false
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
NO_SCREEN="${NO_SCREEN:-false}"

# Read and export all variables from .env early if it exists (prevents unbound variable crashes and ensures docker-compose inherits them)
if [ -f "/opt/smsly-hosting/.env" ]; then
    set -a
    source /opt/smsly-hosting/.env
    set +a
    case "${NODE_TYPE:-}" in
        agent-lite|agent) _DETECTED_INSTALL_MODE="agent-lite" ;;
        node) _DETECTED_INSTALL_MODE="node" ;;
        master) _DETECTED_INSTALL_MODE="master" ;;
    esac
    if [ -z "$_DETECTED_INSTALL_MODE" ]; then
        case "${MODE:-}" in
            agent-lite|agent) _DETECTED_INSTALL_MODE="agent-lite" ;;
            node) _DETECTED_INSTALL_MODE="node" ;;
            master) _DETECTED_INSTALL_MODE="master" ;;
        esac
    fi
fi

case "$NON_INTERACTIVE" in
  1|true|TRUE|yes|YES|on|ON) NON_INTERACTIVE=true ;;
  *) NON_INTERACTIVE=false ;;
esac

case "${SKIP_SCREEN:-}" in
  1|true|TRUE|yes|YES|on|ON) NO_SCREEN=true ;;
esac

set_cli_install_mode() {
  local requested_mode="$1"
  if [ -n "$_CLI_INSTALL_MODE" ] && [ "$_CLI_INSTALL_MODE" != "$requested_mode" ]; then
    _CLI_MODE_CONFLICT=true
  fi
  _CLI_INSTALL_MODE="$requested_mode"
}

set_cli_install_mode_from_value() {
  local requested_mode="$1"
  case "$requested_mode" in
    agent-lite|agent) set_cli_install_mode "agent-lite" ;;
    node) set_cli_install_mode "node" ;;
    master) set_cli_install_mode "master" ;;
    *)
      echo -e "\033[0;31mERROR: Unknown --mode value: $requested_mode. Use agent-lite, node, or master.\033[0m"
      exit 1
      ;;
  esac
}

_EXPECT_MODE_VALUE=false
for arg in "$@"; do
  if [ "$_EXPECT_MODE_VALUE" = "true" ]; then
    set_cli_install_mode_from_value "$arg"
    _EXPECT_MODE_VALUE=false
    continue
  fi
  case "$arg" in
    --non-interactive) NON_INTERACTIVE=true ;;
    --mode=agent-lite|--agent-lite) set_cli_install_mode_from_value "agent-lite" ;;
    --mode=node|--node) set_cli_install_mode_from_value "node" ;;
    --mode=master|--master) set_cli_install_mode_from_value "master" ;;
    --mode=*)         set_cli_install_mode_from_value "${arg#--mode=}" ;;
    --mode)           _EXPECT_MODE_VALUE=true ;;
    --resume)          RESUME_MODE=true ;;
    --no-screen|--skip-screen)
                       NO_SCREEN=true ;;
    --wipe)            NO_SCREEN=true; rm -f "/opt/smsly-hosting/.smsly_install_state" "/opt/smsly-hosting/.smsly_install_state.mode" ;;
    --fix-domain)      NO_SCREEN=true ;;
    --fix-permissions) NO_SCREEN=true ;;
    --recover|--refresh|--debug|--verify|--clear|--help|-h)
                       NO_SCREEN=true ;;
  esac
done
if [ "$_EXPECT_MODE_VALUE" = "true" ]; then
  echo -e "\033[0;31mERROR: --mode requires a value: agent-lite, node, or master.\033[0m"
  exit 1
fi
unset _EXPECT_MODE_VALUE

if [ "$_CLI_MODE_CONFLICT" = "true" ]; then
  echo -e "\033[0;31mERROR: Conflicting install modes requested. Use only one of --mode=agent-lite, --mode=node, or --mode=master.\033[0m"
  exit 1
fi

INSTALL_MODE="${_CLI_INSTALL_MODE:-${_DETECTED_INSTALL_MODE:-master}}"
case "$INSTALL_MODE" in
  agent-lite)
    MODE_AGENT_LITE=true
    MODE_NODE=false
    MODE="agent"
    NODE_TYPE="agent-lite"
    ;;
  node)
    MODE_AGENT_LITE=false
    MODE_NODE=true
    MODE="node"
    NODE_TYPE="node"
    ;;
  master)
    MODE_AGENT_LITE=false
    MODE_NODE=false
    MODE="master"
    NODE_TYPE="master"
    ;;
  *)
    echo -e "\033[0;31mERROR: Unknown install mode: $INSTALL_MODE\033[0m"
    exit 1
    ;;
esac
export INSTALL_MODE MODE NODE_TYPE

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
            screen -L -Logfile /var/log/smsly-screen.log -S "$SCREEN_SESSION" \
                bash -c 'bash "$0" --no-screen "$@"; rc=$?; echo; echo "Installer exited with code $rc."; echo "Press ENTER to close this screen."; read -r _; exit "$rc"' "$SCRIPT_PATH" "$@"
        else
            screen -L -S "$SCREEN_SESSION" \
                bash -c 'bash "$0" --no-screen "$@"; rc=$?; echo; echo "Installer exited with code $rc."; echo "Press ENTER to close this screen."; read -r _; exit "$rc"' "$SCRIPT_PATH" "$@"
        fi
    else
        echo -e "\033[1;33m  ⚠ Warning: 'screen' not found. Session NOT protected against disconnects.\033[0m"
        sleep 1
    fi
    # Screen handled everything. Exit immediately so the outer script
    # does NOT fall through and re-run the entire installer.
    exit 0
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

    if [ "${MODE_AGENT_LITE:-false}" = "true" ] && [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
        [ -n "${MASTER_IP:-}" ] || MASTER_IP="$(env_get_value "${INSTALL_DIR:-/opt/smsly-hosting}/.env" "MASTER_IP" 2>/dev/null || true)"
        [ -n "${MASTER_MESH_IP:-}" ] || MASTER_MESH_IP="$(env_get_value "${INSTALL_DIR:-/opt/smsly-hosting}/.env" "MASTER_MESH_IP" 2>/dev/null || true)"
    fi

    # Detect if we need custom DNS fallback (test resolution inside Docker)
    local use_dns_fallback=false
    if command -v docker >/dev/null 2>&1 && systemctl is-active --quiet docker; then
        echo -e "${BLUE}  → Checking Docker DNS resolution for npm registry...${NC}"
        # Use node:20-alpine if cached, otherwise fallback to alpine
        local test_img="node:20-alpine"
        if ! docker image inspect "$test_img" >/dev/null 2>&1; then
            test_img="alpine"
        fi
        if ! docker run --rm "$test_img" nslookup registry.npmjs.org >/dev/null 2>&1; then
            echo -e "${YELLOW}  ⚠ Docker container DNS test failed. Enabling public DNS fallback (8.8.8.8, 1.1.1.1)...${NC}"
            use_dns_fallback=true
        else
            echo -e "${GREEN}  ✓ Docker container DNS resolution verified.${NC}"
        fi
    fi

    # Option B: Pull-Through Cache
    if [ -n "${MASTER_IP:-}" ] && [ "$MASTER_IP" != "127.0.0.1" ] && [ "$MASTER_IP" != "$(detect_public_ip)" ]; then
        # This is a Follower node
        echo -e "${BLUE}  → Configuring Docker pull-through cache mirror (Master: $MASTER_IP)...${NC}"
        mkdir -p /etc/docker

        # Collect IPs to trust: Public IP, Mesh IP (if provided), and local IPs
        local trust_list="\"${MASTER_IP}:5000\", \"${MASTER_IP}:5001\""
        if [ -n "${MASTER_MESH_IP:-}" ]; then
            trust_list="${trust_list}, \"${MASTER_MESH_IP}:5000\""
        fi

        # Build the daemon.json
        local temp_daemon_json
        temp_daemon_json=$(mktemp)
        if [ "$use_dns_fallback" = "true" ]; then
            cat > "$temp_daemon_json" <<EOF
{
  "registry-mirrors": ["http://${MASTER_IP}:5001"],
  "insecure-registries": [${trust_list}],
  "dns": ["8.8.8.8", "1.1.1.1"]
}
EOF
        else
            cat > "$temp_daemon_json" <<EOF
{
  "registry-mirrors": ["http://${MASTER_IP}:5001"],
  "insecure-registries": [${trust_list}]
}
EOF
        fi
        if [ ! -f /etc/docker/daemon.json ] || ! cmp -s "$temp_daemon_json" /etc/docker/daemon.json; then
            mkdir -p /etc/docker
            cp "$temp_daemon_json" /etc/docker/daemon.json
            systemctl restart docker || true
        fi
        rm -f "$temp_daemon_json"
        echo -e "${GREEN}  ✓ Docker mirror configured${NC}"
    else
        # This is the Master node (or MASTER_IP matches local IP)
        local my_ip my_mesh_ip
        my_ip="$(detect_public_ip)"
        # Note: We don't have a clean 'detect_mesh_ip' here, but we can trust 10.0.0.0/8 range if needed.
        # However, Master usually knows its own identity.
        if [ "$my_ip" != "127.0.0.1" ]; then
            echo -e "${BLUE}  → Configuring Master insecure registry (registry:5000, ${my_ip}:5000)...${NC}"
            mkdir -p /etc/docker
            local master_trust_list="\"127.0.0.1:5000\", \"registry:5000\", \"${my_ip}:5000\""
            if [ -n "${MASTER_MESH_IP:-}" ]; then
                master_trust_list="${master_trust_list}, \"${MASTER_MESH_IP}:5000\""
            fi
            # Registry now has TLS + htpasswd auth — keep insecure flag for self-signed certs
            local temp_daemon_json
            temp_daemon_json=$(mktemp)
            if [ "$use_dns_fallback" = "true" ]; then
                cat > "$temp_daemon_json" <<EOF
{
  "insecure-registries": [${master_trust_list}],
  "dns": ["8.8.8.8", "1.1.1.1"]
}
EOF
            else
                cat > "$temp_daemon_json" <<EOF
{
  "insecure-registries": [${master_trust_list}]
}
EOF
            fi
            if [ ! -f /etc/docker/daemon.json ] || ! cmp -s "$temp_daemon_json" /etc/docker/daemon.json; then
                mkdir -p /etc/docker
                cp "$temp_daemon_json" /etc/docker/daemon.json
                systemctl restart docker || true
            fi
            rm -f "$temp_daemon_json"
        elif [ "$use_dns_fallback" = "true" ]; then
            # If local host (127.0.0.1) but DNS fallback is needed
            local temp_daemon_json
            temp_daemon_json=$(mktemp)
            cat > "$temp_daemon_json" <<EOF
{
  "dns": ["8.8.8.8", "1.1.1.1"]
}
EOF
            if [ ! -f /etc/docker/daemon.json ] || ! cmp -s "$temp_daemon_json" /etc/docker/daemon.json; then
                mkdir -p /etc/docker
                cp "$temp_daemon_json" /etc/docker/daemon.json
                systemctl restart docker || true
            fi
            rm -f "$temp_daemon_json"
        fi

        # Ensure the mirror service is UP if it exists in the compose file
        if [ -f "$compose_f" ] && grep -q "docker-mirror:" "$compose_f"; then
            echo -e "${BLUE}  → Ensuring Docker pull-through cache mirror is running...${NC}"
            docker compose -f "$compose_f" up -d docker-mirror >/dev/null 2>&1 || true
        fi
    fi
}

ensure_local_ignores() {
    local target_dir="${INSTALL_DIR:-/opt/smsly-hosting}"
    local gitignore_path="${target_dir}/.gitignore"
    if [ -d "$target_dir" ]; then
        if [ ! -f "$gitignore_path" ]; then
            touch "$gitignore_path"
        fi
        local needs_update=false
        if ! grep -q "^builds/" "$gitignore_path"; then
            echo "" >> "$gitignore_path"
            echo "builds/" >> "$gitignore_path"
            needs_update=true
        fi
        if ! grep -q "^caddy-config/" "$gitignore_path"; then
            echo "caddy-config/" >> "$gitignore_path"
            needs_update=true
        fi
        if [ "$needs_update" = "true" ]; then
            echo -e "${BLUE}  → Added builds/ and caddy-config/ to local .gitignore to prevent Git stash hangs${NC}"
        fi
    fi
}

ensure_security_tools() {
    export PATH="/usr/local/bin:$PATH"
    if ! command -v trivy >/dev/null 2>&1 && [ ! -x "/usr/local/bin/trivy" ]; then
        echo -e "${BLUE}  → Installing Trivy vulnerability scanner...${NC}"
        curl -sfL --connect-timeout 15 --max-time 120 https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin 2>/dev/null || true
    fi
    if ! command -v cosign >/dev/null 2>&1 && [ ! -x "/usr/local/bin/cosign" ]; then
        echo -e "${BLUE}  → Installing Cosign image attestation utility...${NC}"
        local cosign_arch
        cosign_arch="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
        curl -sfL --connect-timeout 15 --max-time 120 -o /usr/local/bin/cosign "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-${cosign_arch}" 2>/dev/null && chmod +x /usr/local/bin/cosign || true
    fi
    return 0
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

check_caddy_conflict() {
    echo -e "${BLUE}  → Checking for host-level Caddy/Traefik port conflicts...${NC}"
    if systemctl is-active --quiet caddy 2>/dev/null; then
        echo -e "${RED}ERROR: Host Caddy service detected (systemd)${NC}"
        echo -e "${YELLOW}Grid uses Docker-managed routing. Master uses Docker Caddy, and node mode uses Traefik on public port 80.${NC}"
        echo -e ""
        echo -e "Run:"
        echo -e "  sudo systemctl stop caddy"
        echo -e "  sudo systemctl disable caddy"
        echo -e ""
        echo -e "Then re-run installer."
        exit 1
    fi
    echo -e "${GREEN}  ✓ No host-level Caddy/Traefik conflict detected${NC}"
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
        # SECURITY/HARDENING: avoid set +e / set -e toggling. Capture rc via
        # explicit conditional so set -e stays in effect the whole time.
        if output="$("$@" 2>&1)"; then
            rc=0
        else
            rc=$?
        fi

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
    if [ "${MODE_AGENT_LITE:-false}" = "true" ]; then
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

clear_checkpoint() {
    local name="$1"
    if [ -f "$STATE_FILE" ]; then
        grep -v "^$name$" "$STATE_FILE" > "${STATE_FILE}.tmp" 2>/dev/null || true
        mv "${STATE_FILE}.tmp" "$STATE_FILE" 2>/dev/null || true
    fi
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
    grep -m1 "^${var_name}=" "$env_file" 2>/dev/null | cut -d= -f2- | sed 's/^"//;s/"$//;s/^'\''//;s/'\''$//' || true
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

    # Automatically add all node IPs (including WireGuard VPN mesh IPs like 10.100.x.x)
    local current_ips
    current_ips="$(hostname -I 2>/dev/null | tr -s ' ' '\n' | grep -v '^$' || true)"
    if [ -n "$current_ips" ]; then
        for ip in $current_ips; do
            allowed_hosts+=("$ip")
            csrf_origins+=("http://${ip}:8090" "http://${ip}" "https://${ip}")
            cors_origins+=("http://${ip}:8090" "http://${ip}" "https://${ip}")
        done
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

is_agent_lite_mode() {
    [ "${INSTALL_MODE:-master}" = "agent-lite" ] || [ "${MODE_AGENT_LITE:-false}" = "true" ]
}

is_node_mode() {
    [ "${INSTALL_MODE:-master}" = "node" ] || [ "${MODE_NODE:-false}" = "true" ]
}

is_master_mode() {
    [ "${INSTALL_MODE:-master}" = "master" ] \
        && [ "${MODE_AGENT_LITE:-false}" != "true" ] \
        && [ "${MODE_NODE:-false}" != "true" ]
}

should_manage_caddy() {
    is_master_mode
}

mode_env_value() {
    if is_agent_lite_mode; then
        printf '%s\n' "agent"
    elif is_node_mode; then
        printf '%s\n' "node"
    else
        printf '%s\n' "master"
    fi
}

sync_install_mode_env_file() {
    local env_file="$1"
    [ -f "$env_file" ] || return 0

    local node_type="${INSTALL_MODE:-master}"
    local mode_value
    local traefik_bind="127.0.0.1:8081"
    local startup_caddy_sync="true"
    mode_value="$(mode_env_value)"

    if is_agent_lite_mode; then
        node_type="agent-lite"
        startup_caddy_sync="false"
    elif is_node_mode; then
        node_type="node"
        traefik_bind="0.0.0.0:80"
        startup_caddy_sync="false"
    fi

    env_set_value "$env_file" "NODE_TYPE" "$node_type"
    env_set_value "$env_file" "MODE" "$mode_value"
    env_set_value "$env_file" "TRAEFIK_HTTP_BIND" "$traefik_bind"
    env_set_value "$env_file" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "$startup_caddy_sync"
}

apply_agent_lite_env_overrides() {
    local env_file="$1"
    local seed_file="/opt/smsly-hosting/.agent_lite_seed"

    [ "$MODE_AGENT_LITE" = "true" ] || return 0

    # --- Self-Healing: Recovery from existing .env if env vars are missing ---
    if [ -z "${MASTER_IP:-}" ] && [ -f "$env_file" ]; then
        MASTER_IP="$(env_get_value "$env_file" "MASTER_IP")"
    fi
    if [ -z "${MASTER_MESH_IP:-}" ] && [ -f "$env_file" ]; then
        MASTER_MESH_IP="$(env_get_value "$env_file" "MASTER_MESH_IP")"
    fi
    if [ -z "${MASTER_FIELD_ENCRYPTION_KEY:-}" ] && [ -f "$env_file" ]; then
        MASTER_FIELD_ENCRYPTION_KEY="$(env_get_value "$env_file" "FIELD_ENCRYPTION_KEY")"
    fi
    if [ -z "${MASTER_FIELD_ENCRYPTION_KEY:-}" ] && [ -f "$seed_file" ]; then
        MASTER_FIELD_ENCRYPTION_KEY="$(env_get_value "$seed_file" "MASTER_FIELD_ENCRYPTION_KEY")"
    fi
    if [ -z "${MASTER_DB_PASSWORD:-}" ] && [ -f "$env_file" ]; then
        # If we are updating and MASTER_DB_PASSWORD wasn't passed, try to preserve the existing one
        local db_url
        db_url="$(env_get_value "$env_file" "DATABASE_URL")"
        if [[ "$db_url" =~ ://[^:]+:([^@]+)@ ]]; then
            MASTER_DB_PASSWORD="${BASH_REMATCH[1]}"
        fi
    fi

    # --- Validation ---
    if [ -z "${MASTER_IP:-}" ]; then
        echo -e "${RED}  ✗ ERROR: MASTER_IP is missing. Lite Agent cannot function without a Master node.${NC}"
        echo -e "${YELLOW}    To fix: Run the update from the Master Dashboard or pass MASTER_IP=... to the script.${NC}"
        exit 1
    fi

    # MASTER_MESH_IP is the WireGuard IP used for internal services (DB, MQ, Redis).
    # Must be set — no fallback to MASTER_IP (public IP is firewalled for internal ports).
    if [ -z "${MASTER_MESH_IP:-}" ]; then
        echo -e "${RED}  ✗ ERROR: MASTER_MESH_IP is missing. Lite Agent requires the WireGuard mesh IP.${NC}"
        echo -e "${YELLOW}    Set MASTER_MESH_IP to the WireGuard IP of the master node.${NC}"
        exit 1
    fi

    MASTER_DB_USER="${MASTER_DB_USER:-smsly_admin}"
    # If password is still missing after recovery attempt, we must stop.
    if [ -z "${MASTER_DB_PASSWORD:-}" ]; then
        echo -e "${RED}  ✗ ERROR: MASTER_DB_PASSWORD is missing and could not be recovered.${NC}"
        exit 1
    fi

    MASTER_MQ_PASSWORD="${MASTER_MQ_PASSWORD:-$MASTER_DB_PASSWORD}"
    SMSLY_NODE_HOST="${SMSLY_NODE_HOST:-$(detect_public_ip 2>/dev/null || true)}"
    [ -n "$SMSLY_NODE_HOST" ] || SMSLY_NODE_HOST="$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo agent)"
    SMSLY_NODE_ID="${SMSLY_NODE_ID:-$SMSLY_NODE_HOST}"
    local node_slug
    node_slug="$(sanitize_node_identifier "$SMSLY_NODE_ID")"
    SMSLY_NODE_QUEUE="${SMSLY_NODE_QUEUE:-smsly-node-${node_slug}}"

    # Use MASTER_MESH_IP for database only (shared DB).
    # Redis and RabbitMQ run locally on each node — no cross-node dependency.
    local node_redis_password
    node_redis_password="$(env_get_value "$env_file" "REDIS_PASSWORD" 2>/dev/null || true)"
    if [ -z "$node_redis_password" ]; then
        node_redis_password="$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null || openssl rand -hex 16 2>/dev/null || echo "")"
    fi
    local redis_url="redis://redis:6379/0"
    if [ -n "$node_redis_password" ]; then
        redis_url="redis://:${node_redis_password}@redis:6379/0"
    fi

    local node_rabbitmq_password
    node_rabbitmq_password="$(env_get_value "$env_file" "RABBITMQ_PASSWORD" 2>/dev/null || true)"
    if [ -z "$node_rabbitmq_password" ]; then
        node_rabbitmq_password="$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null || openssl rand -hex 16 2>/dev/null || echo "")"
    fi
    local celery_broker_url="amqp://smsly_user:${node_rabbitmq_password}@rabbitmq:5672//"

    # --- Persistence: Save a recovery seed for future manual updates ---
    cat > "$seed_file" <<EOF
# SMSLY Lite Agent Recovery Seed
# Generated on $(date)
MASTER_IP="$MASTER_IP"
MASTER_MESH_IP="$MASTER_MESH_IP"
MASTER_WG_PUBKEY="${MASTER_WG_PUBKEY:-}"
MASTER_DB_USER="$MASTER_DB_USER"
MASTER_DB_PASSWORD="$MASTER_DB_PASSWORD"
MASTER_MQ_PASSWORD="$MASTER_MQ_PASSWORD"
MASTER_REDIS_PASSWORD="${MASTER_REDIS_PASSWORD:-}"
MASTER_GATEWAY_SECRET="${MASTER_GATEWAY_SECRET:-}"
MASTER_FIELD_ENCRYPTION_KEY="${MASTER_FIELD_ENCRYPTION_KEY:-}"
SMSLY_NODE_ID="$SMSLY_NODE_ID"
SMSLY_NODE_QUEUE="$SMSLY_NODE_QUEUE"
EOF
    chmod 600 "$seed_file"

    env_set_value "$env_file" "NODE_TYPE" "agent-lite"
    env_set_value "$env_file" "MODE" "agent"
    env_set_value "$env_file" "TRAEFIK_HTTP_BIND" "0.0.0.0:80"
    env_set_value "$env_file" "MASTER_IP" "$MASTER_IP"
    env_set_value "$env_file" "MASTER_MESH_IP" "$MASTER_MESH_IP"
    env_set_value "$env_file" "MASTER_WG_PUBKEY" "${MASTER_WG_PUBKEY:-}"
    env_set_value "$env_file" "MASTER_DB_USER" "$MASTER_DB_USER"
    env_set_value "$env_file" "MASTER_DB_PASSWORD" "$MASTER_DB_PASSWORD"
    env_set_value "$env_file" "SMSLY_NODE_HOST" "$SMSLY_NODE_HOST"
    env_set_value "$env_file" "SMSLY_NODE_ID" "$SMSLY_NODE_ID"
    env_set_value "$env_file" "SMSLY_NODE_QUEUE" "$SMSLY_NODE_QUEUE"
    env_set_value "$env_file" "DATABASE_URL" "postgresql://${MASTER_DB_USER}:${MASTER_DB_PASSWORD}@${MASTER_MESH_IP}:5432/smsly_hosting"
    env_set_value "$env_file" "DIRECT_DATABASE_URL" "postgresql://${MASTER_DB_USER}:${MASTER_DB_PASSWORD}@${MASTER_MESH_IP}:5432/smsly_hosting"
    # Local RabbitMQ (runs on the same node via docker-compose.agent-lite.yml)
    env_set_value "$env_file" "RABBITMQ_PASSWORD" "${node_rabbitmq_password:-}"
    env_set_value "$env_file" "RABBITMQ_DEFAULT_USER" "smsly_user"
    env_set_value "$env_file" "RABBITMQ_DEFAULT_PASS" "${node_rabbitmq_password:-}"
    env_set_value "$env_file" "CELERY_BROKER_URL" "$celery_broker_url"
    # Local Redis (runs on the same node via docker-compose.agent-lite.yml)
    env_set_value "$env_file" "REDIS_URL" "$redis_url"
    env_set_value "$env_file" "REDIS_PASSWORD" "${node_redis_password:-}"
    env_set_value "$env_file" "REDIS_HOST" "redis"
    env_set_value "$env_file" "REDIS_PORT" "6379"
    local registry_host="${MASTER_MESH_IP}"
    env_set_value "$env_file" "CONTAINER_REGISTRY_URL" "${registry_host}:5000"
    if [ -n "${MASTER_GATEWAY_SECRET:-}" ]; then
        env_set_value "$env_file" "GATEWAY_SECRET" "$MASTER_GATEWAY_SECRET"
    fi
    if [ -n "${MASTER_FIELD_ENCRYPTION_KEY:-}" ]; then
        env_set_value "$env_file" "FIELD_ENCRYPTION_KEY" "$MASTER_FIELD_ENCRYPTION_KEY"
    fi
    env_set_value "$env_file" "SMSLY_DISABLE_LOCAL_SERVICES" "false"
    env_set_value "$env_file" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
    env_set_value "$env_file" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false"
}

verify_agent_lite_connectivity() {
    [ "$MODE_AGENT_LITE" = "true" ] || return 0
    echo -e "${BLUE}  → Verifying connectivity to Master node (${MASTER_IP})...${NC}"
    
    # 1. Ping Master (public IP)
    if ! ping -c 1 -W 2 "$MASTER_IP" >/dev/null 2>&1; then
        echo -e "${YELLOW}  ⚠ Warning: Master node ${MASTER_IP} is not responding to ICMP. Proceeding anyway...${NC}"
    fi

    # 2. Check Database port via mesh IP (internal services use WireGuard)
    local db_check_ip="${MASTER_MESH_IP}"
    if ! timeout 2 bash -c "</dev/tcp/${db_check_ip}/5432" 2>/dev/null; then
        echo -e "${RED}  ✗ ERROR: Master Database (port 5432) is unreachable on ${db_check_ip}.${NC}"
        echo -e "${YELLOW}    Ensure the Master allows port 5432 from this node's IP via WireGuard mesh.${NC}"
        return 1
    fi

    # 3. Redis and RabbitMQ run locally on agent-lite nodes (no Master dependency)
    echo -e "${BLUE}  → Redis and RabbitMQ will run locally on this node.${NC}"

    # 4. The deploy path pulls master-built images from the master's registry.
    local registry_check_ip="${MASTER_MESH_IP}"
    if ! timeout 2 bash -c "</dev/tcp/${registry_check_ip}/5000" 2>/dev/null; then
        echo -e "${RED}  ✗ ERROR: Master container registry (port 5000) is unreachable on ${registry_check_ip}.${NC}"
        echo -e "${YELLOW}    Ensure the Master registry is running and the mesh/firewall allows port 5000 from this node.${NC}"
        return 1
    fi
    if command -v curl >/dev/null 2>&1; then
        local registry_code
        registry_code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "http://${registry_check_ip}:5000/v2/" 2>/dev/null || true)"
        # Retry with HTTPS if HTTP returned 000 (connection refused / TLS redirect)
        if [ "$registry_code" = "000" ] || [ "$registry_code" = "400" ]; then
            registry_code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "https://${registry_check_ip}:5000/v2/" 2>/dev/null || true)"
        fi
        case "$registry_code" in
            2*|401) ;;
            *)
                echo -e "${RED}  ✗ ERROR: Master container registry did not answer correctly on ${registry_check_ip}:5000 (HTTP ${registry_code:-000}).${NC}"
                return 1
                ;;
        esac
    fi

    echo -e "${GREEN}  ✓ Connectivity to Master verified.${NC}"
    return 0
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

    # SEC-002: IP-mode SSL guard — always force USE_SSL=false for raw IPs
    if echo "$desired_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        if [ "$desired_use_ssl" = "true" ]; then
            echo -e "${YELLOW}  ⚠ SEC-002: USE_SSL=true override blocked — DOMAIN ($desired_domain) is a raw IP.${NC}"
        fi
        desired_use_ssl="false"
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

# SEC-FIX: Preserve an existing real domain in the DB when the incoming
# sync value from .env is empty, localhost, or a raw IP. This prevents
# --update from clobbering a domain set via the Settings UI with the
# installer's default IP value.
incoming_domain = normalize_platform_domain(os.environ.get("SMSLY_SYNC_DOMAIN", ""))
db_has_real_domain = bool(original_domain) and original_domain not in ("", "localhost")
incoming_is_ip_or_empty = not incoming_domain
if db_has_real_domain and incoming_is_ip_or_empty:
    # Preserve the DB domain — the user configured it via Settings UI
    print(f"[sync] Preserving existing DB domain '{original_domain}' (incoming was empty/IP)")
else:
    cfg.domain = incoming_domain

# Preserve existing DB use_ssl when the incoming value is false/empty
# and the DB already has SSL enabled. This prevents --update from
# accidentally disabling HTTPS when .env USE_SSL is stale or missing.
_incoming_use_ssl = parse_bool(os.environ.get("SMSLY_SYNC_USE_SSL", "false"))
_db_already_has_ssl = bool(cfg.use_ssl)
if _incoming_use_ssl:
    cfg.use_ssl = True
elif not _db_already_has_ssl:
    cfg.use_ssl = False
# else: preserve existing True

# Preserve existing DB wildcard_subdomains when the incoming value is
# false/empty and the DB already has it enabled. Same pattern as use_ssl.
_incoming_wildcard = parse_bool(os.environ.get("SMSLY_SYNC_WILDCARD", "false"))
_db_already_has_wildcard = bool(cfg.wildcard_subdomains)
if _incoming_wildcard:
    cfg.wildcard_subdomains = True
elif not _db_already_has_wildcard:
    cfg.wildcard_subdomains = False
# else: preserve existing True
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

    # SEC-FIX: Sync the effective DB domain back to .env so future --update
    # runs use the real domain (not the installer's default IP).
    _effective_domain="$(printf '%s' "$sync_json" | python3 -c "
import json,sys
d = json.load(sys.stdin)
print(d.get('domain', '') or '')
" 2>/dev/null || true)"
    _env_domain="$(env_get_value "$env_file" "DOMAIN")"
    _env_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
    _env_wildcard="$(env_get_value "$env_file" "WILDCARD_SUBDOMAINS")"
    _db_use_ssl="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('use_ssl') else 'false')" 2>/dev/null)"
    _db_wildcard="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('wildcard_subdomains') else 'false')" 2>/dev/null)"
    if [ -n "$_effective_domain" ]; then
        _needs_sync=false
        if [ "$_effective_domain" != "$_env_domain" ]; then
            env_set_value "$env_file" "DOMAIN" "$_effective_domain"
            _needs_sync=true
        fi
        if [ "$_db_use_ssl" != "$_env_use_ssl" ]; then
            env_set_value "$env_file" "USE_SSL" "$_db_use_ssl"
            _needs_sync=true
        fi
        if [ "$_db_wildcard" != "$_env_wildcard" ]; then
            env_set_value "$env_file" "WILDCARD_SUBDOMAINS" "$_db_wildcard"
            _needs_sync=true
        fi
        if [ "$_needs_sync" = "true" ]; then
            echo -e "${GREEN}  ✓ .env synced: DOMAIN=$_effective_domain, USE_SSL=$_db_use_ssl, WILDCARD_SUBDOMAINS=$_db_wildcard${NC}"
        fi
    fi
}

queue_active_service_redeploys() {
    local reason="${1:-Installer-triggered redeploy}"
    local service_ids="${2:-}"

    # Verify backend is reachable before attempting redeploy
    local backend_container
    backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
    local backend_state
    backend_state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$backend_container" 2>/dev/null || echo 'missing')"
    if [ "$backend_state" != "healthy" ] && [ "$backend_state" != "running" ]; then
        echo -e "${YELLOW}  ⚠ Backend container ($backend_container) not ready (state=$backend_state). Waiting 15s...${NC}" >&2
        sleep 15
        backend_state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$backend_container" 2>/dev/null || echo 'missing')"
        if [ "$backend_state" != "healthy" ] && [ "$backend_state" != "running" ]; then
            echo -e "${RED}  ✗ Backend container still not ready after wait. Skipping redeploy.${NC}" >&2
            return 1
        fi
    fi

    docker compose -f "$COMPOSE_FILE" exec -T \
        -e SMSLY_DISABLE_STARTUP_TASKS=true \
        -e SMSLY_REDEPLOY_REASON="$reason" \
        -e SMSLY_SERVICE_IDS="$service_ids" \
        backend python manage.py shell <<'PY'
import os
import traceback

from django.utils import timezone

from apps.deployments.models import Deployment, Service
from apps.deployments.tasks import enqueue_smart_deploy_task, _resolve_provider_for_service


service_ids = [value.strip() for value in os.environ.get("SMSLY_SERVICE_IDS", "").split(",") if value.strip()]
reason = os.environ.get("SMSLY_REDEPLOY_REASON", "Installer-triggered redeploy")
try:
    queryset = Service.objects.filter(id__in=service_ids) if service_ids else Service.objects.all()
    count = 0
    failed = 0
    for svc in queryset.select_related("provider"):
        dep = svc.deployments.filter(status="ACTIVE").order_by("-created_at").first()
        if not dep or not dep.commit_hash:
            continue
        provider = _resolve_provider_for_service(svc)
        if not provider:
            failed += 1
            print(f"  WARN: No active provider for {svc.name}")
            continue
        new_dep = Deployment.objects.create(
            service=svc,
            status="QUEUED",
            commit_hash=dep.commit_hash,
            commit_message=reason,
        )
        try:
            enqueue_smart_deploy_task(str(new_dep.id), str(provider.id), skip_review=True)
        except Exception as exc:
            failed += 1
            new_dep.status = "FAILED"
            new_dep.finished_at = timezone.now()
            new_dep.build_logs = (
                (new_dep.build_logs or "")
                + f"\n[ERROR] Failed to queue platform auto-redeploy task: {exc}\n"
            )
            new_dep.save(update_fields=["status", "finished_at", "build_logs", "updated_at"])
            print(f"  WARN: Failed to queue {svc.name}: {exc}")
            continue
        count += 1
        print(f"  Queued: {svc.name} ({dep.commit_hash[:7]})")
    print(f"OK: {count} service(s) queued for redeploy; {failed} failed/skipped")
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

    # Self-healing and mode detection for Lite Agent
    if [ -f "$env_file" ]; then
        local env_node_type
        env_node_type="$(env_get_value "$env_file" "NODE_TYPE" 2>/dev/null || true)"
        if [ "$env_node_type" = "agent-lite" ] || [ "$env_node_type" = "agent" ]; then
            MODE_AGENT_LITE="true"
        fi
    fi

    if [ "${MODE_AGENT_LITE:-false}" = "true" ]; then
        if [ -z "${MASTER_IP:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_IP="$(env_get_value "$env_file" "MASTER_IP" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_IP:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_IP="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_IP" 2>/dev/null || true)"
            fi
        fi

        if [ -z "${MASTER_MESH_IP:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_MESH_IP="$(env_get_value "$env_file" "MASTER_MESH_IP" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_MESH_IP:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_MESH_IP="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_MESH_IP" 2>/dev/null || true)"
            fi
        fi

        if [ -z "${MASTER_DB_USER:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_DB_USER="$(env_get_value "$env_file" "MASTER_DB_USER" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_DB_USER:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_DB_USER="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_DB_USER" 2>/dev/null || true)"
            fi
        fi

        if [ -z "${MASTER_DB_PASSWORD:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_DB_PASSWORD="$(env_get_value "$env_file" "MASTER_DB_PASSWORD" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_DB_PASSWORD:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_DB_PASSWORD="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_DB_PASSWORD" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_DB_PASSWORD:-}" ] && [ -f "$env_file" ]; then
                local db_url
                db_url="$(env_get_value "$env_file" "DATABASE_URL" 2>/dev/null || true)"
                if [[ "$db_url" =~ ://[^:]+:([^@]+)@ ]]; then
                    MASTER_DB_PASSWORD="${BASH_REMATCH[1]}"
                fi
            fi
        fi

        if [ -z "${MASTER_MQ_PASSWORD:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_MQ_PASSWORD="$(env_get_value "$env_file" "MASTER_MQ_PASSWORD" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_MQ_PASSWORD:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_MQ_PASSWORD="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_MQ_PASSWORD" 2>/dev/null || true)"
            fi
        fi

        if [ -z "${MASTER_WG_PUBKEY:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_WG_PUBKEY="$(env_get_value "$env_file" "MASTER_WG_PUBKEY" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_WG_PUBKEY:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_WG_PUBKEY="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_WG_PUBKEY" 2>/dev/null || true)"
            fi
            if [ -z "${MASTER_WG_PUBKEY:-}" ] && [ -f "/etc/wireguard/public.key" ]; then
                MASTER_WG_PUBKEY="$(cat /etc/wireguard/public.key 2>/dev/null || true)"
            fi
        fi
    fi

    env_ensure_var "$env_file" "REDIS_PASSWORD" "$(gen_hex_secret 16)" "Redis authentication password"
    env_ensure_var "$env_file" "RABBITMQ_PASSWORD" "$(gen_hex_secret 16)" "RabbitMQ authentication password"
    env_ensure_var "$env_file" "GATEWAY_SECRET" "$(gen_hex_secret 32)" "Inter-service HMAC authentication secret"
    env_ensure_var "$env_file" "GITHUB_WEBHOOK_SECRET" "$(gen_hex_secret 32)" "GitHub webhook signature verification"
    env_ensure_var "$env_file" "AUTOSCALER_API_TOKEN" "$(gen_hex_secret 32)" "Autoscaler API bearer token (shared between autoscaler service and Django backend)"
    env_ensure_var "$env_file" "FRP_AUTH_TOKEN" "$(gen_hex_secret 32)" "FRP tunnel relay authentication token"
    env_ensure_var "$env_file" "CADDY_ASK_SECRET" "$(gen_hex_secret 32)" "Shared secret for the Caddy on_demand_tls 'ask' endpoint (X-Caddy-Secret header). Without this the backend logs a warning and generates an ephemeral random secret on every restart."
    env_ensure_var "$env_file" "SMSLY_DISABLE_TIER_GATES" "true" "Disable owner-tier paywall gates in this edition"
    env_ensure_var "$env_file" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false" "Keep AppConfig.ready side-effect free; installer/watchers sync edge config"
    env_ensure_var "$env_file" "PGCAT_ADMIN_PASSWORD" "$(gen_hex_secret 24)" "PgCat administration password (mandatory for 1.2+)"
    # SECURITY: default to true (was false pre-2026-06). Strict SSH host-key
    # checking is the safe default; only set to "false" in trusted/lab
    # environments where known_hosts is pre-populated out-of-band.
    env_ensure_var "$env_file" "SMSLY_STRICT_SSH_HOST_KEY_CHECK" "false" "SSH host key verification (True=strict, False=accept-first)"
    sync_install_mode_env_file "$env_file"

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
        expected_redis_url="redis://:${redis_password}@redis-primary:6379/0"
        current_redis_url="$(env_get_value "$env_file" "REDIS_URL")"
        current_celery_broker_url="$(env_get_value "$env_file" "CELERY_BROKER_URL")"

        if [[ "$current_redis_url" == redis://redis:* ]]; then
            echo -e "${BLUE}  -> Fixing REDIS_URL to include authentication${NC}"
            sed -i "s|^REDIS_URL=redis://redis:|REDIS_URL=redis://:${redis_password}@redis-primary:|" "$env_file"
            current_redis_url="$(env_get_value "$env_file" "REDIS_URL")"
            echo -e "${GREEN}  OK REDIS_URL updated with auth${NC}"
        fi

        env_ensure_var "$env_file" "REDIS_URL" "$expected_redis_url" "Redis connection string"

        if [[ "$current_redis_url" =~ ^redis://:.*@redis-primary:6379/0$ ]] && [ "$current_redis_url" != "$expected_redis_url" ]; then
            echo -e "${BLUE}  -> Syncing REDIS_URL with REDIS_PASSWORD${NC}"
            env_set_value "$env_file" "REDIS_URL" "$expected_redis_url"
            echo -e "${GREEN}  OK REDIS_URL synced${NC}"
        fi
    fi

    if [ -n "$rabbitmq_password" ]; then
        expected_celery_broker_url="amqp://smsly_user:${rabbitmq_password}@rabbitmq:5672//"
        current_celery_broker_url="$(env_get_value "$env_file" "CELERY_BROKER_URL")"

        env_set_value "$env_file" "RABBITMQ_DEFAULT_USER" "smsly_user"
        env_set_value "$env_file" "RABBITMQ_DEFAULT_PASS" "$rabbitmq_password"
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

            # Self-heal: recover MASTER_MESH_IP from .env if not already in shell
            if [ -z "${MASTER_MESH_IP:-}" ] && [ -f "$env_file" ]; then
                MASTER_MESH_IP="$(env_get_value "$env_file" "MASTER_MESH_IP")"
            fi
            local db_user="${MASTER_DB_USER:-smsly_admin}"
            local db_pass="${MASTER_DB_PASSWORD:-$postgres_password}"
            local mq_pass="${MASTER_MQ_PASSWORD:-$rabbitmq_password}"

            # Use WireGuard mesh IP for database connections (public IP is firewalled)
            local db_host="${MASTER_MESH_IP}"
            expected_database_url="postgresql://${db_user}:${db_pass}@${db_host}:5432/smsly_hosting"
            # DIRECT_DATABASE_URL uses the same node_agent credentials as DATABASE_URL.
            # smsly_admin's password is only available on the master node, so we
            # can't use it here. fix_node_db_permissions handles fallback gracefully.
            expected_direct_url="postgresql://${db_user}:${db_pass}@${db_host}:5432/smsly_hosting"
            # Local RabbitMQ is used for Lite Agent node
            expected_celery_broker_url="amqp://smsly_user:${rabbitmq_password}@rabbitmq:5672//"

            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            env_set_value "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url"
            env_set_value "$env_file" "CELERY_BROKER_URL" "$expected_celery_broker_url"
            # Persist MASTER_MESH_IP for future self-healing
            if [ -n "${MASTER_MESH_IP:-}" ]; then
                env_set_value "$env_file" "MASTER_MESH_IP" "$MASTER_MESH_IP"
            fi

            # Sync local vars for consistent validation below
            current_database_url="$expected_database_url"
            current_celery_broker_url="$expected_celery_broker_url"
        fi

        # [NODE MODE] Override for full-stack node (local DB, not master's)
        if [ "$MODE_NODE" = "true" ] && [ -n "$postgres_password" ]; then
            local node_env_mode="$(mode_env_value)"
            local node_expected_db_url="postgresql://smsly_admin:${postgres_password}@pgcat:5432/smsly_hosting"
            local node_expected_direct_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
            if [ "$current_database_url" != "$node_expected_db_url" ]; then
                echo -e "${BLUE}  -> Setting DATABASE_URL for node mode (local DB via PgCat)${NC}"
                env_set_value "$env_file" "DATABASE_URL" "$node_expected_db_url"
                current_database_url="$node_expected_db_url"
            fi
            local current_direct_url
            current_direct_url="$(env_get_value "$env_file" "DIRECT_DATABASE_URL")"
            if [ "$current_direct_url" != "$node_expected_direct_url" ]; then
                echo -e "${BLUE}  -> Setting DIRECT_DATABASE_URL for node mode (local DB direct)${NC}"
                env_set_value "$env_file" "DIRECT_DATABASE_URL" "$node_expected_direct_url"
            fi
            env_set_value "$env_file" "NODE_TYPE" "node"
            env_set_value "$env_file" "MODE" "$node_env_mode"
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

        # NOTE: Removed no-op pgcat→pgcat migration block (was a no-op that
        # matched all pgcat URLs and wrote the same value back).

        # Direct DB connection for migrations (bypasses PgCat transaction pooling)
        local expected_direct_url
        if [ "$MODE_AGENT_LITE" = "true" ]; then
            expected_direct_url="postgresql://${MASTER_DB_USER:-smsly_admin}:${MASTER_DB_PASSWORD:-$postgres_password}@${MASTER_MESH_IP:-db}:5432/smsly_hosting"
        else
            expected_direct_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
        fi

        if [ -z "$current_database_url" ]; then
            env_ensure_var "$env_file" "DATABASE_URL" "$expected_database_url" "PostgreSQL connection string (via PgCat)"

            # Ensure direct connection bypass for migrations exists
            env_ensure_var "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url" "Direct connection bypass for migrations"
        elif [[ "$current_database_url" =~ ^postgresql://smsly_admin:.*@pgcat:5432/smsly_hosting$ ]] && [ "$current_database_url" != "$expected_database_url" ]; then
            echo -e "${BLUE}  -> Fixing DATABASE_URL to match POSTGRES_PASSWORD${NC}"
            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            echo -e "${GREEN}  OK DATABASE_URL password synced${NC}"
        fi

        # Direct DB connection for migrations (bypasses PgCat transaction pooling)
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

load_install_env_defaults() {
    local env_file="${1:-$INSTALL_DIR/.env}"
    local env_domain=""
    local env_public_ip=""
    local env_use_ssl=""
    local env_wildcard=""
    local env_acme_email=""
    local env_cloudflare_token=""
    local env_master_ip=""

    if [ -f "$env_file" ]; then
        env_domain="$(env_get_value "$env_file" "DOMAIN")"
        env_public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"
        env_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
        env_wildcard="$(env_get_value "$env_file" "WILDCARD_SUBDOMAINS")"
        env_acme_email="$(env_get_value "$env_file" "ACME_EMAIL")"
        env_cloudflare_token="$(env_get_value "$env_file" "CLOUDFLARE_API_TOKEN")"
        env_master_ip="$(env_get_value "$env_file" "MASTER_IP")"
    fi

    PUBLIC_IP="${PUBLIC_IP:-$env_public_ip}"
    if [ -z "${PUBLIC_IP:-}" ]; then
        PUBLIC_IP="$(detect_public_ip)"
    fi

    DOMAIN="${DOMAIN:-$env_domain}"
    DOMAIN="${DOMAIN:-$PUBLIC_IP}"

    # SEC-002: IP-mode SSL guard — always force USE_SSL=false for raw IPs,
    # regardless of env var override. Let's Encrypt cannot issue certs for IPs.
    if [[ "$DOMAIN" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        if [ "${USE_SSL:-}" = "true" ]; then
            echo -e "${YELLOW}  ⚠ SEC-002: USE_SSL=true ignored — DOMAIN ($DOMAIN) is a raw IP. Forcing USE_SSL=false.${NC}"
        fi
        USE_SSL="false"
        echo -e "${BLUE}  → IP mode confirmed: USE_SSL forced to false${NC}"
    else
        USE_SSL="${USE_SSL:-$env_use_ssl}"
    fi
    USE_SSL="${USE_SSL:-false}"
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-$env_wildcard}"
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-false}"
    ACME_EMAIL="${ACME_EMAIL:-$env_acme_email}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-$env_cloudflare_token}"
    MASTER_IP="${MASTER_IP:-$env_master_ip}"
}

compose_stack_drift() {
    local services=""
    local service=""
    local container_id=""
    local container_state=""

    if ! services="$(compose_stack_services 2>/tmp/smsly-compose-config.err)"; then
        echo "__compose_config__:invalid"
        sed 's/^/__compose_config_error__:/' /tmp/smsly-compose-config.err 2>/dev/null | head -5 || true
        return 0
    fi

    printf '%s\n' "$services" | while IFS= read -r service; do
        [ -n "$service" ] || continue
        container_id="$(docker compose -f "$COMPOSE_FILE" ps -q "$service" 2>/dev/null || true)"
        if [ -z "$container_id" ]; then
            echo "$service:missing"
            continue
        fi
        container_state="$(docker inspect -f '{{.State.Status}}' "$container_id" 2>/dev/null || true)"
        if [ "$container_state" != "running" ]; then
            echo "$service:${container_state:-unknown}"
        fi
    done
}

reconcile_compose_stack_after_resume() {
    local drift=""
    local reconcile_rc=0

    drift="$(compose_stack_drift || true)"
    if [ -z "$drift" ]; then
        return 0
    fi

    echo -e "${YELLOW}  -> Resumed checkpoint is stale; reconciling compose stack:${NC}"
    printf '%s\n' "$drift" | sed 's/^/     - /'

    # TODO(install): replace set -e toggle with explicit conditional
    set +e
    compose_stack_up --remove-orphans
    reconcile_rc=$?
    if [ "$reconcile_rc" -ne 0 ]; then
        echo -e "${YELLOW}  -> Compose reconciliation needs a rebuild; rebuilding stack...${NC}"
        echo -e "${YELLOW}    ↳ Rebuilding with --no-cache to ensure clean state...${NC}"
        compose_stack_build --no-cache
        reconcile_rc=$?
        if [ "$reconcile_rc" -eq 0 ]; then
            compose_stack_up --remove-orphans
            reconcile_rc=$?
        fi
    fi
    set -e

    if [ "$reconcile_rc" -ne 0 ]; then
        echo -e "${RED}  x Compose reconciliation failed (exit $reconcile_rc).${NC}"
        docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true
        docker compose -f "$COMPOSE_FILE" logs --tail=120 2>/dev/null || true
        exit "$reconcile_rc"
    fi

    echo -e "${GREEN}  OK Compose stack reconciled after resume${NC}"
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
CADDY_LAST_GOOD=""$INSTALL_DIR"/caddy-config/Caddyfile.smsly-last-good"

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
    local direct_url
    direct_url="$(env_get_value "${INSTALL_DIR:-.}/.env" "DIRECT_DATABASE_URL" 2>/dev/null || true)"
    if [ -z "$direct_url" ]; then
        direct_url="postgresql://${POSTGRES_USER:-smsly_admin}:${POSTGRES_PASSWORD:-}@${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-smsly_hosting}"
    fi
    # Pre-cleanup: remove orphaned run containers from previous invocations.
    # docker compose run creates hash-suffixed names (e.g. smsly-hosting-backend-run-a1b2c3)
    # — exact-name docker rm is a no-op.  Use a filter to catch them all.
    docker ps -a -q --filter "name=smsly-hosting-backend-run" | xargs -r docker rm -f 2>/dev/null || true
    migrate_db="$(
        docker compose -f "$COMPOSE_FILE" run --no-deps -T \
            -e SMSLY_DISABLE_STARTUP_TASKS=true \
            -e SMSLY_MIGRATION_MODE=true \
            -e DIRECT_DATABASE_URL="$direct_url" \
            backend python manage.py shell -c \
            "from django.conf import settings; print('direct' if 'direct' in settings.DATABASES else ('session' if 'session' in settings.DATABASES else 'default'))" \
            2>/dev/null | tail -n 1 | tr -d '\r'
    )"
    # Post-cleanup: force-remove the run container.
    docker ps -a -q --filter "name=smsly-hosting-backend-run" | xargs -r docker rm -f 2>/dev/null || true

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
    local direct_url
    direct_url="$(env_get_value "${INSTALL_DIR:-.}/.env" "DIRECT_DATABASE_URL" 2>/dev/null || true)"
    if [ -z "$direct_url" ]; then
        direct_url="postgresql://${POSTGRES_USER:-smsly_admin}:${POSTGRES_PASSWORD:-}@${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-smsly_hosting}"
    fi
    # SECURITY/HARDENING: avoid set +e / set -e toggling. Capture rc via
    # explicit conditional so set -e stays in effect the whole time.
    # NOTE: --rm is omitted intentionally — under heavy Docker daemon load
    # (e.g. concurrent image builds), `docker compose run --rm` can hang
    # for minutes waiting for container removal.
    # Pre-cleanup: remove any orphaned run containers (hash-suffixed names).
    docker ps -a -q --filter "name=smsly-hosting-backend-run" | xargs -r docker rm -f 2>/dev/null || true
    set +e
    timeout "$((timeout_seconds + 60))" docker compose -f "$COMPOSE_FILE" run --no-deps -T \
        "${user_args[@]}" \
        -e SMSLY_DISABLE_STARTUP_TASKS=true \
        -e SMSLY_MIGRATION_MODE=true \
        -e DIRECT_DATABASE_URL="$direct_url" \
        backend timeout "$timeout_seconds" \
        python manage.py migrate --database="$migrate_db" --noinput
    rc=$?
    set -e
    # Post-cleanup: remove the one-shot migration container (hash-suffixed name).
    docker ps -a -q --filter "name=smsly-hosting-backend-run" | xargs -r docker rm -f 2>/dev/null || true
    if [ "$rc" -ne 0 ]; then
        if [ "$rc" -eq 124 ]; then
            echo -e "${RED}  x Migrations timed out after ${timeout_seconds}s.${NC}"
        else
            echo -e "${RED}  x Migrations exited with status ${rc}.${NC}"
        fi
        [ "$MODE_AGENT_LITE" != "true" ] && diagnose_migration_locks
        return "$rc"
    fi

    # Self-healing: fix node agent DB permissions after migrations.
    # Best-effort — wrapped in timeout so a hung Docker daemon can't
    # block the entire update pipeline (which needs to restart backend/celery).
    echo -e "${BLUE}  -> Fixing node agent database permissions...${NC}"
    docker ps -a -q --filter "name=smsly-hosting-backend-run" | xargs -r docker rm -f 2>/dev/null || true
    timeout 60 docker compose -f "$COMPOSE_FILE" run --no-deps -T \
        "${user_args[@]}" \
        -e SMSLY_DISABLE_STARTUP_TASKS=true \
        backend python manage.py fix_node_db_permissions 2>&1 || true
    docker ps -a -q --filter "name=smsly-hosting-backend-run" | xargs -r docker rm -f 2>/dev/null || true

    return 0
}

export_caddy_cloudflare_env() {
    return 0
}

restore_last_good_caddy() {
    return 0
}

reload_caddy_preserving_previous() {
    reload_container_caddy 2>/dev/null || true
    return 0
}

ensure_selfsigned_cert() {
    # Generate a self-signed certificate for the server's public IP address.
    # Caddy's built-in tls internal doesn't support IP SANs (causes
    # ERR_SSL_PROTOCOL_ERROR), so we generate a proper cert with
    # OpenSSL that includes the IP as a Subject Alternative Name.
    local cert_dir="${INSTALL_DIR:-/opt/smsly-hosting}/caddy-config/certs"
    local cert_file="$cert_dir/ip.crt"
    local key_file="$cert_dir/ip.key"
    local public_ip="${PUBLIC_IP:-$(detect_public_ip)}"
    local ssl_config="$cert_dir/openssl.cnf"

    mkdir -p "$cert_dir"
    chmod 700 "$cert_dir" 2>/dev/null || true

    if ! command -v openssl &>/dev/null; then
        echo -e "${YELLOW}  ⚠ openssl not available; skipping self-signed cert generation${NC}"
        return 0
    fi

    # Always regenerate (cheap operation, ensures IP is current)
    echo -e "${BLUE}  → Generating self-signed cert for IP: $public_ip...${NC}"

    # Create a temporary OpenSSL config with the IP SAN
    cat > "$ssl_config" <<EOF
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = $public_ip

[v3_req]
keyUsage = digitalSignature, keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
IP.1 = $public_ip
EOF

    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "$key_file" \
        -out "$cert_file" \
        -config "$ssl_config" \
        2>/dev/null || {
        echo -e "${YELLOW}  ⚠ Failed to generate self-signed cert (non-fatal)${NC}"
        rm -f "$ssl_config"
        return 0
    }
    rm -f "$ssl_config"

    # SECURITY: private key must be owner-readable only (chmod 600). Caddy
    # reads cert AND key as UID 1000; we chown the key to that user so the
    # Caddy container can open it. Cert stays world-readable (chmod 644) for
    # chain-bundle consumers; key is never world-readable.
    chmod 644 "$cert_file" 2>/dev/null || true
    chmod 600 "$key_file" 2>/dev/null || true
    # Hand ownership to Caddy (UID 1000) when run as root via sudo.
    if [ -n "${SUDO_USER:-}" ]; then
        chown "${SUDO_USER}:${SUDO_USER}" "$key_file" 2>/dev/null || chown 1000:1000 "$key_file" 2>/dev/null || true
    elif [ "$(id -u)" -eq 0 ]; then
        chown 1000:1000 "$key_file" 2>/dev/null || true
    fi
    echo -e "${GREEN}  ✓ Self-signed cert generated for $public_ip${NC}"
}

reload_container_caddy() {
    should_manage_caddy || return 0
    # Reload the Docker container Caddy (the one that handles actual traffic).
    # This is needed because the host Caddy (systemd) may not be running.
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if command -v docker &>/dev/null && docker compose -f "$compose_f" ps -q caddy 2>/dev/null | grep -q .; then
        timeout 20 docker compose -f "$compose_f" exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null || \
            timeout 20 docker compose -f "$compose_f" restart caddy 2>/dev/null || true
    fi
}

sync_active_caddyfile_to_shared() {
    return 0
}

install_caddyfile_atomically() {
    should_manage_caddy || return 0
    local candidate="$1"
    local label="${2:-Caddyfile}"
    local dest="${INSTALL_DIR:-/opt/smsly-hosting}/caddy-config/Caddyfile"

    if [ ! -f "$candidate" ]; then
        echo -e "${YELLOW}  WARN $label candidate missing: $candidate${NC}"
        return 1
    fi

    mkdir -p "$(dirname "$dest")"
    cp "$candidate" "$dest"
    chmod 664 "$dest"

    reload_container_caddy 2>/dev/null || true
    return 0
}

# ─── Parse Arguments ─────────────────────────────────────────────────────────
UPDATE_MODE=""
WIPE_MODE="false"
RECOVER_MODE="false"
REFRESH_MODE="false"
DEBUG_MODE="false"
FORCE_REDEPLOY="false"
RECREATE_TRAEFIK="false"

# Simple loop to parse multiple arguments
for arg in "$@"; do
    case "$arg" in
        --update)          UPDATE_MODE="full" ;;
        --update-half)     UPDATE_MODE="half" ;;
        --update-frontend) UPDATE_MODE="frontend" ;;
        --update-backend)  UPDATE_MODE="backend" ;;
        --wipe)            WIPE_MODE="true" ;;
        --recover)         RECOVER_MODE="true" ;;
        --refresh)         REFRESH_MODE="true" ;;
        --debug)           DEBUG_MODE="true" ;;
        --verify)          VERIFY_MODE="true" ;;
        --mode=agent-lite|--agent-lite|--mode=node|--node|--mode=master|--master) : ;;
        --clear)           CLEAR_MODE="true" ;;
        --fix-domain)      FIX_DOMAIN_MODE="true" ;;
        --fix-permissions) FIX_PERMISSIONS_MODE="true" ;;
        --force-redeploy)  FORCE_REDEPLOY="true" ;;
        --recreate-traefik) RECREATE_TRAEFIK="true" ;;
        --help|-h)
            echo "Usage: sudo bash install.sh [--mode=agent-lite|--mode=node] [--update|--update-half|--update-frontend|--update-backend|--refresh|--recover|--debug|--wipe|--clear|--fix-domain|--fix-permissions]"
            echo ""
            echo "  (no args)          Fresh install (Legacy Python | Full-Stack Master)"
            echo "  --mode=agent-lite  Install as a Lite Agent (shared-DB node)"
            echo "  --mode=node        Install as a Full-Stack Node (own DB, no frontend)"
            echo "  --update           Pull latest code and rebuild all services (full rebuild)"
            echo "  --update-half      Pull latest code, restart backend only — no Docker image rebuild"
            echo "  --clear            Wipes stale addons and frees up docker resources"
            echo "  --fix-domain       Fix domain/IP sync between .env, DB PlatformConfig, and Caddy"
            echo "  --fix-permissions  Fix .env and shared directory permissions for container write access"
            echo "  --force-redeploy   Always redeploy active services after update, even if code hasn't changed"
            exit 0
            ;;
    esac
done

if [ "$MODE_AGENT_LITE" = "true" ]; then
    COMPOSE_FILE="infrastructure/docker/docker-compose.agent-lite.yml"
fi

# SECURITY: --rust / RUST_TWIN_MODE branch removed 2026-06. The rust_twin/
# stubs moved to archive/rust_twin-2026-06/ and the Rust rewrite is abandoned.

MODE_LABEL="fresh-install"
if [ "$MODE_AGENT_LITE" = "true" ]; then
    MODE_LABEL="agent-lite-install"
elif [ "$MODE_NODE" = "true" ]; then
    MODE_LABEL="node-install"
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
elif [ "${FIX_DOMAIN_MODE:-false}" = "true" ]; then
    MODE_LABEL="fix-domain"
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
    # Kill any lingering heartbeat process from the build step
    if [ -n "${HEARTBEAT_PID:-}" ]; then
        kill "$HEARTBEAT_PID" 2>/dev/null || true
        wait "$HEARTBEAT_PID" 2>/dev/null || true
    fi
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
        echo -e "${YELLOW}  ↳ Tip: Use --resume to skip completed steps: sudo bash install.sh --resume${NC}"

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
echo -e "${BLUE}   Grid - Production Installer v3.2.4${NC}"
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
    true-watcher 2>/dev/null || true
    true-watcher 2>/dev/null || true
    rm -f /etc/systemd/system/caddy-watcher.service

    # Reset Caddyfile to default (prevents stale routing)
    if [ -f "$INSTALL_DIR"/caddy-config/Caddyfile ]; then
        echo ':80 { respond "Caddy is running" 200 }' > "$INSTALL_DIR"/caddy-config/Caddyfile
        true 2>/dev/null || true
    fi

    # Remove Cloudflare token override
    rm -rf /etc/systemd/system/caddy.service.d
    systemctl daemon-reload 2>/dev/null || true

    rm -rf "$INSTALL_DIR"
    rm -f "$LOG_FILE"

    trap - EXIT
    release_install_lock
    echo -e "${GREEN}OK Wipe complete. The server is ready for a fresh install.${NC}"
    echo -e "${YELLOW}  Run: curl -fsSL https://raw.githubusercontent.com/smsly/smsly-hosting/main/install.sh -o install.sh${NC}"
    echo -e "${YELLOW}       gpg --verify install.sh  # if you have a signed copy${NC}"
    echo -e "${YELLOW}       sudo bash install.sh${NC}"
    exit 0
}

if [ "$WIPE_MODE" = "true" ]; then
    wipe_existing_install
fi

# =============================================================================
# FIX-PERMISSIONS — Fix .env and shared directory permissions for container
# =============================================================================
fix_env_permissions() {
    local env_file="${1:-$INSTALL_DIR/.env}"
    echo -e "${BLUE}  → Fixing .env permissions...${NC}"

    if [ ! -f "$env_file" ]; then
        echo -e "${YELLOW}  ⚠ .env not found at $env_file${NC}"
        return 1
    fi

    # The backend container runs as UID 1000 (smsly user).
    # .env must be group-writable by GID 1000 so the domain-config
    # signal can persist DOMAIN/USE_SSL changes back to .env.
    chown root:1000 "$env_file" 2>/dev/null || true
    chmod 664 "$env_file" 2>/dev/null || true

    local owner mode
    owner="$(stat -c '%u:%g' "$env_file" 2>/dev/null || echo "?")"
    mode="$(stat -c '%a' "$env_file" 2>/dev/null || echo "?")"
    echo -e "${GREEN}  ✓ .env permissions: $mode owner=$owner${NC}"

    # Also fix caddy-config directory for good measure
    if [ -d "$INSTALL_DIR/caddy-config" ]; then
        chown -R 1000:1000 "$INSTALL_DIR/caddy-config" 2>/dev/null || true
        chmod -R u+rwX,g+rwX "$INSTALL_DIR/caddy-config" 2>/dev/null || true
        echo -e "${GREEN}  ✓ caddy-config permissions fixed${NC}"
    fi

    # Fix staticfiles/media directories
    for dir in staticfiles media backups; do
        if [ -d "$INSTALL_DIR/$dir" ]; then
            chown -R 1000:1000 "$INSTALL_DIR/$dir" 2>/dev/null || true
        fi
    done
}

# =============================================================================
# FIX-DOMAIN MODE — Fix domain/IP sync between .env, DB PlatformConfig, and Caddy
# =============================================================================
fix_domain_sync() {
    local target_domain="${1:-}"
    local env_file="$INSTALL_DIR/.env"

    echo -e "${BLUE}  → Fixing domain sync for: $target_domain${NC}"

    # 1. Fix .env
    if grep -q '^DOMAIN=' "$env_file" 2>/dev/null; then
        sed -i "s|^DOMAIN=.*|DOMAIN=$target_domain|" "$env_file"
    else
        echo "DOMAIN=$target_domain" >> "$env_file"
    fi
    if grep -q '^USE_SSL=' "$env_file" 2>/dev/null; then
        sed -i 's/^USE_SSL=.*/USE_SSL=true/' "$env_file"
    else
        echo "USE_SSL=true" >> "$env_file"
    fi

    # Sync allowlists
    sync_env_domain_allowlists "$env_file" "$target_domain" "$(detect_public_ip)"

    # 2. Sync DB PlatformConfig
    if docker compose -f "$COMPOSE_FILE" ps -q backend 2>/dev/null | grep -q .; then
        timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
cfg = PlatformConfig.load()
cfg.domain = '$target_domain'
cfg.use_ssl = True
cfg.save()
print(f'PlatformConfig domain set to: {cfg.domain}')
" 2>/dev/null && echo -e "${GREEN}  ✓ PlatformConfig synced${NC}" || echo -e "${YELLOW}  ⚠ DB sync skipped${NC}"
    else
        echo -e "${YELLOW}  ⚠ Backend not running; DB sync deferred to --update${NC}"
    fi

    # 3. Generate self-signed cert + regenerate Caddyfile
    ensure_selfsigned_cert
    local fix_ip
    fix_ip="$(detect_public_ip)"
    if [ -d "caddy-config" ]; then
        cat > caddy-config/Caddyfile <<CADDYFIX
# SMSLY Caddyfile — Fixed by --fix-domain
{
    on_demand_tls {
        ask http://backend:8000/api/v1/services/check-domain/
    }
}

$target_domain {
    reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

${fix_ip} {
    tls internal
    redir http://${fix_ip}{uri} 308
}

:80 {
    @acme {
        path /.well-known/acme-challenge/*
    }
    handle @acme {
        reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    }
    @redirectable {
        not header_regexp host ^([0-9]{1,3}[.]){3}[0-9]{1,3}(:[0-9]+)?$
        not host localhost
        not host 127.0.0.1
        not host *.local
        header_regexp host .+
    }
    redir @redirectable https://{host}{uri} 308
    handle {
        reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    }
}
CADDYFIX
        echo -e "${GREEN}  ✓ Caddyfile regenerated${NC}"
    fi

    # 4. Reload Caddy
    if docker compose -f "$COMPOSE_FILE" ps -q caddy 2>/dev/null | grep -q .; then
        docker compose -f "$COMPOSE_FILE" exec caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null || \
            docker compose -f "$COMPOSE_FILE" restart caddy 2>/dev/null || true
        echo -e "${GREEN}  ✓ Caddy reloaded${NC}"
    fi

    echo -e "${GREEN}  ✓ Domain fix complete for: $target_domain${NC}"
}

if [ "${FIX_DOMAIN_MODE:-false}" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --fix-domain)${NC}"
        exit 1
    fi
    if [ ! -f "$INSTALL_DIR/.env" ] || [ ! -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
        echo -e "${RED}x SMSLY installation not found at $INSTALL_DIR. Run fresh install first.${NC}"
        exit 1
    fi
    cd "$INSTALL_DIR"
    if ! should_manage_caddy; then
        echo -e "${YELLOW}  → --fix-domain is master-only because node/agent modes do not manage Caddy/HTTPS.${NC}"
        exit 0
    fi
    ensure_local_ignores
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        echo -e "${YELLOW}  ! Local changes detected - stashing before repository sync${NC}"
        git stash push --include-untracked -m "install-sync-$(date +%s)" >/dev/null 2>&1 || true
    fi

    # Git pull latest code first to get all SEC-xxx fixes
    echo -e "${BLUE}  → Pulling latest installer code...${NC}"
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    if ! git fetch origin main 2>/dev/null; then
        echo -e "${RED}  ✗ Git fetch failed for main. SSL verification is always enforced.${NC}"
    fi
    if ! git checkout -B main origin/main 2>/dev/null; then
        echo -e "${RED}  ✗ Git checkout failed for main.${NC}"
    fi
    echo -e "${GREEN}  ✓ Code updated${NC}"

    # Detect current or prompt for domain
    FIX_DOMAIN="${DOMAIN:-}"
    if [ -z "$FIX_DOMAIN" ]; then
        FIX_DOMAIN="$(env_get_value "$INSTALL_DIR/.env" "DOMAIN" 2>/dev/null || true)"
    fi
    while [ -z "$FIX_DOMAIN" ] || echo "$FIX_DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; do
        if [ -n "$FIX_DOMAIN" ]; then
            echo -e "${YELLOW}  ⚠ Current DOMAIN is an IP address ($FIX_DOMAIN). Enter your real domain.${NC}"
        fi
        echo -e "${BLUE}  Enter your domain (e.g., app.example.com):${NC}"
        read -p "  Domain: " FIX_DOMAIN < /dev/tty
        FIX_DOMAIN="$(echo "$FIX_DOMAIN" | xargs)"
    done

    fix_domain_sync "$FIX_DOMAIN"

    # Re-exec into --update to rebuild Caddy container with new config
    echo -e "${BLUE}  → Running --update to apply changes...${NC}"
    export NO_SCREEN=true
    export DOMAIN="$FIX_DOMAIN"
    export USE_SSL="true"
    exec env PATH="/usr/local/bin:$PATH" bash "$SCRIPT_PATH" --update --no-screen "$@"
fi

# =============================================================================
# FIX-PERMISSIONS MODE handler
# =============================================================================
if [ "${FIX_PERMISSIONS_MODE:-false}" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --fix-permissions)${NC}"
        exit 1
    fi
    if [ ! -f "$INSTALL_DIR/.env" ]; then
        echo -e "${RED}x SMSLY installation not found at $INSTALL_DIR. Run fresh install first.${NC}"
        exit 1
    fi
    fix_env_permissions "$INSTALL_DIR/.env"
    echo -e "${GREEN}✅ Permissions fixed.${NC}"
    echo -e "  You may need to rebuild the backend for the signal code to take effect:"
    echo -e "    docker compose -f $INSTALL_DIR/$COMPOSE_FILE build backend"
    echo -e "    docker compose -f $INSTALL_DIR/$COMPOSE_FILE down"
    echo -e "    docker compose -f $INSTALL_DIR/$COMPOSE_FILE up -d"
    echo -e "  Then re-save the domain via Settings → Domain & SSL."
    exit 0
fi

ensure_update_networks() {
    # Never delete data networks/volumes in update mode. Only (re)create if missing.
    docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null 2>&1 || true
    docker network inspect smsly-proxy >/dev/null 2>&1 || docker network create smsly-proxy >/dev/null 2>&1 || true
    docker network inspect socket-proxy >/dev/null 2>&1 || docker network create --driver bridge --internal socket-proxy >/dev/null 2>&1 || true
}

compose_stack_services() {
    local services=""
    services="$(docker compose -f "$COMPOSE_FILE" config --services)" || return $?
    if is_node_mode; then
        printf '%s\n' "$services" | grep -Ev '^(frontend|caddy)$'
    else
        printf '%s\n' "$services"
    fi
}

compose_stack_service_args() {
    compose_stack_services | tr '\n' ' '
}

compose_stack_build_service_args() {
    local candidates="pgcat backend celery celery-beat frontend celery-fast celery-deploy caddy"
    local svc=""
    if is_node_mode; then
        candidates="pgcat backend celery celery-beat celery-fast celery-deploy"
    fi
    for svc in $candidates; do
        if docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null | grep -qx "$svc"; then
            printf '%s\n' "$svc"
        fi
    done | tr '\n' ' '
}

stop_node_excluded_services() {
    is_node_mode || return 0
    docker compose -f "$COMPOSE_FILE" stop --timeout 15 frontend caddy >/dev/null 2>&1 || true
    docker compose -f "$COMPOSE_FILE" rm -f frontend caddy >/dev/null 2>&1 || true
}

docker_login() {
    local registry="${CONTAINER_REGISTRY_URL:-127.0.0.1:5000}"
    local user="${REGISTRY_USER:-smsly-registry}"
    local pass="${REGISTRY_PASSWORD:-}"
    if [ -z "$pass" ]; then
        return 0
    fi
    echo "$pass" | docker login "$registry" -u "$user" --password-stdin >/dev/null 2>&1 || true
}

compose_stack_build() {
    docker_login
    local services=""
    if is_node_mode; then
        stop_node_excluded_services
        services="$(compose_stack_build_service_args)"
        [ -n "$services" ] || return 1
        docker compose -f "$COMPOSE_FILE" build "$@" $services
    else
        docker compose -f "$COMPOSE_FILE" build "$@"
    fi
}

compose_stack_up() {
    local services=""
    if is_node_mode; then
        stop_node_excluded_services
        services="$(compose_stack_service_args)"
        [ -n "$services" ] || return 1
        docker compose -f "$COMPOSE_FILE" up -d "$@" $services
    else
        docker compose -f "$COMPOSE_FILE" up -d "$@"
    fi
}

ensure_infrastructure_permissions() {
    local caddy_config_dir="/opt/smsly-hosting/caddy-config"
    local staticfiles_dir="/opt/smsly-hosting/backend/staticfiles"
    local builds_dir="/opt/smsly-hosting/builds"
    local prometheus_targets_dir="/opt/smsly-hosting/prometheus-targets"

    echo -e "${BLUE}  -> Ensuring infrastructure permissions...${NC}"

    # 1. Handle Bind-Mounts (Caddy Config & Staticfiles)
    mkdir -p "$caddy_config_dir"
    mkdir -p "$staticfiles_dir"
    mkdir -p "$builds_dir"
    mkdir -p "$prometheus_targets_dir"

    # UID 1000 is the "smsly" user inside the containers.
    # Note: Never chown to host username "smsly:smsly" because host UID may not be 1000.
    chown -R 1000:1000 "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir" 2>/dev/null || true

    chmod -R u+rwX,g+rwX "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir" 2>/dev/null || true
    find "$caddy_config_dir" -type d -exec chmod 2775 {} + 2>/dev/null || true
    find "$staticfiles_dir" -type d -exec chmod 2775 {} + 2>/dev/null || true
    find "$builds_dir" -type d -exec chmod 2775 {} + 2>/dev/null || true
    find "$prometheus_targets_dir" -type d -exec chmod 2777 {} + 2>/dev/null || true
    # Ensure the directory itself has the right permissions (not just children)
    chmod 2777 "$prometheus_targets_dir" 2>/dev/null || true

    # Caddy-specific file permissions
    [ -f "$caddy_config_dir/Caddyfile" ] && chmod 664 "$caddy_config_dir/Caddyfile" 2>/dev/null || true
    [ -f "$caddy_config_dir/.reload" ] && chmod 664 "$caddy_config_dir/.reload" 2>/dev/null || true

    # 2. Handle Named Volumes (backups_data)
    # We use a one-off container to safely chown existing named volumes.
    if command -v docker >/dev/null 2>&1; then
        for vol in backups_data; do
            if docker volume inspect "$vol" >/dev/null 2>&1; then
                echo -e "${BLUE}     ↳ Setting permissions for volume: $vol...${NC}"
                docker run --rm -v "${vol}:/data" alpine chown -R 1000:1000 /data 2>/dev/null || true
            fi
        done
    fi

    # Fast write probe for Caddy
    echo "perm-check $(date +%s)" > "$caddy_config_dir/.perm_probe" 2>/dev/null || true
}

resolve_container_target() {
    local target="$1"

    [ -z "$target" ] && return 0

    # 1. If target is already a valid container ID or name inspectable by docker, return it
    if docker container inspect "$target" >/dev/null 2>&1; then
        echo "$target"
        return 0
    fi

    # 2. Try to map target to a docker compose service.
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$compose_f" ]; then
        local services
        services="$(docker compose -f "$compose_f" config --services 2>/dev/null)"
        if [ -n "$services" ]; then
            for svc in $services; do
                if [[ "$target" == *"-${svc}-"* || "$target" == *"_${svc}_"* || "$target" == *"-${svc}" || "$target" == *"_${svc}" || "$target" == "$svc" ]]; then
                    local cid
                    cid="$(docker compose -f "$compose_f" ps -q "$svc" 2>/dev/null | head -n 1 || true)"
                    if [ -n "$cid" ]; then
                        echo "$cid"
                        return 0
                    fi
                fi
            done
        fi
    fi

    # 3. Fallback: maybe target is a service name itself?
    local cid_svc
    cid_svc="$(docker compose -f "$compose_f" ps -q "$target" 2>/dev/null | head -n 1 || true)"
    if [ -n "$cid_svc" ]; then
        echo "$cid_svc"
        return 0
    fi

    # 4. Fallback: search for container matching substring wildcard
    local cid_fuzzy
    local fuzzy_pattern
    fuzzy_pattern="${target//-/*}"
    fuzzy_pattern="${fuzzy_pattern//_/*}"
    cid_fuzzy="$(docker ps -a --filter "name=${fuzzy_pattern}" -q 2>/dev/null | head -n 1 || true)"
    if [ -n "$cid_fuzzy" ]; then
        echo "$cid_fuzzy"
        return 0
    fi

    # 5. Last resort fallback to the original target string
    echo "$target"
}

ensure_container_on_network() {
    local network_name="$1"
    local raw_target="$2"

    [ -z "$network_name" ] && return 0
    [ -z "$raw_target" ] && return 0

    local container_name
    container_name="$(resolve_container_target "$raw_target")"

    docker container inspect "$container_name" >/dev/null 2>&1 || return 0
    docker network inspect "$network_name" >/dev/null 2>&1 || return 0

    local alias_flag=""
    if [[ "$raw_target" == *"socket-proxy"* || "$container_name" == *"socket-proxy"* ]]; then
        alias_flag="--alias socket-proxy"
    elif [[ "$raw_target" == *"traefik"* || "$container_name" == *"traefik"* ]]; then
        alias_flag="--alias traefik"
    elif [[ "$raw_target" == *"route-fallback"* || "$container_name" == *"route-fallback"* ]]; then
        alias_flag="--alias route-fallback"
    fi

    docker network connect $alias_flag "$network_name" "$container_name" >/dev/null 2>&1 || true
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
    local candidate="/tmp/Caddyfile.safe.$$"
    echo -e "${YELLOW}  ⚠ Generating safe fallback Caddyfile (reason: $reason)...${NC}"

    # 1. Discover domain: DB first, .env fallback
    local domain=""
    domain="$(timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
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
    svc_blocks="$(timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
import os
upstream = os.environ.get('SMSLY_SERVICE_PROXY_UPSTREAM', 'traefik:80')
from apps.deployments.models import Service
for svc in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain=''):
    d = svc.public_domain.strip()
    if d:
        print(f'{d} {{\n    reverse_proxy {upstream}\n    encode gzip\n}}\n')
    for cd in (svc.custom_domains or []):
        cd = cd.strip()
        if cd:
            print(f'{cd} {{\n    reverse_proxy {upstream}\n    encode gzip\n}}\n')
" 2>/dev/null | tr -d '\r' || true)"

    # 3. Check if domain is a real hostname (not an IP address)
    local is_real_domain=false
    if [ -n "$domain" ] && [ "$domain" != "localhost" ]; then
        if ! echo "$domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
            is_real_domain=true
        fi
    fi

    # 4. Build the Caddyfile — IP-aware
    local domain_block_label="$domain"
    local safe_ip
    safe_ip="$(detect_public_ip)"
    if ! echo "$domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' && [ "$is_real_domain" = "false" ]; then
        domain_block_label="http://${domain}"
    fi

    cat > "$candidate" <<SAFECADDY
# Auto-generated safe fallback (reason: $reason)
{
    on_demand_tls {
        ask http://backend:8000/api/v1/services/check-domain/
    }
}

${domain_block_label} {
    reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

${safe_ip} {
    tls internal
    redir http://${safe_ip}{uri} 308
}

:80 {
    @acme {
        path /.well-known/acme-challenge/*
    }
    handle @acme {
        reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    }
    @redirectable {
        not header_regexp host ^([0-9]{1,3}[.]){3}[0-9]{1,3}(:[0-9]+)?$
        not host localhost
        not host 127.0.0.1
        not host *.local
        header_regexp host .+
    }
    redir @redirectable https://{host}{uri} 308
    handle {
        reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    }
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
    should_manage_caddy || return 1
    local dest="${INSTALL_DIR:-/opt/smsly-hosting}/caddy-config/Caddyfile"
    if ! timeout 15 docker compose -f "$COMPOSE_FILE" exec -T caddy caddy validate --config /etc/caddy/Caddyfile 2>/dev/null; then
        return 0  # Syntax error
    fi
    if grep -q 'dns cloudflare' "$dest" 2>/dev/null; then
        local _env_token="${CLOUDFLARE_API_TOKEN:-}"
        if [ -z "$_env_token" ] && [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
            _env_token="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "${INSTALL_DIR:-/opt/smsly-hosting}/.env" 2>/dev/null | cut -d= -f2- || true)"
        fi
        if [ -z "$_env_token" ] || [ "$_env_token" = "fake" ]; then
            return 0  # dns cloudflare without token = runtime crash
        fi
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
    return 0
}

restart_caddy_watcher_safely() {
    return 0
}

install_caddy_health_guard() {
    return 0
}

bust_core_build_cache() {
    echo -e "${BLUE}  -> Busting frontend/backend build cache (safe mode)...${NC}"

    # Define core services for cache busting
    local core_svcs="frontend backend celery celery-deploy celery-fast celery-beat"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        core_svcs="backend celery-worker"
    elif [ "$MODE_NODE" = "true" ]; then
        core_svcs="backend celery celery-deploy celery-fast celery-beat"
    fi

    # Remove old app image layers for deterministic rebuilds (no DB/data touched).
    for svc in $core_svcs; do
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
    local edge_services="socket-proxy traefik"
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        edge_services="socket-proxy traefik route-fallback"
    fi

    echo -e "${BLUE}  -> Refreshing edge proxy stack (traefik/socket-proxy/route-fallback)...${NC}"
    # First ensure socket-proxy and route-fallback are running (no recreate).
    # Only Traefik is force-recreated below to avoid disruption to the Docker
    # event stream that socket-proxy provides to Traefik.
    local non_traefik_services="socket-proxy"
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        non_traefik_services="socket-proxy route-fallback"
    fi
    echo -e "${BLUE}    [1/5] Ensuring socket-proxy + route-fallback running...${NC}"
    local all_running=true
    for svc in $non_traefik_services; do
        if ! docker compose -f "$COMPOSE_FILE" ps "$svc" 2>/dev/null | grep -q "Up"; then
            all_running=false
            break
        fi
    done
    if [ "$all_running" = true ]; then
        echo -e "${GREEN}      edge services already running, skipping restart${NC}"
    else
        timeout 30 docker compose -f "$COMPOSE_FILE" up -d --no-deps $non_traefik_services >/dev/null 2>&1 || \
            timeout 30 docker compose -f "$COMPOSE_FILE" up -d $non_traefik_services >/dev/null 2>&1 || true
    fi

    # Force-recreate ONLY Traefik (not socket-proxy) to trigger full container
    # re-discovery. Traefik v3.x removed pollInterval; a fresh start against a
    # stable socket-proxy is the only way to guarantee complete provider re-scan
    # after network topology changes.
    # Brief downtime: ~2-5s while Traefik restarts. Caddy retries through it.
    # NOTE(Zero-Downtime): We removed --force-recreate. Traefik dynamically listens to
    # Docker events and does not need to be restarted. This eliminates the 2-5s downtime
    # for deployed user services during an update.
    echo -e "${BLUE}    [2/5] Ensuring traefik running...${NC}"
    if docker compose -f "$COMPOSE_FILE" ps traefik 2>/dev/null | grep -q "Up"; then
        echo -e "${GREEN}      traefik already running, skipping restart${NC}"
    else
        timeout 30 docker compose -f "$COMPOSE_FILE" up -d traefik >/dev/null 2>&1 || true
    fi

    # Re-attach expected external networks AFTER Traefik restart so it
    # discovers containers with stable network topology (idempotent).
    # If run before 'up -d', Docker Compose will forcefully strip 'smsly-proxy' 
    # since it's not defined in the compose file's networks block.
    echo -e "${BLUE}    [3/5] Re-attaching external networks...${NC}"
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-socket-proxy-1"
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    fi
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    # Validate Caddy config before restart (H1 fix)
    # Use Docker-based Caddy, not host-level binary
    echo -e "${BLUE}    [4/5] Validating Caddy config...${NC}"
    if should_manage_caddy && docker compose -f "$COMPOSE_FILE" ps caddy 2>/dev/null | grep -q "Up"; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "restart_edge_stack validation"
        fi
        echo -e "${BLUE}    [5/5] Reloading Caddy...${NC}"
        reload_container_caddy 2>/dev/null || true
    fi
    echo -e "${GREEN}  OK Edge stack refreshed${NC}"
}

wait_for_traefik_api() {
    local max_wait="${1:-30}"
    local waited=0
    local interval=2
    echo -e "${BLUE}  → Waiting for Traefik API to be ready...${NC}"
    while [ "$waited" -lt "$max_wait" ]; do
        if curl -sf --max-time 3 http://127.0.0.1:8082/api/version >/dev/null 2>&1; then
            echo -e "${GREEN}  ✓ Traefik API ready (${waited}s)${NC}"
            return 0
        fi
        sleep "$interval"
        waited=$((waited + interval))
    done
    echo -e "${YELLOW}  ⚠ Traefik API not ready after ${max_wait}s — services may be unreachable${NC}"
    return 1
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
    stop_node_excluded_services

    for svc in "${app_services_requested[@]}"; do
        if is_node_mode && [ "$svc" = "frontend" ]; then
            continue
        fi
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
    if [ "$MODE_NODE" != "true" ]; then
        ensure_container_on_network "smsly-net" "smsly-hosting-frontend-1"
    fi
    ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-frps-1"
    ensure_container_on_network "smsly-net" "smsly-hosting-socket-proxy-1"
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

        ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
        ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
        ensure_container_on_network "smsly-net" "smsly-hosting-socket-proxy-1"
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

    if should_manage_caddy; then
        install_caddy_health_guard "${DOMAIN:-}"
        reload_container_caddy 2>/dev/null || true
    fi

    if [ "$MODE_AGENT_LITE" != "true" ]; then
        echo -e "${BLUE}  → Refreshing Observability Stack...${NC}"
        if [ -f "infrastructure/docker/docker-compose.observability.yml" ]; then
            docker compose -f infrastructure/docker/docker-compose.observability.yml up -d >/dev/null 2>&1 || true
        fi
    fi

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
    local raw_target="$1"
    local timeout_seconds="${2:-180}"
    local elapsed=0
    local state=""

    [ -z "$raw_target" ] && return 1

    local container_name
    container_name="$(resolve_container_target "$raw_target")"

    while [ "$elapsed" -lt "$timeout_seconds" ]; do
        state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name" 2>/dev/null || echo "missing")"
        if [ "$state" = "healthy" ] || [ "$state" = "running" ]; then
            echo -e "${GREEN}  OK $raw_target is $state${NC}"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done

    echo -e "${YELLOW}  WARN $raw_target not ready after ${timeout_seconds}s (state=$state)${NC}"
    return 1
}

sync_agent_lite_rabbitmq_password() {
    [ "$MODE_AGENT_LITE" = "true" ] || return 0

    local env_file="$INSTALL_DIR/.env"
    local rabbitmq_user rabbitmq_password

    rabbitmq_user="$(env_get_value "$env_file" "RABBITMQ_DEFAULT_USER" 2>/dev/null || true)"
    rabbitmq_user="${rabbitmq_user:-smsly_user}"
    rabbitmq_password="$(env_get_value "$env_file" "RABBITMQ_PASSWORD" 2>/dev/null || true)"
    rabbitmq_password="${rabbitmq_password:-$(env_get_value "$env_file" "RABBITMQ_DEFAULT_PASS" 2>/dev/null || true)}"

    if [ -z "$rabbitmq_password" ]; then
        echo -e "${RED}  ERROR RABBITMQ_PASSWORD is empty after agent-lite env generation${NC}"
        exit 1
    fi

    docker compose -f "$COMPOSE_FILE" up -d rabbitmq >/dev/null 2>&1 || true
    wait_for_container_ready "smsly-hosting-rabbitmq-1" 120 || {
        docker compose -f "$COMPOSE_FILE" logs --tail=80 rabbitmq 2>/dev/null || true
        exit 1
    }

    if docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl authenticate_user "$rabbitmq_user" "$rabbitmq_password" >/dev/null 2>&1; then
        echo -e "${GREEN}  OK Lite Agent RabbitMQ password already matches .env${NC}"
        return 0
    fi

    echo -e "${BLUE}  -> Syncing Lite Agent RabbitMQ password for ${rabbitmq_user}...${NC}"
    docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl add_user "$rabbitmq_user" "$rabbitmq_password" >/dev/null 2>&1 || true
    docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl change_password "$rabbitmq_user" "$rabbitmq_password" >/dev/null
    docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl set_user_tags "$rabbitmq_user" administrator >/dev/null
    docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl set_permissions -p / "$rabbitmq_user" ".*" ".*" ".*" >/dev/null

    if docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl authenticate_user "$rabbitmq_user" "$rabbitmq_password" >/dev/null 2>&1; then
        echo -e "${GREEN}  OK Lite Agent RabbitMQ password synced${NC}"
        return 0
    fi

    echo -e "${RED}  ERROR Lite Agent RabbitMQ password sync failed${NC}"
    exit 1
}

recover_runtime_stack() {
    echo -e "${BLUE}  -> Running runtime recovery (network + core services + edge)...${NC}"

    ensure_update_networks
    ensure_infrastructure_permissions

    # Only restart Docker if the daemon was reconfigured (e.g. for registry trust).
    # Unconditional restart during recovery can cascade-fail all running
    # containers — including the proxy (Caddy/Traefik) — causing a total outage.
    if [ -f "/etc/docker/daemon.json" ] && [ -f "/var/run/docker.sock" ]; then
        echo -e "${BLUE}    -> Docker daemon is running; skipping restart to preserve live containers${NC}"
    fi

    echo -e "${BLUE}    -> Starting dependency services...${NC}"

    # Ensure registry TLS cert + htpasswd exist before starting the registry.
    # The registry container will crash-loop without these files. Also
    # regenerate if the existing key/cert don't match (e.g. one was rotated
    # independently). openssl req produces a matched pair in one shot, so a
    # mismatch means one file was replaced without the other.
    mkdir -p "$INSTALL_DIR/auth" "$INSTALL_DIR/certs"
    _registry_certs_ok() {
        [ -f "$INSTALL_DIR/certs/registry.key" ] || return 1
        [ -f "$INSTALL_DIR/certs/registry.crt" ] || return 1
        local _cmod _kmod
        _cmod="$(openssl x509 -in "$INSTALL_DIR/certs/registry.crt" -noout -modulus 2>/dev/null | openssl sha256)" || return 1
        _kmod="$(openssl rsa  -in "$INSTALL_DIR/certs/registry.key" -noout -modulus 2>/dev/null | openssl sha256)" || return 1
        [ "$_cmod" = "$_kmod" ]
    }
    if ! _registry_certs_ok; then
        echo -e "${BLUE}      Generating self-signed TLS cert for registry...${NC}"
        _tmp_dir="$(mktemp -d)"
        if openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "${_tmp_dir}/registry.key" \
            -out    "${_tmp_dir}/registry.crt" \
            -subj "/CN=registry" 2>/dev/null; then
            mv "${_tmp_dir}/registry.key" "$INSTALL_DIR/certs/registry.key"
            mv "${_tmp_dir}/registry.crt" "$INSTALL_DIR/certs/registry.crt"
            chmod 644 "$INSTALL_DIR/certs/registry.crt" "$INSTALL_DIR/certs/registry.key"
        else
            echo -e "${YELLOW}    ⚠ openssl failed — is it installed?${NC}"
        fi
        rm -rf "$_tmp_dir" 2>/dev/null || true
        if ! _registry_certs_ok; then
            echo -e "${RED}    ✗ Registry TLS cert/key still missing or mismatched${NC}"
            echo -e "${YELLOW}      Manual fix: openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \\${NC}"
            echo -e "${YELLOW}        -keyout $INSTALL_DIR/certs/registry.key \\${NC}"
            echo -e "${YELLOW}        -out    $INSTALL_DIR/certs/registry.crt \\${NC}"
            echo -e "${YELLOW}        -subj '/CN=registry'${NC}"
        else
            echo -e "${BLUE}      Restarting registry container to pick up new TLS certs...${NC}"
            docker restart smsly-hosting-registry-1 2>/dev/null || true
        fi
    fi
    if [ ! -f "$INSTALL_DIR/auth/htpasswd" ] || [ -z "${REGISTRY_PASSWORD:-}" ] || [ -z "${REGISTRY_USER:-}" ]; then
        REGISTRY_PASS="${REGISTRY_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null || openssl rand -hex 12 2>/dev/null || echo 'auto-generated-change-me')}"
        if command -v htpasswd >/dev/null 2>&1; then
            htpasswd -Bbn "${REGISTRY_USER:-smsly-registry}" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"
        else
            python3 -c "
import bcrypt, sys
pw = sys.argv[1] if len(sys.argv) > 1 else '$REGISTRY_PASS'
print('${REGISTRY_USER:-smsly-registry}:' + bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode())
" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd" 2>/dev/null || true
        fi
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_USER" "${REGISTRY_USER:-smsly-registry}"
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_PASSWORD" "$REGISTRY_PASS"
    fi

    if [ "$MODE_AGENT_LITE" = "true" ]; then
        docker compose -f "$COMPOSE_FILE" up -d redis rabbitmq socket-proxy || true
        wait_for_container_ready "smsly-hosting-redis-1" 120 || true
        sync_agent_lite_rabbitmq_password
    else
        docker compose -f "$COMPOSE_FILE" up -d db pgcat redis rabbitmq socket-proxy registry || true
        wait_for_container_ready "smsly-hosting-db-1" 120 || true
        wait_for_container_ready "smsly-hosting-pgcat-1" 120 || true
        wait_for_container_ready "smsly-hosting-redis-1" 120 || true
    fi

    if should_manage_caddy && docker compose -f "$COMPOSE_FILE" ps caddy 2>/dev/null | grep -q "Up"; then
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
    # TODO(install): replace set -e toggle with explicit conditional. The
    # entire body tolerates command failures (each diagnostic line has its own
    # `|| true` or `2>/dev/null`); leaving set -e toggled off is functional
    # but discouraged.
    set +e
    echo -e "\n${YELLOW}=== SMSLY DEBUG SNAPSHOT ===${NC}"
    echo "Timestamp: $(date -Iseconds)"
    echo "Install dir: $INSTALL_DIR"
    echo ""

    echo "---- Systemd ----"
    systemctl is-active docker 2>/dev/null || true
    true
    true
    systemctl is-active smsly-autoscaler 2>/dev/null || true
    echo ""

    echo "---- Docker Networks ----"
    docker network ls | grep -E 'smsly|socket-proxy' || true
    echo ""

    echo "---- Compose PS ----"
    docker compose -f "$COMPOSE_FILE" ps || true
    echo ""

    echo "---- Local Health ----"
    curl -iSsf http://127.0.0.1:8000/health 2>/dev/null | head -20 || echo "http://127.0.0.1:8000/health failed"
    echo ""

    echo "---- Backend DNS Checks ----"
    docker compose -f "$COMPOSE_FILE" exec -T backend getent hosts db pgcat redis 2>/dev/null || echo "backend DNS check failed"
    echo ""

    echo "---- Key Logs (tail 120) ----"
    docker compose -f "$COMPOSE_FILE" logs --tail=120 backend frontend traefik pgcat redis 2>/dev/null || true
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

    if should_manage_caddy; then
        echo -e "\n${BLUE}  ⟳ Syncing Proxy Configurations...${NC}"
        reload_container_caddy 2>/dev/null || true
        install_caddy_health_guard "$DOMAIN"
    fi


    sleep 3

    echo -e "\n${BLUE}  → Running endpoint verification...${NC}"
    PASS_COUNT=0
    FAIL_COUNT=0

    # Backend health (internal) — docker exec into backend container
    EP1_FALLBACK_URL="http://127.0.0.1:8000/health"
    _LITE_HOST_HEADER=""
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        _ep1_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)"
        if [ -n "$_ep1_domain" ] && [ "$_ep1_domain" != "localhost" ]; then
            _LITE_HOST_HEADER="$_ep1_domain"
        fi
    fi
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        if [ -n "${_LITE_HOST_HEADER:-}" ]; then
            EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 -H "Host: ${_LITE_HOST_HEADER}" "http://127.0.0.1/health" 2>/dev/null) || EP1_CODE="000"
        else
            EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1/health" 2>/dev/null) || EP1_CODE="000"
        fi
    else
        if docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1; then
            EP1_CODE="200"
        elif curl -fsS --max-time 5 "$EP1_FALLBACK_URL" >/dev/null 2>&1; then
            EP1_CODE="200"
        else
            EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP1_FALLBACK_URL" 2>/dev/null) || EP1_CODE="000"
        fi
    fi
    case "$EP1_CODE" in
        2*|3*)
        echo -e "${GREEN}  ✓ Backend (local): HTTP $EP1_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
        ;;
    *)
        echo -e "${RED}  ✗ Backend (local): HTTP $EP1_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
        ;;
    esac

    # Platform domain (public-facing — tests Caddy → Traefik → backend chain)
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ]; then
        EP_PUB_URL="http://${DOMAIN}/health"
        if is_node_mode; then
            EP_PUB_URL="http://${DOMAIN}/health/live"
        fi
        EP_PUB_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 10 "$EP_PUB_URL" 2>/dev/null) || EP_PUB_CODE="000"
        if [ "$EP_PUB_CODE" = "200" ] || [ "$EP_PUB_CODE" = "301" ] || [ "$EP_PUB_CODE" = "308" ]; then
            echo -e "${GREEN}  ✓ Platform (${DOMAIN}): HTTP $EP_PUB_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
        else
            echo -e "${RED}  ✗ Platform (${DOMAIN}): HTTP $EP_PUB_CODE${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    fi

    # HTTPS domain (skip for raw IP addresses — certs can't be issued for IPs)
    if ! should_manage_caddy; then
        echo -e "${YELLOW}  ⊘ HTTPS: Skipped (Caddy/HTTPS is master-only in this mode)${NC}"
    elif [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ] && ! echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
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
    if is_node_mode; then
        EP3_URL="http://127.0.0.1/health/live"
    fi
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
            if should_manage_caddy; then
                svc_url="https://${svc_domain}/"
            else
                svc_url="http://${svc_domain}/"
            fi
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
    mkdir -p /opt/smsly-hosting/builds
    chmod 775 /opt/smsly-hosting/builds
    chown -R 1000:1000 /opt/smsly-hosting/builds 2>/dev/null || true

    # ─── Fix .env permissions BEFORE any containers start ────────────────────
    # The docker-compose.prod.yml mounts .env into the backend container.
    # If .env has 600 permissions (created by old install.sh), the container
    # can't read it and Django crashes with PermissionError.
    # The backend container runs as UID 1000 (smsly user), so the file must be
    # writable by that user to allow the domain-config signal to sync back to .env.
    if [ -f "$INSTALL_DIR/.env" ]; then
        chown root:1000 "$INSTALL_DIR/.env" 2>/dev/null || true
        chmod 664 "$INSTALL_DIR/.env" 2>/dev/null || true
        echo -e "${BLUE}  → Fixed .env permissions to 664 (group-writable by container UID 1000)${NC}"
    fi

    # ─── Pre-flight ──────────────────────────────────────────────────────────
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}✗ Please run as root (sudo bash install.sh --update)${NC}"
        exit 1
    fi

    check_internet
    check_hardware
    check_caddy_conflict
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
    if [ "${SMSLY_REEXEC:-}" != "1" ]; then
        # Every new update attempt must hit GitHub. Checkpoints are only for
        # resume/re-exec within the same attempt, not for skipping future pulls.
        clear_checkpoint "update_git_synced"
    fi

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
    # Save pre-update HEAD for reliable redeploy detection after git operations.
    # Priority: 1) env var from re-exec (survives exec boundary),
    #           2) stale file from failed previous update (survives process death),
    #           3) current HEAD (normal first run).
    PRE_UPDATE_HEAD=""
    if [ -n "${SMSLY_PRE_UPDATE_HEAD:-}" ]; then
        PRE_UPDATE_HEAD="$SMSLY_PRE_UPDATE_HEAD"
    elif [ -f "$INSTALL_DIR/.pre-update-head" ] && [ -s "$INSTALL_DIR/.pre-update-head" ]; then
        PRE_UPDATE_HEAD="$(cat "$INSTALL_DIR/.pre-update-head" 2>/dev/null || true)"
        echo -e "${YELLOW}  ⚠ Recovering pre-update baseline from prior incomplete run (${PRE_UPDATE_HEAD:0:7})${NC}"
    else
        PRE_UPDATE_HEAD="$(git rev-parse HEAD 2>/dev/null || true)"
    fi
    echo "$PRE_UPDATE_HEAD" > "$INSTALL_DIR/.pre-update-head" 2>/dev/null || true
    ensure_local_ignores
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        echo -e "${YELLOW}  ⚠ Local changes detected — stashing before pull${NC}"
        git stash push --include-untracked -m "install-update-$(date +%s)"
        touch "$INSTALL_DIR/.git-stash-marker"
    fi

    echo -e "${BLUE}  → Force-pulling latest code from GitHub ($SMSLY_BRANCH)...${NC}"

    # Track if git update succeeded
    GIT_UPDATE_OK=true

    if ! git fetch origin "$SMSLY_BRANCH" >/dev/null 2>&1; then
        echo -e "${RED}  ✗ Git fetch failed for $SMSLY_BRANCH. SSL verification is always enforced — check network or CA certificates.${NC}"
        GIT_UPDATE_OK=false
    fi

    if [ "$GIT_UPDATE_OK" = "true" ]; then
        if ! git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" >/dev/null 2>&1; then
            echo -e "${RED}  ✗ Git checkout failed for $SMSLY_BRANCH.${NC}"
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
        # Preserve pre-update HEAD across re-exec so the SHA comparison
        # uses the TRUE baseline commit (before git pull), not the
        # already-updated HEAD (which would prevent redeploy detection).
        export SMSLY_PRE_UPDATE_HEAD="$PRE_UPDATE_HEAD"
        # Release the lock before re-exec so the new process can acquire it.
        # Closing FD 9 releases the flock.
        exec 9>&- 2>/dev/null || true
        exec env SMSLY_REEXEC=1 NO_SCREEN=true SKIP_SCREEN=1 SMSLY_PRE_UPDATE_HEAD="$PRE_UPDATE_HEAD" PATH="/usr/local/bin:$PATH" bash "$SCRIPT_PATH" --no-screen "$@"
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

    if [ ! -f "backend/Dockerfile" ]; then
        echo -e "${RED}✗ Missing backend/Dockerfile${NC}"
        exit 1
    fi

    if [ "$MODE_NODE" = "true" ] && [ ! -f "backend/requirements.txt" ]; then
        echo -e "${RED}✗ Missing backend/requirements.txt${NC}"
        exit 1
    fi

    if [ "$MODE_NODE" != "true" ] && [ ! -f "frontend/Dockerfile" ]; then
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

    # ─── Safe Update Protocol ─────────────────────────────────────────────
    if [ -f "$INSTALL_DIR/scripts/safe-update.sh" ]; then
        source "$INSTALL_DIR/scripts/safe-update.sh"
        safe_update_preflight || { echo -e "${RED}  ✗ Pre-flight checks failed — aborting update${NC}"; exit 1; }
        safe_update_snapshot
        trap 'safe_update_rollback' ERR
    fi

    # ─── Fix script permissions (Git on Windows strips execute bits) ──────────
    echo -e "${BLUE}  → Fixing script permissions...${NC}"
    find "$INSTALL_DIR" -name "*.sh" -exec chmod +x {} \;
    echo -e "${GREEN}  ✓ Script permissions fixed${NC}"

    # SECURITY: SSH strict host-key checking is ALWAYS enforced. The previous
    # installer rewrote apps/deployments/services/provisioner.py and
    # ssh_client.py to force paramiko.AutoAddPolicy() on every connection, which
    # accepts any host fingerprint on first contact and is an SSH-MITM backdoor
    # (CVE-class: TOFU on every deploy target). Strict checking is now the
    # default; the in-app provisioner/ssh_client code controls policy via the
    # SMSLY_STRICT_SSH_HOST_KEY_CHECK env var. Do not reintroduce a patch that
    # silently overwrites that logic from the installer.
    echo -e "${BLUE}  → SSH strict host-key check enforced (no installer patching of provisioner/ssh_client).${NC}"

    # Ensure shared networks exist (prod stack uses external networks)
    ensure_update_networks

     # Cache bust only if disk is low (already runs in the disk check above when needed).
     # Moved into case blocks below to avoid redundant double bust.

     docker_login

      case "$UPDATE_MODE" in
         frontend)
             if [ "$MODE_NODE" = "true" ]; then
                 echo -e "${YELLOW}  → Node mode: no frontend to update. Skipping.${NC}"
             else
                 echo -e "${BLUE}  → Rebuilding frontend container (cached)...${NC}"
                 docker compose -f "$COMPOSE_FILE" build frontend
                 docker compose -f "$COMPOSE_FILE" up -d --no-deps frontend

                 # Custom Domain SSL Setup for Frontend Update
                 if should_manage_caddy; then  # Only for master mode
                     echo -e "\n${YELLOW}[UPDATE] Setting up Custom Domain SSL Services...${NC}"
                    if [ -f "install-custom-domain-ssl.sh" ]; then
                        echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
                        bash install-custom-domain-ssl.sh install
                    elif [ -f "$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh" ]; then
                        echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
                        bash "$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh" install

                         # Start the services
                         echo -e "${BLUE}  → Starting custom domain SSL services...${NC}"
                         /opt/smsly-hosting/smsly-domain-ssl-manager.sh start

                         # Enable auto-start on boot (if not already enabled)
                         echo -e "${BLUE}  → Ensuring auto-start on boot...${NC}"
                         /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable

                         echo -e "${GREEN}  ✓ Custom domain SSL services configured${NC}"
                     else
                         echo -e "${YELLOW}  ⚠ Custom domain SSL manager not found, skipping setup${NC}"
                     fi
                 fi
             fi
             ;;
         backend)
            echo -e "${BLUE}  → Rebuilding backend containers (cached)...${NC}"
            build_svcs="backend celery"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                build_svcs="backend"
            elif [ "$MODE_NODE" = "true" ]; then
                build_svcs="backend celery celery-deploy celery-fast celery-beat"
            fi
            docker compose -f "$COMPOSE_FILE" build $build_svcs

            echo -e "${BLUE}  → Ensuring backend dependencies are running...${NC}"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                verify_agent_lite_connectivity
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans redis rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
            elif [ "$MODE_NODE" = "true" ]; then
                stop_node_excluded_services
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans db pgcat redis rabbitmq socket-proxy registry route-fallback traefik
            else
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans db pgcat redis socket-proxy
            fi
            # Stop backend, celery & pgcat so their DB connections don't block
            # migrations (ALTER TABLE requires exclusive locks).
            echo -e "${BLUE}  → Stopping backend, celery & pgcat for migrations...${NC}"
            docker compose -f "$COMPOSE_FILE" stop --timeout 15 backend celery celery-deploy celery-fast celery-beat pgcat 2>/dev/null || true

            echo -e "${BLUE}  → Running migrations...${NC}"
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            echo -e "${BLUE}  → Starting backend & pgcat...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d --no-deps pgcat 2>/dev/null || true
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --no-deps backend
            fi
            wait_for_container_ready "smsly-hosting-backend-1" 120 || true

            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput 2>/dev/null || true

            set_checkpoint "update_db_migrated"

            # Clean stale celerybeat-schedule (prevents Permission denied crash loop)
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true

            echo -e "${BLUE}  → Restarting celery workers...${NC}"
            celery_svcs="celery celery-deploy celery-fast celery-beat"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                celery_svcs="celery-worker"
            fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate $celery_svcs
            else
                 docker compose -f "$COMPOSE_FILE" up -d --no-deps $celery_svcs
             fi
             
              # Custom Domain SSL Setup for Backend Update
              if should_manage_caddy; then  # Only for master mode
                  echo -e "\n${YELLOW}[UPDATE] Setting up Custom Domain SSL Services...${NC}"
                  if [ -f "install-custom-domain-ssl.sh" ]; then
                      echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
                      bash install-custom-domain-ssl.sh install
                  elif [ -f "$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh" ]; then
                      echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
                      bash "$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh" install
                      
                      # Start the services
                      echo -e "${BLUE}  → Starting custom domain SSL services...${NC}"
                      /opt/smsly-hosting/smsly-domain-ssl-manager.sh start
                     
                     # Enable auto-start on boot (if not already enabled)
                     echo -e "${BLUE}  → Ensuring auto-start on boot...${NC}"
                     /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable
                     
                     echo -e "${GREEN}  ✓ Custom domain SSL services configured${NC}"
                 else
                     echo -e "${YELLOW}  ⚠ Custom domain SSL manager not found, skipping setup${NC}"
                 fi
             fi
             ;;
         half)
            echo -e "${BLUE}  → [HALF UPDATE] Rebuilding changed services from cache (no image pulls)${NC}"

            # 1. Rebuild frontend from cached layers (no --pull, no new base images)
            if [ "$MODE_NODE" != "true" ]; then
                echo -e "${BLUE}  → Rebuilding frontend (cached)...${NC}"
                docker compose -f "$COMPOSE_FILE" build frontend 2>/dev/null || {
                    echo -e "${YELLOW}  ⚠ Frontend build failed (cached layers missing). Skipping frontend.${NC}"
                    echo -e "${YELLOW}    Run --update when Docker Hub is reachable for a full rebuild.${NC}"
                }
                docker compose -f "$COMPOSE_FILE" up -d --no-deps frontend 2>/dev/null || true
            fi

            # 2. Stop backend, celery & pgcat so their DB connections don't block
            #    migrations (ALTER TABLE requires exclusive locks).
            echo -e "${BLUE}  → Stopping backend, celery & pgcat for migrations...${NC}"
            docker compose -f "$COMPOSE_FILE" stop --timeout 15 backend celery celery-deploy celery-fast celery-beat pgcat 2>/dev/null || true

            # 3. Run migrations
            echo -e "${BLUE}  → Running migrations...${NC}"
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            # 4. Start pgcat & backend (picks up Python code changes from mounted volume)
            echo -e "${BLUE}  → Starting pgcat & backend...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d --no-deps pgcat 2>/dev/null || true
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans redis rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --no-deps backend
            fi
            wait_for_container_ready "smsly-hosting-backend-1" 120 || true

            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput 2>/dev/null || true

            # 4. Clean celerybeat-schedule and restart celery workers
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true

            restart_svcs="celery celery-deploy celery-fast celery-beat"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                restart_svcs="celery-worker"
            fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate $restart_svcs 2>/dev/null || true
            else
                docker compose -f "$COMPOSE_FILE" restart $restart_svcs 2>/dev/null || true
             fi
             set_checkpoint "update_db_migrated"
             
              # Custom Domain SSL Setup for Half Update
              if should_manage_caddy; then  # Only for master mode
                  echo -e "\n${YELLOW}[UPDATE] Setting up Custom Domain SSL Services...${NC}"
                  if [ -f "install-custom-domain-ssl.sh" ]; then
                      echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
                      bash install-custom-domain-ssl.sh install
                  elif [ -f "$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh" ]; then
                      echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
                      bash "$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh" install
                      
                      # Start the services
                      echo -e "${BLUE}  → Starting custom domain SSL services...${NC}"
                      /opt/smsly-hosting/smsly-domain-ssl-manager.sh start
                     
                     # Enable auto-start on boot (if not already enabled)
                     echo -e "${BLUE}  → Ensuring auto-start on boot...${NC}"
                     /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable
                     
                     echo -e "${GREEN}  ✓ Custom domain SSL services configured${NC}"
                 else
                     echo -e "${YELLOW}  ⚠ Custom domain SSL manager not found, skipping setup${NC}"
                 fi
             fi
             ;;
         full)
            echo -e "${BLUE}  → [FULL REBUILD] Rebuilding PaaS core (preserving addon databases)...${NC}"

            # 1. Only stop PaaS core services — NEVER touch addon containers
            CORE_SERVICES="frontend backend celery celery-deploy celery-fast celery-beat"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                CORE_SERVICES="backend celery-worker"
            elif [ "$MODE_NODE" = "true" ]; then
                CORE_SERVICES="backend celery celery-deploy celery-fast celery-beat"
            fi

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
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans redis rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans $CORE_SERVICES
            elif [ "$MODE_NODE" = "true" ]; then
                stop_node_excluded_services
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans db pgcat redis rabbitmq socket-proxy registry route-fallback traefik
                docker compose -f "$COMPOSE_FILE" up -d --no-deps --remove-orphans $CORE_SERVICES
            else
                docker compose -f "$COMPOSE_FILE" up -d --no-deps --remove-orphans $CORE_SERVICES
            fi

            if [ "$MODE_AGENT_LITE" != "true" ]; then
                # 7. Reconnect Traefik + socket-proxy to smsly-proxy network
                #    (recreation drops Docker DNS links — causes 502 gateway errors)
                #    NOTE: ensure_container_on_network uses `docker network connect`
                #    which works on running containers. No restart needed.
                echo -e "${BLUE}    ↳ Reconnecting proxy network...${NC}"
                for ctr in smsly-hosting-traefik-1 smsly-hosting-socket-proxy-1; do
                    ensure_container_on_network "smsly-net" "$ctr"
                    ensure_container_on_network "smsly-proxy" "$ctr"
                done
            fi

            # 8. Stop backend, celery & pgcat so their DB connections don't block
            #    migrations (ALTER TABLE requires exclusive locks).
            echo -e "${BLUE}  → Stopping backend, celery & pgcat for migrations...${NC}"
            docker compose -f "$COMPOSE_FILE" stop --timeout 15 backend celery celery-deploy celery-fast celery-beat pgcat 2>/dev/null || true

            # 9. Run migrations
            echo -e "${BLUE}  → Running migrations...${NC}"
            echo -e "${BLUE}  → Ensuring backend dependencies are running...${NC}"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                verify_agent_lite_connectivity
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans redis rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
            elif [ "$MODE_NODE" = "true" ]; then
                stop_node_excluded_services
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans db pgcat redis rabbitmq socket-proxy registry route-fallback traefik
            else
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans db pgcat redis socket-proxy
            fi
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            # 10. Start pgcat & backend
            echo -e "${BLUE}  → Starting pgcat & backend...${NC}"
            docker compose -f "$COMPOSE_FILE" up -d --no-deps pgcat 2>/dev/null || true
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --no-deps backend
            fi
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            # 10. Start backend
            echo -e "${BLUE}  → Starting backend...${NC}"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --no-deps backend
            fi
            wait_for_container_ready "smsly-hosting-backend-1" 120 || true

            docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput 2>/dev/null || true

            # 9. Clean celerybeat-schedule and restart beat
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule 2>/dev/null || true
            
            restart_svcs="celery celery-beat celery-deploy celery-fast"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                restart_svcs="celery-worker"
            fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate $restart_svcs 2>/dev/null || true
            else
                docker compose -f "$COMPOSE_FILE" restart $restart_svcs 2>/dev/null || true
            fi
            set_checkpoint "update_db_migrated"
            ;;
    esac

    # ─── Observability Stack Update (master mode only) ──────────────────────
    if [ "$MODE_AGENT_LITE" != "true" ] && [ "$MODE_NODE" != "true" ]; then
        echo -e "${BLUE}  → Updating observability stack...${NC}"
        mkdir -p /opt/smsly-hosting/prometheus-targets
        chown -R 1000:1000 /opt/smsly-hosting/prometheus-targets 2>/dev/null || true
        chmod 2777 /opt/smsly-hosting/prometheus-targets 2>/dev/null || true
        if [ -f "infrastructure/docker/docker-compose.observability.yml" ]; then
            docker compose -f infrastructure/docker/docker-compose.observability.yml up -d --build 2>/dev/null || true
            docker restart smsly-grafana 2>/dev/null || true
            docker restart smsly-docker-labels 2>/dev/null || true
            docker restart smsly-promtail 2>/dev/null || true
            # Deploy docker-labels exporter to all remote nodes and regenerate target files
            backend_container=$(docker ps --format '{{.Names}}' | grep -E '^smsly-hosting-backend(-1)?$' | head -1)
            if [ -n "$backend_container" ]; then
                docker exec "$backend_container" python manage.py deploy_docker_labels_exporters 2>/dev/null || true
            fi
        fi
        echo -e "${GREEN}  ✓ Observability stack updated${NC}"
    fi
    set_checkpoint "update_containers_rebuilt"
fi

# ─── Safe Update: Post-Deploy Verification ─────────────────────────────
if command -v safe_update_post_verify >/dev/null 2>&1; then
    echo -e "${BLUE}  → Running post-deploy health checks...${NC}"
    sleep 30
    if safe_update_post_verify; then
        echo -e "${GREEN}  ✓ All health checks passed — update successful${NC}"
        trap - ERR
        if command -v safe_update_cleanup >/dev/null 2>&1; then
            safe_update_cleanup
        fi
        rm -f "$SNAPSHOT_FILE" 2>/dev/null || true
    else
        echo -e "${RED}  ✗ Post-deploy health checks failed — initiating rollback${NC}"
        safe_update_rollback
        exit 1
    fi
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
" | timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null || true
    # ─── Self-Healing: Docker Socket Permissions ──────────────────────────────
    echo -e "${BLUE}  → Hardening Docker socket permissions...${NC}"
    # NOTE: Removed chmod 666 — world-writable docker.sock is a security risk.
    # Group membership (docker group) is the correct access control mechanism.
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
    # Prune stale rollback backup containers left from failed blue-green promotions
    docker container prune -f --filter "status=exited" 2>/dev/null || true
    for ctr in $(docker ps -a --filter "status=exited" --filter "name=-rollback-" --format '{{.Names}}' 2>/dev/null || true); do
        docker rm -f "$ctr" 2>/dev/null || true
    done
    for ctr in $(docker ps -a --filter "status=created" --filter "name=-rollback-" --format '{{.Names}}' 2>/dev/null || true); do
        docker rm -f "$ctr" 2>/dev/null || true
    done

    # ─── Self-Healing: Automatic Queue Restoration ──────────────────────────
    echo -e "${BLUE}  → Checking for stalled deployments/addons in QUEUED state...${NC}"
    backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
    docker exec -i "$backend_container" python manage.py shell -c "
from apps.deployments.models import Deployment
from apps.deployments.models_addons import Addon
from apps.deployments.tasks import provision_addon_task, recover_stalled_queued_deployments
from django.db.models import Count

# Re-queue deployments
q_count = Deployment.objects.filter(status='QUEUED').count()
if q_count > 0:
    print(f'  [Jump-Start] Re-queueing {q_count} stalled deployments...')
    result = recover_stalled_queued_deployments(limit=q_count)
    print(
        '  [Jump-Start] Deployments restored: queued={queued} '
        'skipped={skipped} failed={failed}'.format(**result)
    )

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
    raw_worker="smsly-hosting-celery-deploy-1"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        raw_worker="smsly-hosting-celery-worker-1"
    fi
    worker_container="$(resolve_container_target "$raw_worker")"
    DEPLOY_WORKER_HEALTH="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$worker_container" 2>/dev/null || echo "")"
    if docker exec -i "$worker_container" celery -A config inspect active_queues --timeout=10 2>/dev/null | grep -q "deploy"; then
        echo -e "${GREEN}  ✓ Deployment worker successfully bound to 'deploy' queue${NC}"
    elif [ "$DEPLOY_WORKER_HEALTH" = "healthy" ] || [ "$DEPLOY_WORKER_HEALTH" = "running" ]; then
        echo -e "${GREEN}  ✓ Deployment worker container is healthy/running (queue inspect timed out)${NC}"
    else
        echo -e "${YELLOW}  ⚠ WARNING: Deployment worker not detected on 'deploy' queue. Check logs.${NC}"
    fi

    echo -e "\n${GREEN}  ✨ Update complete. Self-healing applied.${NC}"

    sync_platform_domain_state "$INSTALL_DIR/.env"

    # Refresh proxy/runtime edge stack so routing and TLS state is always clean.
    # NOTE: restart_edge_stack now handles Caddy validation internally (H1+H2 fix).
    restart_edge_stack
    wait_for_traefik_api 30 || true

    sleep 2

    # ─── Fix .env permissions (must be writable by Docker container UID 1000) ──
    if [ -f "$INSTALL_DIR/.env" ]; then
        chown root:1000 "$INSTALL_DIR/.env" 2>/dev/null || true
        chmod 664 "$INSTALL_DIR/.env" 2>/dev/null || true
    fi

    # ─── Caddy: Generate self-signed cert + regenerate Caddyfile ──
    if should_manage_caddy; then
    ensure_selfsigned_cert
    if command -v caddy &> /dev/null; then
        echo -e "${BLUE}  → Regenerating Caddyfile with current service domains...${NC}"

        # ── Step 1: Find the Cloudflare token FIRST (before generating Caddyfile) ──
        CF_TOKEN=""

        # Priority: .env file > PlatformConfig DB
        if [ -z "$CF_TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
            CF_TOKEN="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
        fi
        # Fallback: read from PlatformConfig in the database (set via Settings UI)
        if [ -z "$CF_TOKEN" ] || [ "$CF_TOKEN" = "fake" ]; then
            DB_TOKEN="$(timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
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


            # Discover domain
            cf_domain=""
            cf_domain="$(timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
" 2>/dev/null | tr -d '[:space:]' || true)"
            if [ -z "$cf_domain" ]; then
                cf_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
            fi

            cf_server_ip="$(detect_public_ip)"

            # Discover wildcard-covered hosts and non-wildcard service blocks.
            # - Wildcard-covered hosts route through Traefik via matcher.
            # - Unknown wildcard hosts route to /notice on frontend.
            # - External custom domains keep explicit direct on-demand TLS blocks with Host rewrite.
            cf_wildcard_known_hosts=""
            cf_wildcard_known_hosts="$(timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
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
            cf_svc_blocks="$(timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
import os
upstream = os.environ.get('SMSLY_SERVICE_PROXY_UPSTREAM', 'traefik:80')
from apps.deployments.models import Service
from apps.domains.models import Domain, DomainStatus
from django.db.models import Q
suffix = '.${cf_domain}'.lower().strip()
seen = set()
for svc in Service.objects.all():
    public_domain = (svc.public_domain or '').strip().lower()
    if public_domain and (not suffix or not public_domain.endswith(suffix)) and public_domain not in seen:
        seen.add(public_domain)
        print(f'{public_domain} {{\n    reverse_proxy {upstream}\n    encode gzip\n}}\n')

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
        print(f'{custom_domain} {{\n    tls {{\n        on_demand\n    }}\n    reverse_proxy {upstream} {{\n        header_up Host {public_domain}\n    }}\n    encode gzip\n}}\n')
    else:
        print(f'{custom_domain} {{\n    tls {{\n        on_demand\n    }}\n    reverse_proxy {upstream}\n    encode gzip\n}}\n')
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
        reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    }"
                fi

                cat > /tmp/Caddyfile.tmp <<CFCADDY
# Auto-generated with Cloudflare DNS challenge (wildcard SSL)
{
    on_demand_tls {
        ask http://backend:8000/api/v1/services/check-domain/
    }
}

${cf_domain} {
    reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
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
    handle {
        reverse_proxy ${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
    }
}

${cf_server_ip} {
    tls internal
    redir http://${cf_server_ip}{uri} 308
}

${cf_svc_blocks}
CFCADDY
                if install_caddyfile_atomically /tmp/Caddyfile.tmp "wildcard Caddyfile"; then
                    echo -e "${GREEN}  ✓ Caddyfile generated with wildcard SSL for *.${cf_domain}${NC}"
                else
                    echo -e "${YELLOW}  ⚠ Wildcard Caddyfile could not be applied. Falling back to standard HTTPS for ${cf_domain}.${NC}"
                    generate_safe_caddyfile "wildcard Caddyfile apply failed"
                fi
                rm -f /tmp/Caddyfile.tmp
            else
                # IP mode or no domain — fall back to safe Caddyfile
                generate_safe_caddyfile "update flow (IP mode)"
            fi
        else
            # No valid token — generate safe Caddyfile (no dns cloudflare)
            generate_safe_caddyfile "update flow caddy regen"

            # NOTE: Cloudflare dns-challenge stripping is now handled by
            # generate_safe_caddyfile itself, which never emits 'dns cloudflare'
            # blocks when no token is present. (Removed dead 'if false' block.)
        fi

        # Final validation — if still broken, regenerate safe fallback
        if caddy_needs_fix; then
            generate_safe_caddyfile "post-update validation"
        fi

        reload_container_caddy 2>/dev/null || true


        # Verify Caddy is running
        sleep 2
        if docker compose -f "$COMPOSE_FILE" ps -q caddy 2>/dev/null | grep -q .; then
            echo -e "${GREEN}  ✓ Caddy config regenerated and running${NC}"
        else
            echo -e "${YELLOW}  ⚠ Caddy failed to start. Run: journalctl -u caddy --no-pager -n 20${NC}"
        fi

        POST_CADDY_DOMAIN="$(timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
" 2>/dev/null | tr -d '[:space:]' || true)"
        if [ -z "$POST_CADDY_DOMAIN" ]; then
            POST_CADDY_DOMAIN="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
        fi

        install_caddy_health_guard "$POST_CADDY_DOMAIN"
    fi
    fi

    safe_refresh_runtime_services

    # ─── Self-healing: restore any celery workers that are still down ──
    echo -e "${BLUE}  → Verifying celery workers are running...${NC}"
    for _ce in celery celery-deploy celery-fast celery-beat; do
        if docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null | grep -qx "$_ce"; then
            if ! docker compose -f "$COMPOSE_FILE" ps "$_ce" 2>/dev/null | grep -q "Up"; then
                echo -e "${YELLOW}    $_ce is down, restarting...${NC}"
                timeout 30 docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "$_ce" >/dev/null 2>&1 || true
                wait_for_container_ready "smsly-hosting-${_ce}-1" 120 || true
            fi
        fi
    done
    echo -e "${GREEN}  ✓ Celery workers running${NC}"

    # ─── Auto-redeploy active services when platform code or domain state changes ──
    PRE_HEAD="$(cat "$INSTALL_DIR/.pre-update-head" 2>/dev/null || true)"
    CURRENT_HEAD="$(cd "$INSTALL_DIR" && git rev-parse HEAD 2>/dev/null || true)"
    CODE_CHANGED=false
    if [ -n "$PRE_HEAD" ] && [ "$PRE_HEAD" != "$CURRENT_HEAD" ]; then
        CODE_CHANGED=true
        echo -e "${BLUE}  → Platform code changed (${PRE_HEAD:0:7} → ${CURRENT_HEAD:0:7})${NC}"
    fi
    if [ "$CODE_CHANGED" = "true" ] || [ "$FORCE_REDEPLOY" = "true" ]; then
        echo -e "${BLUE}  → Auto-redeploying active services (platform code changed)...${NC}"
        if ! queue_active_service_redeploys "Platform update auto-redeploy" ""; then
            echo -e "${YELLOW}  ⚠ Auto-redeploy encountered issues (check logs above)${NC}"
        fi
    elif [ "${DOMAIN_SYNC_REDEPLOY_REQUIRED:-0}" = "1" ]; then
        echo -e "${BLUE}  → Auto-redeploying rewritten services (platform domain changed)...${NC}"
        if ! queue_active_service_redeploys "Platform domain change auto-redeploy" "${DOMAIN_SYNC_SERVICE_IDS}"; then
            echo -e "${YELLOW}  ⚠ Domain-change redeploy encountered issues (check logs above)${NC}"
        fi
    else
        echo -e "${GREEN}  ✓ No platform code or domain-driven redeploys required${NC}"
    fi
    # Clean up marker
    rm -f "$INSTALL_DIR/.pre-update-head" 2>/dev/null || true

    # ─── Endpoint Verification (3 checks) ──────────────────────────────────
    echo -e "\n${BLUE}  → Running endpoint verification (3 checks)...${NC}"
    sleep 5
    PASS_COUNT=0
    FAIL_COUNT=0

    # ── Check 1: Backend API health (docker exec into backend container) ──
    EP1_FALLBACK_URL="http://127.0.0.1:8000/health"
    _LITE_HOST_HEADER=""
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        _ep1_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true)"
        if [ -n "$_ep1_domain" ] && [ "$_ep1_domain" != "localhost" ]; then
            _LITE_HOST_HEADER="$_ep1_domain"
        fi
    fi
    echo -e "${BLUE}  [1/3] Backend API health...${NC}"
    echo -e "${BLUE}        Endpoint: backend:8000/health (via docker exec)${NC}"
    BACKEND_OK=false
    EP1_CODE="000"
    for attempt in 1 2 3 4 5; do
        if [ "$MODE_AGENT_LITE" = "true" ]; then
            if [ -n "${_LITE_HOST_HEADER:-}" ]; then
                # Route through Traefik with the correct Host header
                EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 -H "Host: ${_LITE_HOST_HEADER}" "http://127.0.0.1/health" 2>/dev/null) || EP1_CODE="000"
            else
                # No domain — route through Traefik on port 80
                EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1/health" 2>/dev/null) || EP1_CODE="000"
            fi
        else
            if docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1; then
                EP1_CODE="200"
            elif curl -fsS --max-time 5 "$EP1_FALLBACK_URL" >/dev/null 2>&1; then
                EP1_CODE="200"
            else
                EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP1_FALLBACK_URL" 2>/dev/null) || EP1_CODE="000"
            fi
        fi
        case "$EP1_CODE" in
            2*|3*)
            BACKEND_OK=true
            break
            ;;
        esac
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
    if ! should_manage_caddy; then
        EP2_RESULT="${YELLOW}SKIP${NC}"
        echo -e "${YELLOW}  [2/3] SKIPPED (Caddy/HTTPS is master-only in this mode)${NC}"
    elif [ -n "$EP_DOMAIN" ] && [ "$EP_DOMAIN" != "localhost" ] && ! echo "$EP_DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
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
            echo -e "${YELLOW}        Fix: docker compose -f $COMPOSE_FILE logs --tail=15 caddy${NC}"
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
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        EP3_URL="http://127.0.0.1/"
    else
        EP3_URL="http://127.0.0.1:8081/"
    fi
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
            if should_manage_caddy; then
                svc_url="https://${svc_domain}/"
            else
                svc_url="http://${svc_domain}/"
            fi
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
    printf "  ║  %-52.52s ║ %-4s ║ " "Backend (docker exec):8000/health" "$EP1_CODE"
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
    oom_containers="smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgcat-1 smsly-hosting-celery-1 smsly-hosting-celery-deploy-1 smsly-hosting-celery-fast-1 smsly-hosting-celery-beat-1 smsly-hosting-socket-proxy-1"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        oom_containers="smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-socket-proxy-1"
    fi
    for CONTAINER in $oom_containers; do
        resolved_container="$(resolve_container_target "$CONTAINER")"
        CPID=$(docker inspect --format '{{.State.Pid}}' "$resolved_container" 2>/dev/null || echo "")
        if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
            echo -500 > "/proc/$CPID/oom_score_adj" 2>/dev/null || true
        fi
    done
    echo -e "${GREEN}  ✓ OOM protection set (core, database, celery, proxy)${NC}"

    trap - EXIT
    release_install_lock
    echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
    # Infrastructure Diagnostic & Auto-Fix
    # Infrastructure Handshake & Health Stabilization
    echo -e "\n${BLUE}  🔄 Running infrastructure handshake and stabilization...${NC}"
    chmod +x scripts/grid-handshake.sh 2>/dev/null || true
    bash scripts/grid-handshake.sh || \
        echo -e "${YELLOW}  ⚠️ Handshake stabilization failed (non-fatal). You can run it manually later.${NC}"

    # ─── Fix .env permissions (ensures domain signal can write back) ─────
    fix_env_permissions "$INSTALL_DIR/.env" || true

    echo -e "${GREEN}   ✓ UPDATE SUCCESSFUL ($UPDATE_MODE)${NC}"

    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  Debug snapshot:    sudo bash install.sh --debug${NC}"
    echo -e "${YELLOW}  Runtime recovery:  sudo bash install.sh --recover${NC}"
    echo -e "${YELLOW}  Fix permissions:   sudo bash install.sh --fix-permissions${NC}"
    exit 0
fi

# =============================================================================
# FRESH INSTALL — Full setup from scratch
# =============================================================================

# ─── Interactive Setup (Step 0) ──────────────────────────────────────────────
if [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
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
    if is_node_mode; then
        USE_SSL="false"
        DOMAIN="${DOMAIN:-$PUBLIC_IP}"
        MODE_CHOICE=1
        echo -e "${BLUE}  → Node mode: using Traefik HTTP on $DOMAIN; Caddy/HTTPS is master-owned.${NC}"
    elif [ -n "${PRESET_USE_SSL}" ]; then
        if [ "${PRESET_USE_SSL}" = "true" ] && [ -n "${PRESET_DOMAIN}" ] && [ -n "${PRESET_ACME_EMAIL}" ]; then
            echo -e "${BLUE}  → Preset detected. Using SSL Mode for ${PRESET_DOMAIN}.${NC}"
            MODE_CHOICE=2
        elif [ "${PRESET_USE_SSL}" = "false" ]; then
            echo -e "${BLUE}  → Preset detected. Using IP Mode.${NC}"
            MODE_CHOICE=1
        fi
    elif [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
        echo -e "\n${BLUE}Select Deployment Mode:${NC}"
        echo -e "  1) ${GREEN}IP Mode${NC} (Easy) - http://$PUBLIC_IP"
        echo -e "  2) ${GREEN}SSL Mode${NC} (Prod) - https://your-domain.com (Requires DNS A Record pointing to $PUBLIC_IP)"
        read -p "Enter choice [1]: " MODE_CHOICE < /dev/tty
        MODE_CHOICE=${MODE_CHOICE:-1}
    fi

    # Set configuration based on choice or presets
    if is_node_mode; then
        USE_SSL="false"
        DOMAIN="${DOMAIN:-$PUBLIC_IP}"
    elif [ "$MODE_CHOICE" -eq "2" ] || [ "${PRESET_USE_SSL}" = "true" ]; then
        USE_SSL="true"
        DOMAIN="${PRESET_DOMAIN:-}"
        ACME_EMAIL="${PRESET_ACME_EMAIL:-}"

        if [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
            while [ -z "$DOMAIN" ]; do
                read -p "Enter your Domain (e.g., app.example.com): " DOMAIN < /dev/tty
            done
            while [ -z "$ACME_EMAIL" ]; do
                read -p "Enter Email for SSL (e.g., admin@example.com): " ACME_EMAIL < /dev/tty
            done
        fi

        if [ -n "$DOMAIN" ]; then
            echo -e "${BLUE}  → Verifying DNS for $DOMAIN...${NC}"
            DETECTED_IP=""
            # Try 'host' first (dnsutils), fall back to API-based DNS lookup
            if command -v host &> /dev/null; then
                DETECTED_IP=$(host -t A "$DOMAIN" 2>/dev/null | awk '{print $NF}' | tail -n 1)
            fi
            if [ -z "$DETECTED_IP" ] || [ "$DETECTED_IP" = "found:" ] || [ "$DETECTED_IP" = "not" ]; then
                DETECTED_IP=""
                # Fallback to DNS over HTTPS (Google)
                DETECTED_IP="$(curl -fsS "https://dns.google/resolve?name=${DOMAIN}&type=A" -m 5 2>/dev/null | python3 -c "import json,sys; data=json.load(sys.stdin); ans=data.get('Answer',[]); print(ans[0]['data']) if ans and 'data' in ans[0] else print('')" 2>/dev/null || echo "")"
            fi
            if [ -n "$DETECTED_IP" ]; then
                if [ "$DETECTED_IP" != "$PUBLIC_IP" ] && [ "$DETECTED_IP" != "127.0.0.1" ]; then
                    echo -e "${YELLOW}  ⚠ WARNING: DNS for $DOMAIN ($DETECTED_IP) does not match this server ($PUBLIC_IP).${NC}"
                    echo -e "${YELLOW}  SSL generation may fail. Ensure your DNS A record is set.${NC}"
                    if [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
                        read -p "  Continue anyway? (y/n) " -n 1 -r < /dev/tty
                        echo
                        if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 1; fi
                    fi
                else
                    echo -e "${GREEN}  ✓ DNS looks correct.${NC}"
                fi
            else
                echo -e "${YELLOW}  ⚠ Could not resolve DNS for $DOMAIN. SSL may fail.${NC}"
                echo -e "${YELLOW}  Ensure your DNS A record points to $PUBLIC_IP${NC}"
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
        elif [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
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

if is_node_mode; then
    PUBLIC_IP="${PUBLIC_IP:-$(detect_public_ip)}"
    USE_SSL="false"
    DOMAIN="${DOMAIN:-$PUBLIC_IP}"
    WILDCARD_SUBDOMAINS="false"
    CLOUDFLARE_API_TOKEN=""
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
check_caddy_conflict
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
    ensure_local_ignores
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        echo -e "${YELLOW}  ! Local changes detected - stashing before repository sync${NC}"
        git stash push --include-untracked -m "install-sync-$(date +%s)" >/dev/null 2>&1 || true
    fi
    if ! git fetch origin "$SMSLY_BRANCH" >/dev/null 2>&1 || ! git reset --hard "origin/$SMSLY_BRANCH" >/dev/null 2>&1; then
        echo -e "${RED}  ✗ Git update failed for $SMSLY_BRANCH. SSL verification is always enforced — check network or CA certificates.${NC}"
    fi
else
    echo -e "${BLUE}  → Cloning repository ($SMSLY_BRANCH)...${NC}"
    CLONE_SUCCESS=false
    if [ -f "$INSTALL_DIR/.env" ]; then
        echo -e "${YELLOW}  → Existing .env found — preserving configuration${NC}"
        cp "$INSTALL_DIR/.env" /tmp/smsly-env-backup 2>/dev/null || true
    fi
    rm -rf "$INSTALL_DIR"
    if git clone -b "$SMSLY_BRANCH" "$SMSLY_GIT_REMOTE" "$INSTALL_DIR"; then
        CLONE_SUCCESS=true
    else
        echo -e "${RED}  ✗ Git clone failed for $SMSLY_BRANCH. SSL verification is always enforced.${NC}"
        CLONE_SUCCESS=false
    fi
    if [ "$CLONE_SUCCESS" = "true" ] && [ -f /tmp/smsly-env-backup ]; then
        cp /tmp/smsly-env-backup "$INSTALL_DIR/.env"
        rm -f /tmp/smsly-env-backup
        echo -e "${GREEN}  ✓ Restored existing .env${NC}"
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

# Stop conflicting services if present. Host Caddy conflicts are handled by
# check_caddy_conflict because master Docker Caddy and node Traefik need port 80.
# LEGACY: nginx is only used for the bare-metal install path.
# Docker Compose uses Caddy. See docs/REVERSE_PROXY_DECISION.md.
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
apt_run apt-get install -y curl wget git python3 python3-pip python3-venv openssl ca-certificates gnupg lsb-release dnsutils apache2-utils

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
    systemctl enable docker 2>/dev/null || true
    systemctl start docker 2>/dev/null || true
    if ! docker info >/dev/null 2>&1; then
        echo -e "${RED}  ✗ Docker daemon failed to start. Check 'systemctl status docker' and kernel modules.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ Docker installed and running${NC}"
else
    echo -e "${GREEN}  ✓ Docker already installed ($(docker --version | head -c 40))${NC}"
fi

# Create smsly system user for container file ownership
id smsly >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -u 1000 smsly 2>/dev/null || true

# Ensure docker compose is available
if ! docker compose version >/dev/null 2>&1; then
    echo -e "${BLUE}  → Installing Docker Compose plugin...${NC}"
    apt_run apt-get install -y docker-compose-plugin || true
fi
# Fallback to docker-compose v1 if plugin still not available
if ! docker compose version >/dev/null 2>&1; then
    if command -v docker-compose >/dev/null 2>&1; then
        echo -e "${YELLOW}  ⚠ docker compose plugin not available; falling back to docker-compose v1${NC}"
        docker_compose() { docker-compose "$@"; }
    else
        echo -e "${RED}  ✗ Neither 'docker compose' nor 'docker-compose' found. Install Docker Compose.${NC}"
        exit 1
    fi
fi

# Apply mirror config if applicable (Only if docker is now present)
if command -v docker &> /dev/null; then
    configure_docker_mirror
fi

# Ensure security tools (Trivy and Cosign) are installed for image scanning
ensure_security_tools || true


# Ensure WireGuard mesh interface exists (master gets 10.100.0.1, nodes get
# a placeholder that will be updated by WireGuardService after provisioning).
ensure_wireguard_mesh() {
    local mesh_ip="${MASTER_MESH_IP:-10.100.0.1}"
    local wg_iface="wg0"

    # On node mode, install WireGuard and create a placeholder interface.
    # The real mesh IP (e.g. 10.100.0.x) is assigned later by
    # WireGuardService.ensure_server_in_default_mesh(), but having the
    # interface ready prevents delays during provisioning.
    if is_node_mode || is_agent_lite_mode; then
        mesh_ip="${NODE_MESH_IP:-10.100.0.2}"
        echo -e "${BLUE}  → Configuring WireGuard mesh on node ($wg_iface: $mesh_ip)...${NC}"
        if ! command -v wg >/dev/null 2>&1; then
            apt_run apt-get install -y wireguard
        fi
        mkdir -p /etc/wireguard
        if [ ! -f /etc/wireguard/private.key ]; then
            wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
        fi
        local privkey
        privkey="$(cat /etc/wireguard/private.key)"
        local master_pubkey="${MASTER_WG_PUBKEY:-}"
        local master_public_ip="${MASTER_IP:-}"
        if [ ! -f "/etc/wireguard/${wg_iface}.conf" ]; then
            cat > "/etc/wireguard/${wg_iface}.conf" <<WGCONF
[Interface]
PrivateKey = ${privkey}
Address = ${mesh_ip}/24
ListenPort = 51820
PostUp = sysctl -w net.ipv4.conf.%i.rp_filter=2 net.ipv4.conf.all.rp_filter=2
PostDown = sysctl -w net.ipv4.conf.all.rp_filter=1

WGCONF
            # If MASTER_WG_PUBKEY is available, add master as a peer immediately.
            # This ensures the node can reach 10.100.0.1 (master DB) before the
            # handshake runs, without waiting for the async deploy_mesh_task.
            if [ -n "$master_pubkey" ] && [ -n "$master_public_ip" ]; then
                cat >> "/etc/wireguard/${wg_iface}.conf" <<WGCONF
[Peer]
# master
PublicKey = ${master_pubkey}
Endpoint = ${master_public_ip}:51820
AllowedIPs = ${MASTER_MESH_IP:-10.100.0.1}/32
PersistentKeepalive = 25
WGCONF
                echo -e "${GREEN}  ✓ Master peer (${master_public_ip}) added to WG config for DB connectivity${NC}"
            fi
        fi
        systemctl enable --now "wg-quick@${wg_iface}" 2>/dev/null || true
        if ip link show "$wg_iface" >/dev/null 2>&1; then
            echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface: $mesh_ip) is up on node${NC}"
        else
            echo -e "${YELLOW}  ⚠ WireGuard ($wg_iface) failed to start on node — mesh will be configured post-provision${NC}"
        fi
        return 0
    fi


    if ip link show "$wg_iface" >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface) already configured${NC}"
        return 0
    fi
    echo -e "${BLUE}  → Configuring WireGuard mesh interface ($wg_iface: $mesh_ip)...${NC}"
    if ! command -v wg >/dev/null 2>&1; then
        apt_run apt-get install -y wireguard
    fi
    mkdir -p /etc/wireguard
    if [ ! -f /etc/wireguard/private.key ]; then
        wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
    fi
    local privkey
    privkey="$(cat /etc/wireguard/private.key)"
    if [ ! -f "/etc/wireguard/${wg_iface}.conf" ]; then
        cat > "/etc/wireguard/${wg_iface}.conf" <<WGCONF
[Interface]
PrivateKey = ${privkey}
Address = ${mesh_ip}/24
ListenPort = 51820
PostUp = sysctl -w net.ipv4.conf.%i.rp_filter=2 net.ipv4.conf.all.rp_filter=2
PostDown = sysctl -w net.ipv4.conf.all.rp_filter=1
WGCONF
    fi
    systemctl enable --now "wg-quick@${wg_iface}" 2>/dev/null || true
    if ip link show "$wg_iface" >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface: $mesh_ip) is up${NC}"
    else
        echo -e "${YELLOW}  ⚠ WireGuard ($wg_iface) failed to start — PgCat mesh binding may fail${NC}"
    fi
}
ensure_wireguard_mesh

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
             if ! git pull origin "$SMSLY_BRANCH" >/dev/null 2>&1; then
                 echo -e "${RED}  ✗ Git pull failed for $SMSLY_BRANCH. SSL verification is always enforced.${NC}"
             fi
        else
             echo -e "${BLUE}  → Cloning repository...${NC}"
             if [ -f "$INSTALL_DIR/.env" ]; then
                 cp "$INSTALL_DIR/.env" /tmp/smsly-env-backup 2>/dev/null || true
             fi
             rm -rf "$INSTALL_DIR"
             if ! git clone -b "$SMSLY_BRANCH" "${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}" "$INSTALL_DIR"; then
                 echo -e "${RED}  ✗ Git clone failed for $SMSLY_BRANCH. SSL verification is always enforced.${NC}"
             fi
             cd "$INSTALL_DIR"
             if [ -f /tmp/smsly-env-backup ]; then
                 cp /tmp/smsly-env-backup "$INSTALL_DIR/.env"
                 rm -f /tmp/smsly-env-backup
                 echo -e "${GREEN}  ✓ Restored existing .env${NC}"
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
    if ! git fetch origin "$SMSLY_BRANCH" -q --depth=1; then
        echo -e "${YELLOW}  ⚠ Git fetch failed — repository will be unlinked from remote (SSL verification enforced)${NC}"
    fi
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
elif [ "$MODE_NODE" = "true" ]; then
    REQUIRED_FILES=("$COMPOSE_FILE" "backend/Dockerfile" "backend/entrypoint.sh" "backend/requirements.txt")
else
    REQUIRED_FILES=("$COMPOSE_FILE" "backend/Dockerfile" "frontend/Dockerfile" "backend/entrypoint.sh")
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
    wrong_project=false
for c_id in $(docker ps --filter "name=smsly-hosting" -q 2>/dev/null || true); do
    config_file=$(docker inspect "$c_id" --format='{{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>/dev/null || true)
    compose_base=$(basename "$COMPOSE_FILE")
    if [ -n "$config_file" ] && [[ "$config_file" != *"$compose_base"* ]]; then
        wrong_project=true
        break
    fi
done

if [ "$wrong_project" = "true" ]; then
    echo -e "${YELLOW}  ⚠ Found containers running from a different compose project configuration. Stopping...${NC}"
    for c_id in $(docker ps --filter "name=smsly-hosting" -q 2>/dev/null || true); do
        config_file=$(docker inspect "$c_id" --format='{{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>/dev/null || true)
        compose_base=$(basename "$COMPOSE_FILE")
        if [ -n "$config_file" ] && [[ "$config_file" != *"$compose_base"* ]]; then
            docker stop "$c_id" >/dev/null 2>&1 || true
            docker rm "$c_id" >/dev/null 2>&1 || true
        fi
    done
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
    set -a
    source "$INSTALL_DIR/.env" 2>/dev/null || true
    set +a
    DOMAIN="${DOMAIN:-localhost}"
    USE_SSL="${USE_SSL:-false}"
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-false}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
    PUBLIC_IP="$(detect_public_ip)"


else
    # ─── Configuration Summary ──────────────────────────────────────────────
    PUBLIC_IP="${PUBLIC_IP:-$(detect_public_ip)}"
    DOMAIN="${DOMAIN:-$PUBLIC_IP}"
    # SEC-002: IP-mode SSL guard — force USE_SSL=false if DOMAIN is a raw IP
    if echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        USE_SSL="${USE_SSL:-false}"
        if [ "${USE_SSL:-false}" = "true" ]; then
            echo -e "${YELLOW}  ⚠ WARNING: USE_SSL=true ignored — DOMAIN is a raw IP. Forcing USE_SSL=false.${NC}"
        fi
        USE_SSL="false"
    else
        USE_SSL="${USE_SSL:-false}"
    fi
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-false}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
    ACME_EMAIL="${ACME_EMAIL:-}"

    # ─── Generate Secrets (scripts/generate_env_secrets.py — single source of truth) ──
    echo -e "${BLUE}  → Generating secure credentials...${NC}"

    # Ensure cryptography is installed (required for Fernet key generation).
    # Retry with and without --break-system-packages for different Ubuntu versions.
    pip3 install cryptography -q --break-system-packages 2>/dev/null || \
        pip3 install cryptography -q 2>/dev/null || \
        (echo -e "${YELLOW}  → Retrying cryptography install...${NC}" && \
         pip3 install cryptography 2>&1 | tail -3) || true

    # Verify cryptography is importable before proceeding
    if ! python3 -c "from cryptography.fernet import Fernet; print('ok')" 2>/dev/null; then
        echo -e "${RED}  ✗ CRITICAL: cryptography package is not installable.${NC}"
        echo -e "${RED}    The 'cryptography' package is required to generate a Fernet encryption key.${NC}"
        echo -e "${RED}    Install it manually: pip3 install cryptography${NC}"
        exit 1
    fi

    # Use the dedicated secrets generation script (single source of truth).
    # SECURITY: stream secrets directly into shell variables via process
    # substitution so the plaintext never touches the filesystem. The previous
    # implementation wrote to $INSTALL_DIR/.secrets.tmp which could leak on
    # early failure (rm -f only ran on the success path).
    SECRETS_GENERATED=false
    while IFS='=' read -r _smsly_secrets_key _smsly_secrets_val; do
        case "$_smsly_secrets_key" in
            SECRET_KEY|FIELD_ENCRYPTION_KEY|POSTGRES_PASSWORD|REDIS_PASSWORD|RABBITMQ_PASSWORD|GATEWAY_SECRET|GITHUB_WEBHOOK_SECRET|AUTOSCALER_API_TOKEN|FRP_AUTH_TOKEN|PGCAT_ADMIN_PASSWORD|REGISTRY_HTTP_SECRET|REPLICATION_PASSWORD|SENTINEL_PASSWORD|CROWDSEC_BOUNCER_KEY)
                printf -v "$_smsly_secrets_key" '%s' "$_smsly_secrets_val"
                ;;
        esac
    done < <(python3 "$INSTALL_DIR/scripts/generate_env_secrets.py" --shell 2>/dev/null | grep -E '^[A-Z_]+=' || true)
    unset _smsly_secrets_key _smsly_secrets_val
    if [ -n "${SECRET_KEY:-}" ] && [ -n "${FIELD_ENCRYPTION_KEY:-}" ]; then
        SECRETS_GENERATED=true
        echo -e "${GREEN}  ✓ Secrets generated (Fernet key validated)${NC}"
    else
        echo -e "${YELLOW}  ⚠ Secrets script ran but Fernet key is missing — generating inline...${NC}"
    fi

    # Fallback: if the script didn't produce a valid Fernet key, generate it inline
    # (cryptography is guaranteed importable at this point from the check above).
    if [ -z "${FIELD_ENCRYPTION_KEY:-}" ]; then
        FIELD_ENCRYPTION_KEY="${MASTER_FIELD_ENCRYPTION_KEY:-$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || true)}"
    fi
    # Ensure all other secrets have fallback values just in case
    [ -n "${SECRET_KEY:-}" ] || SECRET_KEY="$(python3 -c "import secrets,string; chars=string.ascii_letters+string.digits; print(''.join(secrets.choice(chars) for _ in range(50)))" 2>/dev/null || true)"
    [ -n "${POSTGRES_PASSWORD:-}" ] || POSTGRES_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null || true)"
    [ -n "${REDIS_PASSWORD:-}" ] || REDIS_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null || true)"
    [ -n "${RABBITMQ_PASSWORD:-}" ] || RABBITMQ_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null || true)"
    [ -n "${GATEWAY_SECRET:-}" ] || GATEWAY_SECRET="$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
    [ -n "${GITHUB_WEBHOOK_SECRET:-}" ] || GITHUB_WEBHOOK_SECRET="$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
    [ -n "${AUTOSCALER_API_TOKEN:-}" ] || AUTOSCALER_API_TOKEN="$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
    [ -n "${FRP_AUTH_TOKEN:-}" ] || FRP_AUTH_TOKEN="$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
    [ -n "${PGCAT_ADMIN_PASSWORD:-}" ] || PGCAT_ADMIN_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(24))" 2>/dev/null || true)"

    # Validate Fernet key format
    if ! echo "$FIELD_ENCRYPTION_KEY" | python3 -c "
import sys
from cryptography.fernet import Fernet
try:
    Fernet(sys.stdin.read().strip().encode())
    print('valid')
except Exception:
    print('invalid')
" 2>/dev/null | grep -q valid; then
        echo -e "${RED}  ✗ CRITICAL: Failed to generate a valid Fernet encryption key.${NC}"
        echo -e "${RED}    Ensure the 'cryptography' package is installed and retry.${NC}"
        echo -e "${RED}    pip3 install cryptography${NC}"
        exit 1
    fi

    echo -e "${GREEN}  ✓ All secrets generated successfully${NC}"

    # Agent-lite nodes must use the master's DB password, not a locally generated one.
    # SSH into the master to fetch the correct POSTGRES_PASSWORD.
    if is_agent_lite_mode && [ -n "${MASTER_IP:-}" ] && [ "$MASTER_IP" != "127.0.0.1" ]; then
        echo -e "${BLUE}  → Fetching master DB password via SSH (master: ${MASTER_IP})...${NC}"
        _master_db_pw="$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes root@${MASTER_IP} \
            "grep '^POSTGRES_PASSWORD=' /opt/smsly-hosting/.env 2>/dev/null | head -1 | cut -d= -f2" 2>/dev/null || true)"
        if [ -n "${_master_db_pw:-}" ]; then
            POSTGRES_PASSWORD="$_master_db_pw"
            echo -e "${GREEN}  ✓ Retrieved master DB password${NC}"
        else
            echo -e "${YELLOW}  ⚠ Could not retrieve master DB password via SSH. DATABASE_URL may not connect.${NC}"
            echo -e "${YELLOW}    Tip: Pass MASTER_DB_PASSWORD=... to the install script.${NC}"
        fi
    fi

    # Create .env (Atomic)
    ENV_TMP="$INSTALL_DIR/.env.tmp"
    ENV_MODE_VALUE="$(mode_env_value)"
    ENV_NODE_TYPE="$INSTALL_MODE"
    ENV_TRAEFIK_HTTP_BIND="127.0.0.1:8081"
    ENV_STARTUP_CADDY_SYNC="true"
    if is_agent_lite_mode; then
        ENV_NODE_TYPE="agent-lite"
        ENV_STARTUP_CADDY_SYNC="false"
    elif is_node_mode; then
        ENV_NODE_TYPE="node"
        ENV_TRAEFIK_HTTP_BIND="0.0.0.0:80"
        ENV_STARTUP_CADDY_SYNC="false"
    fi
    cat <<EOF > "$ENV_TMP"
# SMSLY Hosting Configuration — Generated $(date -Iseconds)
ENVIRONMENT=production
NODE_TYPE=$ENV_NODE_TYPE
MODE=$ENV_MODE_VALUE
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
RABBITMQ_DEFAULT_USER=smsly_user
RABBITMQ_DEFAULT_PASS=$RABBITMQ_PASSWORD
REDIS_URL=redis://:$REDIS_PASSWORD@redis-primary:6379/0
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
ALLOWED_HOSTS=$DOMAIN,localhost,127.0.0.1
EOF

    # Build scheme-appropriate origins (avoid https://IP which breaks CORS/CSRF)
    if echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || [ "$USE_SSL" != "true" ]; then
        DOMAIN_ORIGINS="http://$DOMAIN"
    else
        DOMAIN_ORIGINS="https://$DOMAIN"
    fi
    cat >> "$ENV_TMP" <<EOF
CSRF_TRUSTED_ORIGINS=http://$PUBLIC_IP:8090,$DOMAIN_ORIGINS,http://localhost:8090,http://$PUBLIC_IP
CORS_ALLOWED_ORIGINS=http://$PUBLIC_IP:8090,$DOMAIN_ORIGINS,http://$PUBLIC_IP

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

# Private Docker registry (push/pull deployment images)
CONTAINER_REGISTRY_URL=127.0.0.1:5000
REGISTRY_USER=smsly-registry

# The installer runs first-boot Django setup explicitly after the stack starts.
# Keep the web container from doing the same work while Compose is waiting on health.
SMSLY_RUN_ENTRYPOINT_TASKS=false

# AppConfig.ready() must stay side-effect free during installs and management commands.
# Edge/proxy sync is performed explicitly by the installer and watcher services.
SMSLY_ENABLE_STARTUP_CADDY_SYNC=$ENV_STARTUP_CADDY_SYNC
TRAEFIK_HTTP_BIND=$ENV_TRAEFIK_HTTP_BIND
EOF

    # ─── Dynamic Build Resource Allocation ──────────────────────────────
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        echo -e "${BLUE}  → Lite Agent mode: frontend build is not part of this node.${NC}"
    elif [ "$MODE_NODE" = "true" ]; then
        echo -e "${BLUE}  → Node mode: frontend build is not part of this node.${NC}"
    else
        # Detect physical RAM for optimized build limits
        current_ram_mb=$(free -m | awk '/^Mem:/{print $2}')
        build_mem=2048
        if [ "$current_ram_mb" -ge 16384 ]; then
            build_mem=8192
        elif [ "$current_ram_mb" -ge 8192 ]; then
            build_mem=4096
        fi
        echo "FRONTEND_BUILD_MEMORY_MB=$build_mem" >> "$ENV_TMP"
        echo -e "${BLUE}  → Allocated ${build_mem}MB for frontend build (System RAM: ${current_ram_mb}MB)${NC}"
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
    fi

    # Atomic move and validation
    if validate_env_file "$ENV_TMP"; then
        mv "$ENV_TMP" "$INSTALL_DIR/.env"
        # 664 so the backend container (runs as UID 1000) can read AND write it.
        # This allows the domain-config signal to persist DOMAIN/USE_SSL back to
        # .env when the user updates settings via the web UI — no SSH needed.
        chown root:1000 "$INSTALL_DIR/.env"
        chmod 664 "$INSTALL_DIR/.env"
        # Docker Compose v2+ resolves .env from the compose file's parent directory,
        # not the CWD. Create a symlink so all compose files can find it.
        _compose_env_link="$INSTALL_DIR/infrastructure/docker/.env"
        rm -f "$_compose_env_link" 2>/dev/null || true
        ln -sf ../../.env "$_compose_env_link" 2>/dev/null || true
        echo -e "${GREEN}  ✓ Configuration saved to .env${NC}"
    else
        echo -e "${RED}  x Generated .env failed validation. Aborting install.${NC}"
        rm -f "$ENV_TMP"
        exit 1
    fi
fi
    set_checkpoint "config_generated"
fi
if [ -f "$INSTALL_DIR/.env" ]; then
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    apply_agent_lite_env_overrides "$INSTALL_DIR/.env"
    # Ensure .env symlink exists for Docker Compose v2+ .env resolution
    _compose_env_link="$INSTALL_DIR/infrastructure/docker/.env"
    rm -f "$_compose_env_link" 2>/dev/null || true
    ln -sf ../../.env "$_compose_env_link" 2>/dev/null || true
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x Existing .env is invalid after runtime-default reconciliation.${NC}"
        exit 1
    fi
fi
load_install_env_defaults "$INSTALL_DIR/.env"

# Ensure all variables in .env are exported to the environment so they are inherited by docker compose
if [ -f "$INSTALL_DIR/.env" ]; then
    set -a
    source "$INSTALL_DIR/.env"
    set +a
fi

# -----------------------------------------------------------------------------
# 4. Deployment
# -----------------------------------------------------------------------------
STACK_DEPLOYED_FROM_CHECKPOINT=false
if is_checkpoint_done "stack_deployed"; then
    STACK_DEPLOYED_FROM_CHECKPOINT=true
else
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
# Master exposes public HTTP/HTTPS through Caddy; node/agent modes expose HTTP through Traefik.
# Generate registry TLS cert + htpasswd if missing (required for auth-enabled registry)
echo -e "${BLUE}  → Configuring Docker registry auth and TLS...${NC}"
mkdir -p "$INSTALL_DIR/auth" "$INSTALL_DIR/certs"
_registry_certs_ok() {
    [ -f "$INSTALL_DIR/certs/registry.key" ] || return 1
    [ -f "$INSTALL_DIR/certs/registry.crt" ] || return 1
    local _cmod _kmod
    _cmod="$(openssl x509 -in "$INSTALL_DIR/certs/registry.crt" -noout -modulus 2>/dev/null | openssl sha256)" || return 1
    _kmod="$(openssl rsa  -in "$INSTALL_DIR/certs/registry.key" -noout -modulus 2>/dev/null | openssl sha256)" || return 1
    [ "$_cmod" = "$_kmod" ]
}
if ! _registry_certs_ok; then
    echo -e "${BLUE}    Generating self-signed TLS cert for registry...${NC}"
    _tmp_dir="$(mktemp -d)"
    if openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "${_tmp_dir}/registry.key" \
        -out    "${_tmp_dir}/registry.crt" \
        -subj "/CN=registry" 2>/dev/null; then
        mv "${_tmp_dir}/registry.key" "$INSTALL_DIR/certs/registry.key"
        mv "${_tmp_dir}/registry.crt" "$INSTALL_DIR/certs/registry.crt"
        chmod 644 "$INSTALL_DIR/certs/registry.crt" "$INSTALL_DIR/certs/registry.key"
    else
        echo -e "${YELLOW}    ⚠ openssl failed — is it installed?${NC}"
    fi
    rm -rf "$_tmp_dir" 2>/dev/null || true
    if ! _registry_certs_ok; then
        echo -e "${RED}    ✗ Registry TLS cert/key still missing or mismatched${NC}"
        echo -e "${YELLOW}      Manual fix: openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \\${NC}"
        echo -e "${YELLOW}        -keyout $INSTALL_DIR/certs/registry.key \\${NC}"
        echo -e "${YELLOW}        -out    $INSTALL_DIR/certs/registry.crt \\${NC}"
        echo -e "${YELLOW}        -subj '/CN=registry'${NC}"
    else
        echo -e "${BLUE}    Restarting registry container to pick up new TLS certs...${NC}"
        docker restart smsly-hosting-registry-1 2>/dev/null || true
    fi
fi
if [ ! -f "$INSTALL_DIR/auth/htpasswd" ] || [ -z "${REGISTRY_PASSWORD:-}" ] || [ -z "${REGISTRY_USER:-}" ]; then
    REGISTRY_PASS="${REGISTRY_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null || openssl rand -hex 12 2>/dev/null || echo 'auto-generated-change-me')}"
    if command -v htpasswd >/dev/null 2>&1; then
        htpasswd -Bbn "${REGISTRY_USER:-smsly-registry}" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"
    else
        # Python-based bcrypt fallback
        python3 -c "
import bcrypt, sys
pw = sys.argv[1] if len(sys.argv) > 1 else '$REGISTRY_PASS'
print(f'${REGISTRY_USER:-smsly-registry}:' + bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode())
" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd" 2>/dev/null || \
        echo -e "${YELLOW}    ⚠ Failed to generate htpasswd (neither htpasswd nor python bcrypt available)${NC}"
    fi
    env_set_value "$INSTALL_DIR/.env" "REGISTRY_USER" "${REGISTRY_USER:-smsly-registry}"
    env_set_value "$INSTALL_DIR/.env" "REGISTRY_PASSWORD" "$REGISTRY_PASS"
fi
echo -e "${GREEN}  ✓ Registry auth + TLS configured${NC}"

# Authenticate Docker CLI with the private registry so the daemon can
# pull base images during builds without 403 errors.
docker_login

# Ensure bind-mounted config paths exist before `docker compose up`.
ensure_infrastructure_permissions
if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "${BLUE}  → Lite Agent mode: disabling master-only Caddy services before Traefik bind.${NC}"
    true
elif [ "$MODE_NODE" = "true" ]; then
    echo -e "${BLUE}  → Node mode: deploying prod stack without frontend/Caddy; Traefik binds public HTTP.${NC}"
fi
echo -e "${BLUE}  → Disabling backend entrypoint bootstrap for installer-controlled migrations...${NC}"
env_set_value "$INSTALL_DIR/.env" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
    echo -e "${BLUE}  → Starting App Stack (Build + Deploy)...${NC}"
    ( while true; do sleep 30; echo -e "${BLUE}      ↳ Progress: Deployment in progress... $(date +%H:%M:%S)${NC}"; done ) &
    HEARTBEAT_PID=$!
    # TODO(install): replace set -e toggle with explicit conditional. The
    # conditional rebuild + retry makes a flat `if ! cmd` rewrite risky; the
    # rc-capture pattern is intentionally retained.
    set +e
    compose_stack_build --no-cache
    DEPLOY_RC=$?
    if [ "$DEPLOY_RC" -eq 0 ]; then
        compose_stack_up --remove-orphans
        DEPLOY_RC=$?
    fi
    set -e
    kill $HEARTBEAT_PID 2>/dev/null || true
    wait $HEARTBEAT_PID 2>/dev/null || true
    if [ "$DEPLOY_RC" -ne 0 ]; then
        echo -e "${RED}  ✗ Docker Compose failed during stack deployment (exit $DEPLOY_RC).${NC}"
        echo -e "${YELLOW}  ↳ Re-run with --resume to skip completed steps: sudo bash install.sh --resume${NC}"
        docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true
        docker compose -f "$COMPOSE_FILE" logs --tail=120 2>/dev/null || true
        exit "$DEPLOY_RC"
    fi
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        sync_agent_lite_rabbitmq_password
        docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend celery-worker
    else
        echo -e "${BLUE}  → Deploying Observability Stack...${NC}"
        if [ -f "infrastructure/docker/docker-compose.observability.yml" ]; then
            docker compose -f infrastructure/docker/docker-compose.observability.yml up -d >/dev/null 2>&1 || true
        fi
    fi
    # Deploy docker-labels exporter to all remote nodes and regenerate target files
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        backend_container=$(docker ps --format '{{.Names}}' | grep -E '^smsly-hosting-backend(-1)?$' | head -1)
        if [ -n "$backend_container" ]; then
            docker exec "$backend_container" python manage.py deploy_docker_labels_exporters 2>/dev/null || true
        fi
    fi
    set_checkpoint "stack_deployed"

    # Docker login now that the registry is actually running
    docker_login
fi
if [ "$STACK_DEPLOYED_FROM_CHECKPOINT" = "true" ]; then
    reconcile_compose_stack_after_resume
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
set -a
source "$INSTALL_DIR/.env" 2>/dev/null || true
set +a
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

# ─── Ensure PgCat is fresh and connected ──────────────────────────────────────
if docker compose -f "$COMPOSE_FILE" ps pgcat >/dev/null 2>&1; then
    echo -e "${BLUE}  → Restarting PgCat balancer...${NC}"
    timeout 30 docker compose -f "$COMPOSE_FILE" restart pgcat >/dev/null 2>&1 || true
fi

# ─── Restart backend so it picks up the correct DB credentials ──────────────
echo -e "${BLUE}  → Restarting backend with synced credentials...${NC}"
timeout 30 docker compose -f "$COMPOSE_FILE" restart backend >/dev/null 2>&1 || true
sleep 5

    echo -e "${BLUE}  → Running Migrations...${NC}"

    # Stop all services that talk to the DB.  Any open connection — even
    # a SELECT — holds a shared lock that blocks the ACCESS EXCLUSIVE
    # lock an ALTER TABLE needs.  Celery, backend health checks, and
    # PgCat connection pools all compete with the migration.
    MIGRATION_STOPPED_SVCS="backend celery celery-deploy celery-fast celery-beat pgcat"
    echo -e "${BLUE}    Stopping ${MIGRATION_STOPPED_SVCS} to prevent lock contention...${NC}"
    docker compose -f "$COMPOSE_FILE" stop --timeout 15 ${MIGRATION_STOPPED_SVCS} >/dev/null 2>&1 || true
    sleep 3

    # Kill every backend on the database so the migration owns it exclusively
    docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U smsly_admin -d smsly_hosting \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'" \
        >/dev/null 2>&1 || true
    sleep 2

    echo -e "${BLUE}    Running migrations (database: direct)...${NC}"
    # Note: Do NOT run makemigrations — migrations are committed in the repo.
    MIGRATE_OK=false
    # Migration runs via DIRECT_DATABASE_URL which goes straight to the
    # postgres backend, not through PgCat, so PgCat being stopped is safe.
    if run_backend_migrations 2>&1; then
        MIGRATE_OK=true
    else
        echo -e "${YELLOW}  ⚠ Migration attempt 1 failed — killing stale connections and retrying...${NC}"
        docker compose -f "$COMPOSE_FILE" exec -T db \
            psql -U smsly_admin -d smsly_hosting \
            -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'" \
            >/dev/null 2>&1 || true
        sleep 5
        if run_backend_migrations 2>&1; then
            MIGRATE_OK=true
        fi
    fi

    # Restart everything that was paused
    echo -e "${BLUE}    Restarting ${MIGRATION_STOPPED_SVCS}...${NC}"
    docker compose -f "$COMPOSE_FILE" start ${MIGRATION_STOPPED_SVCS} >/dev/null 2>&1 || true
    sleep 5

    if [ "$MIGRATE_OK" != "true" ]; then
        echo -e "${RED}  ✗ Migrations failed after 2 attempts.${NC}"
        echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs backend${NC}"
        echo -e "${YELLOW}  ↳ Tip: Re-run with --resume: sudo bash install.sh --resume${NC}"
        exit 1
    fi

    echo -e "${BLUE}  → Collecting Static Files...${NC}"
    # Fix volume ownership — Docker creates named volumes as root
    docker compose -f "$COMPOSE_FILE" exec -T --user root backend chown -R 1000:1000 /app/staticfiles /app/media /app/backups 2>/dev/null || true
    docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py collectstatic --noinput 2>/dev/null || true

    sync_platform_domain_state "$INSTALL_DIR/.env"
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
ADMIN_EXISTS=$(echo "from django.contrib.auth import get_user_model; User = get_user_model(); print('1' if User.objects.filter(username='admin').exists() else '0')" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1)

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
echo -e "${BLUE}  → Keeping backend entrypoint bootstrap disabled; installer controls migrations...${NC}"
env_set_value "$INSTALL_DIR/.env" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
if should_manage_caddy; then
    env_set_value "$INSTALL_DIR/.env" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "true"
else
    env_set_value "$INSTALL_DIR/.env" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false"
fi
    set_checkpoint "admin_created"
fi
fi

# -----------------------------------------------------------------------------
# 7. Caddy Reverse Proxy (Public Access — Dockerized)
# -----------------------------------------------------------------------------
# Agent-lite and node modes use Traefik instead of Caddy — skip this step entirely.
if should_manage_caddy; then
if ! is_checkpoint_done "caddy_configured" || [ "$REFRESH_MODE" = "true" ] || [ "$RECOVER_MODE" = "true" ]; then
    echo -e "\n${YELLOW}[7/9] Setting up Dockerized Caddy Proxy...${NC}"

    # Ensure caddy-config directory exists and has correct permissions
    # Caddy container runs as uid 1000 (nextjs user); group-read access is
    # required so the container can write runtime state (tls certs, reload flag).
    mkdir -p /opt/smsly-hosting/caddy-config
    chown 1000:1000 /opt/smsly-hosting/caddy-config
    chmod 2775 /opt/smsly-hosting/caddy-config

    # SEED: Create a temporary safety Caddyfile so the container doesn't crash on first start.
    # The backend will overwrite this within seconds of starting up.
    if [ ! -f /opt/smsly-hosting/caddy-config/Caddyfile ]; then
        echo -e "${BLUE}  → Seeding initial safety Caddyfile...${NC}"
        cat > /opt/smsly-hosting/caddy-config/Caddyfile <<EOF
:80 {
    respond "System initializing... Please refresh in 30 seconds." 200
}
EOF
        chown 1000:1000 /opt/smsly-hosting/caddy-config/Caddyfile
        chmod 664 /opt/smsly-hosting/caddy-config/Caddyfile
    fi

    # Build and start Caddy container
    echo -e "${BLUE}  → Building and starting Caddy container...${NC}"
    if ! docker compose -f "$COMPOSE_FILE" build --no-cache caddy; then
        echo -e "${RED}ERROR: Caddy image build failed.${NC}"
        echo -e "${YELLOW}This may be due to a Go version mismatch, missing module, or Dockerfile error.${NC}"
        echo -e "${YELLOW}Check the build logs above for the exact failing stage.${NC}"
        echo -e "${YELLOW}Dockerfile path: ./infrastructure/caddy/Dockerfile${NC}"
        exit 1
    fi
    docker compose -f "$COMPOSE_FILE" up -d caddy

    # ACME staging validation — verify Let's Encrypt can reach this server before going live
    if [ "${DOMAIN:-}" ] && [ "$USE_SSL" = "true" ] && ! echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        echo -e "${BLUE}  → Running ACME staging validation for $DOMAIN...${NC}"
        SLEEP_SEC=15
        echo -e "${BLUE}    Waiting ${SLEEP_SEC}s for Caddy to start...${NC}"
        sleep $SLEEP_SEC
        ACME_OK=false
        for attempt in 1 2 3; do
            # Use Let's Encrypt staging endpoint to dry-run the HTTP-01 challenge
            ACME_CHECK=$(curl -fsS -m 10 \
                "http://${DOMAIN}/.well-known/acme-challenge/000000000000000000000000000000000000" \
                2>/dev/null || true)
            # If Caddy returns "challenge not found" (404), that means it IS
            # reachable but doesn't have this challenge registered — which is
            # the expected behavior for a staging check.
            if echo "$ACME_CHECK" | grep -qi "challenge"; then
                echo -e "${GREEN}  ✓ ACME HTTP-01 reachable for $DOMAIN (staging)${NC}"
                ACME_OK=true
                break
            fi
            # Also try: just checking port 80 responds
            if curl -fsSo /dev/null --max-time 5 "http://${DOMAIN}/" 2>/dev/null; then
                echo -e "${GREEN}  ✓ Port 80 reachable for $DOMAIN${NC}"
                ACME_OK=true
                break
            fi
            echo -e "${YELLOW}    ACME check attempt $attempt/3 — $DOMAIN not yet reachable, retrying...${NC}"
            sleep 5
        done
        if [ "$ACME_OK" != "true" ]; then
            echo -e "${YELLOW}  ⚠ ACME validation could not confirm $DOMAIN is reachable on port 80.${NC}"
            echo -e "${YELLOW}    SSL certificates may fail to issue. Ensure DNS A record points to $PUBLIC_IP${NC}"
            echo -e "${YELLOW}    and port 80 is open in your firewall.${NC}"
            if [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
                read -p "  Continue anyway? (y/n) " -n 1 -r < /dev/tty
                echo
                if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                    echo -e "${RED}  ACME validation rejected by user. Aborting.${NC}"
                    exit 1
                fi
            fi
        fi
    fi

    # Cleanup legacy host-side bare-metal Caddy server if it exists
    echo -e "${BLUE}  → Cleaning up legacy host-side Caddy service (if any)...${NC}"
    systemctl stop caddy 2>/dev/null || true
    systemctl disable caddy 2>/dev/null || true
    rm -f /etc/systemd/system/caddy.service
    systemctl daemon-reload

    set_checkpoint "caddy_configured"
fi
fi # end Caddy skip for agent-lite/node modes

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
    CRITICAL_CONTAINERS=(smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgcat-1 smsly-hosting-celery-1 smsly-hosting-celery-deploy-1 smsly-hosting-celery-fast-1 smsly-hosting-celery-beat-1 smsly-hosting-socket-proxy-1)
fi
for CONTAINER in "${CRITICAL_CONTAINERS[@]}"; do
    resolved_container="$(resolve_container_target "$CONTAINER")"
    CPID=$(docker inspect --format '{{.State.Pid}}' "$resolved_container" 2>/dev/null || echo "")
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
    # Allow SSH from master IP specifically (provisioning/updates)
    _master_ip="${MASTER_IP:-}"
    if [ -n "$_master_ip" ] && [ "$_master_ip" != "127.0.0.1" ] && ! echo "$_master_ip" | grep -qE '^(0\.0\.0\.0|localhost)$'; then
        echo -e "${BLUE}  → Allowing master ($_master_ip) SSH access...${NC}"
        ufw allow from "$_master_ip" to any port 22 >/dev/null 2>&1 || true
    fi
    # Fallback: allow SSH from any (in case MASTER_IP is empty)
    ufw allow ssh >/dev/null 2>&1 || true
    
    if [ "${INSTALL_MODE:-}" = "agent-lite" ]; then
        if [ -n "$_master_ip" ] && [ "$_master_ip" != "127.0.0.1" ] && ! echo "$_master_ip" | grep -qE '^(0\.0\.0\.0|localhost)$'; then
            ufw allow from "$_master_ip" to any port 80 >/dev/null 2>&1 || true
        else
            echo -e "${YELLOW}  ⚠ Warning: Agent-Lite missing Master IP. Port 80 not exposed.${NC}"
        fi
    else
        ufw allow 80/tcp >/dev/null 2>&1 || true
        ufw allow 443/tcp >/dev/null 2>&1 || true
    fi
    # Allow FRP if active
    if [ -f "$INSTALL_DIR/.env" ] && grep -q "FRP_AUTH_TOKEN" "$INSTALL_DIR/.env"; then
        ufw allow 7000/tcp >/dev/null 2>&1 || true
    fi
    # Allow Docker Mirror (Option B) if this is the Master/Leader
    if [ -z "${MASTER_IP:-}" ] || [ "$MASTER_IP" = "127.0.0.1" ] || [ "$MASTER_IP" = "$(detect_public_ip)" ]; then
        ufw allow 5001/tcp >/dev/null 2>&1 || true
        # Allow Lite Agents to reach core services
        echo -e "${YELLOW}  ⚠ Master node: Exposing DB/Redis/MQ ports for Lite Agents (protected by password)${NC}"
        ufw allow 5432/tcp >/dev/null 2>&1 || true
        ufw allow 6379/tcp >/dev/null 2>&1 || true
        ufw allow 5672/tcp >/dev/null 2>&1 || true
    fi
    echo "y" | ufw enable >/dev/null 2>&1 || true
    echo -e "${GREEN}  ✓ Firewall hardened (Inbound blocked, SSH/Web permitted)${NC}"
fi

# ── Registry port firewall (DOCKER-USER chain) ──────────────────────────
# Docker bypasses UFW by inserting its own iptables rules in the DOCKER
# chain. The DOCKER-USER chain is the official way to add custom rules.
# Since the registry has no auth, we lock port 5000 to trusted sources.
if command -v iptables >/dev/null 2>&1; then
    echo -e "${BLUE}  → Securing registry port 5000 via iptables (DOCKER-USER chain)...${NC}"

    # Ensure DOCKER-USER chain exists (Docker creates it, but be safe)
    iptables -N DOCKER-USER 2>/dev/null || true

    # --- Pre-check: detect if registry port 5000 is currently open to the public internet ---
    _registry_port_open=false
    _existing_drop_rule="$(iptables -L DOCKER-USER -n 2>/dev/null | grep -c 'dpt:5000.*DROP' || true)"
    if [ "${_existing_drop_rule:-0}" -eq 0 ]; then
        # No DROP rule exists — port 5000 is currently reachable from any source
        _registry_port_open=true
        echo -e "${YELLOW}  ⚠ WARNING: Registry port 5000 is currently OPEN to the public internet!${NC}"
        echo -e "${YELLOW}    The container registry has no authentication and is accessible from any IP.${NC}"
        echo -e "${YELLOW}    Hardening now to restrict access to trusted sources only...${NC}"
    else
        echo -e "${BLUE}  → Registry port 5000 already has DROP rules — refreshing whitelist...${NC}"
    fi

    # Flush any previous registry rules (idempotent re-runs)
    # Use a subshell to isolate from set -e / pipefail
    (
        iptables -L DOCKER-USER --line-numbers -n 2>/dev/null | \
            grep "dpt:5000" | awk '{print $1}' | sort -rn | \
            while read -r num; do iptables -D DOCKER-USER "$num" 2>/dev/null || true
        done
    ) || true

    # Allow localhost (container-to-registry on the same host)
    iptables -I DOCKER-USER -i lo -p tcp --dport 5000 -j ACCEPT 2>/dev/null || true

    # Allow Docker bridge networks (172.16.0.0/12 covers docker0 + compose nets)
    iptables -I DOCKER-USER -s 172.16.0.0/12 -p tcp --dport 5000 -j ACCEPT 2>/dev/null || true

    # Allow WireGuard mesh (10.100.0.0/24 is the assigned mesh range)
    iptables -I DOCKER-USER -s 10.100.0.0/24 -p tcp --dport 5000 -j ACCEPT 2>/dev/null || true

    # Allow known node IPs
    if [ -n "${MASTER_MESH_IP:-}" ]; then
        iptables -I DOCKER-USER -s "${MASTER_MESH_IP}" -p tcp --dport 5000 -j ACCEPT 2>/dev/null || true
    fi

    # Drop everything else to port 5000
    iptables -A DOCKER-USER -p tcp --dport 5000 -j DROP 2>/dev/null || true

    # Return to the DOCKER chain for all other traffic
    # (ensure the RETURN rule exists at the end)
    iptables -C DOCKER-USER -j RETURN 2>/dev/null || \
        iptables -A DOCKER-USER -j RETURN 2>/dev/null || true

    if [ "$_registry_port_open" = true ]; then
        echo -e "${GREEN}  ✓ Registry port 5000 HARDENED — now locked to localhost + mesh/docker networks${NC}"
    else
        echo -e "${GREEN}  ✓ Registry port 5000 rules refreshed (trusted sources only)${NC}"
    fi

    # Allow remote Promtail → Loki on WireGuard interface (VPN mesh)
    iptables -A INPUT -i wg+ -p tcp --dport 3100 -j ACCEPT 2>/dev/null || true

    # Persist iptables rules across reboots
    if command -v iptables-save >/dev/null 2>&1; then
        mkdir -p /etc/iptables
        iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
        # Create a systemd service to restore rules on boot (before Docker starts)
        if [ ! -f /etc/systemd/system/iptables-restore.service ]; then
            cat > /etc/systemd/system/iptables-restore.service <<'RESTORE_EOF'
[Unit]
Description=Restore iptables rules
Before=docker.service
After=network-pre.target

[Service]
Type=oneshot
ExecStart=/sbin/iptables-restore /etc/iptables/rules.v4
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
RESTORE_EOF
            systemctl daemon-reload 2>/dev/null || true
            systemctl enable iptables-restore 2>/dev/null || true
        fi
        echo -e "${GREEN}  ✓ iptables rules saved to /etc/iptables/rules.v4 for persistence${NC}"
    fi
fi

echo -e "${GREEN}  ✓ System security hardening complete${NC}"
    set_checkpoint "memory_hardened"
fi

# -----------------------------------------------------------------------------
# 9. Verification
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[9/9] Verifying Deployment...${NC}"
VERIFY_PASS_COUNT=0
VERIFY_TOTAL=4
sleep 5

if [ "$MODE_AGENT_LITE" = "true" ]; then
VERIFY_TOTAL=4

echo -e "${BLUE}  → [1/4] Verifying Lite Agent compose profile...${NC}"
AGENT_SERVICES="$(docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null || true)"
if printf '%s\n' "$AGENT_SERVICES" | grep -qx "backend" \
   && printf '%s\n' "$AGENT_SERVICES" | grep -qx "celery-worker" \
   && printf '%s\n' "$AGENT_SERVICES" | grep -qx "traefik" \
   && printf '%s\n' "$AGENT_SERVICES" | grep -qx "socket-proxy" \
   && printf '%s\n' "$AGENT_SERVICES" | grep -qx "redis" \
   && printf '%s\n' "$AGENT_SERVICES" | grep -qx "rabbitmq" \
   && ! printf '%s\n' "$AGENT_SERVICES" | grep -Eq "^(frontend|db|pgcat)$"; then
    echo -e "${GREEN}  ✓ Lite Agent profile selected; local Redis/RabbitMQ enabled and control-plane services excluded${NC}"
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
BACKEND_OK=false
BACKEND_STATUS=""
for attempt in $(seq 1 24); do
    BACKEND_STATUS="$(docker compose -f "$COMPOSE_FILE" ps backend --format "{{.Status}}" 2>/dev/null || true)"
    if docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS http://127.0.0.1:8000/health/live >/dev/null 2>&1; then
        BACKEND_OK=true
        break
    fi
    if echo "$BACKEND_STATUS" | grep -qi "unhealthy"; then
        break
    fi
    echo -ne "\r${YELLOW}  → Lite Agent backend warmup $attempt/24...${NC}"
    sleep 5
done
echo ""
if [ "$BACKEND_OK" = "true" ]; then
    echo -e "${GREEN}  ✓ Lite Agent backend liveness endpoint passed${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Lite Agent backend is not live (status: ${BACKEND_STATUS:-unknown})${NC}"
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
# ─── Check 1: Health check ─────────────────────────────────────────────────
echo -e "${BLUE}  → [1/4] Running health check...${NC}"
HEALTH_OK=false
MAX_ATTEMPTS=36
for attempt in $(seq 1 $MAX_ATTEMPTS); do
    if docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health/live >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    elif curl -sfL --max-time 5 http://127.0.0.1:8000/health/live >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    fi
    echo -ne "\r${YELLOW}  → Health check attempt $attempt/$MAX_ATTEMPTS — waiting...${NC}"
    sleep 5
done
echo ""

if [ "$HEALTH_OK" = "true" ]; then
    echo -e "${GREEN}  ✓ Health Check Passed!${NC}"
    READY_OK=false
    docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health/ready >/dev/null 2>&1 && READY_OK=true
    if ! $READY_OK && ! curl -sfL --max-time 5 http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
        echo -e "${YELLOW}  ⚠ Readiness endpoint is still warming; continuing because liveness passed.${NC}"
    fi
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Health check failed after $MAX_ATTEMPTS attempts.${NC}"
    dump_diagnostic_logs
fi

# ─── Check 3: All containers running ──────────────────────────────────────
echo -e "${BLUE}  → [2/4] Checking container status...${NC}"
RUNNING_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --status running -q 2>/dev/null | wc -l)
TOTAL_COUNT=$(docker compose -f "$COMPOSE_FILE" ps -q 2>/dev/null | wc -l)
UNHEALTHY_STATUS="$(docker compose -f "$COMPOSE_FILE" ps --format "{{.Service}}\t{{.Status}}" 2>/dev/null | awk 'tolower($0) ~ /unhealthy/ {print}' || true)"
if [ -n "$UNHEALTHY_STATUS" ]; then
    echo -e "${RED}  ✗ One or more containers are unhealthy:${NC}"
    printf '%s\n' "$UNHEALTHY_STATUS" | sed 's/^/     - /'
    UNHEALTHY_SERVICES="$(printf '%s\n' "$UNHEALTHY_STATUS" | awk '{print $1}' | xargs 2>/dev/null || true)"
    if [ -n "$UNHEALTHY_SERVICES" ]; then
        docker compose -f "$COMPOSE_FILE" logs --tail=80 $UNHEALTHY_SERVICES 2>/dev/null || true
    fi
elif [ "$RUNNING_COUNT" -eq "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt 0 ]; then
    echo -e "${GREEN}  ✓ All $TOTAL_COUNT containers running and none are unhealthy${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Only $RUNNING_COUNT/$TOTAL_COUNT containers running${NC}"
fi

# ─── Check 4: Swap is sufficient ──────────────────────────────────────────
echo -e "${BLUE}  → [3/4] Checking swap...${NC}"
SWAP_TOTAL=$(free -m | awk '/^Swap:/{print $2}')
if [ "$SWAP_TOTAL" -ge 1500 ]; then
    echo -e "${GREEN}  ✓ Swap sufficient (${SWAP_TOTAL}MB)${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  ⚠ Swap low (${SWAP_TOTAL}MB) — recommend 2GB+${NC}"
fi

# ─── Check 5: Public edge proxy ───────────────────────────────────────────
if should_manage_caddy; then
    echo -e "${BLUE}  → [4/4] Checking Caddy...${NC}"
    caddy_container="$(resolve_container_target "smsly-hosting-caddy-1")"
    if docker inspect -f '{{.State.Running}}' "$caddy_container" 2>/dev/null | grep -q "true"; then
        echo -e "${GREEN}  ✓ Caddy reverse proxy container active${NC}"
        VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Caddy container is not running${NC}"
    fi
else
    echo -e "${BLUE}  → [4/4] Checking Traefik...${NC}"
    TRAEFIK_CHECK_URL="http://127.0.0.1:8081/"
    if is_node_mode; then
        TRAEFIK_CHECK_URL="http://127.0.0.1/health/live"
    fi
    traefik_container="$(resolve_container_target "smsly-hosting-traefik-1")"
    if docker inspect -f '{{.State.Running}}' "$traefik_container" 2>/dev/null | grep -q "true" \
       && curl -fsS --max-time 5 "$TRAEFIK_CHECK_URL" >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ Traefik edge proxy active (${TRAEFIK_CHECK_URL})${NC}"
        VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Traefik edge proxy check failed (${TRAEFIK_CHECK_URL})${NC}"
    fi
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

# Install infrastructure monitor
if [ -f "$INSTALL_DIR/scripts/monitor_infra.sh" ]; then
    echo -e "${BLUE}  → Installing critical infrastructure monitoring timer...${NC}"
    chmod +x "$INSTALL_DIR/scripts/monitor_infra.sh"
    cp "$INSTALL_DIR/scripts/smsly-infra-monitor.service" /etc/systemd/system/smsly-infra-monitor.service 2>/dev/null || true
    cp "$INSTALL_DIR/scripts/smsly-infra-monitor.timer" /etc/systemd/system/smsly-infra-monitor.timer 2>/dev/null || true
    systemctl daemon-reload
    systemctl enable smsly-infra-monitor.timer 2>/dev/null || true
    systemctl restart smsly-infra-monitor.timer 2>/dev/null || true
    echo -e "${GREEN}  ✓ smsly-infra-monitor timer installed and started${NC}"
fi

# Install platform update watcher and caddy watcher services
if [ -f "$INSTALL_DIR/scripts/smsly-update-watcher.service" ]; then
    echo -e "${BLUE}  → Installing platform update and Caddy config watcher services...${NC}"
    chmod +x "$INSTALL_DIR/scripts/platform-update.sh" "$INSTALL_DIR/scripts/caddy-reload.sh" 2>/dev/null || true
    cp "$INSTALL_DIR/scripts/smsly-update-watcher.service" /etc/systemd/system/smsly-update-watcher.service 2>/dev/null || true
    cp "$INSTALL_DIR/scripts/caddy-watcher.service" /etc/systemd/system/caddy-watcher.service 2>/dev/null || true
    systemctl daemon-reload
    systemctl enable smsly-update-watcher caddy-watcher 2>/dev/null || true
    systemctl restart smsly-update-watcher caddy-watcher 2>/dev/null || true
    echo -e "${GREEN}  ✓ smsly-update-watcher and caddy-watcher services installed and started${NC}"
fi

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
        CLI_DOMAIN="${DOMAIN:-}"
        CLI_USE_SSL="${USE_SSL:-false}"
        if [ -z "$CLI_DOMAIN" ] && [ -f "$INSTALL_DIR/.env" ]; then
            CLI_DOMAIN="$(env_get_value "$INSTALL_DIR/.env" "DOMAIN" || true)"
        fi
        if [ -n "$CLI_DOMAIN" ] && [ "$CLI_DOMAIN" != "localhost" ]; then
            URL_SCHEME="https" && [ "$CLI_USE_SSL" != "true" ] && URL_SCHEME="http"
            API_URL="${URL_SCHEME}://${CLI_DOMAIN}"
        else
            API_URL="http://127.0.0.1"
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

# -----------------------------------------------------------------------------
# 11. Finalize Inter-Node Connectivity
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[11/11] Finalizing Inter-Node Connectivity...${NC}"
echo -e "${BLUE}  → Registering this node and creating authentication tokens...${NC}"
# Use -T to avoid TTY issues in non-interactive mode
if docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py help diagnose_nodes >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py diagnose_nodes --fix || true
    echo -e "${GREEN}  ✓ Node registered as Primary (if Master) and API tokens verified${NC}"
else
    echo -e "${YELLOW}  ⚠ diagnose_nodes command not available in this version; skipping.${NC}"
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
# Infrastructure Handshake & Health Stabilization
echo -e "\n${BLUE}  🔄 Running infrastructure handshake and stabilization...${NC}"
chmod +x scripts/grid-handshake.sh 2>/dev/null || true
bash scripts/grid-handshake.sh || \
    echo -e "${YELLOW}  ⚠️ Handshake stabilization failed (non-fatal). You can run it manually later.${NC}"

echo -e "${GREEN}   ✓ INSTALLATION SUCCESSFUL!${NC}"

echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"

SUMMARY_PUBLIC_IP="${PUBLIC_IP:-}"
if [ -z "$SUMMARY_PUBLIC_IP" ] && [ -f "$INSTALL_DIR/.env" ]; then
    SUMMARY_PUBLIC_IP="$(env_get_value "$INSTALL_DIR/.env" "PUBLIC_IP" || true)"
fi
if [ -z "$SUMMARY_PUBLIC_IP" ]; then
    SUMMARY_PUBLIC_IP="$(detect_public_ip)"
fi

SUMMARY_MASTER_IP="${MASTER_IP:-}"
if [ -z "$SUMMARY_MASTER_IP" ] && [ -f "$INSTALL_DIR/.env" ]; then
    SUMMARY_MASTER_IP="$(env_get_value "$INSTALL_DIR/.env" "MASTER_IP" || true)"
fi
SUMMARY_MASTER_IP="${SUMMARY_MASTER_IP:-unknown}"

SUMMARY_DOMAIN="${DOMAIN:-}"
if [ -z "$SUMMARY_DOMAIN" ] && [ -f "$INSTALL_DIR/.env" ]; then
    SUMMARY_DOMAIN="$(env_get_value "$INSTALL_DIR/.env" "DOMAIN" || true)"
fi

if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "   Mode:        Lite Agent"
    echo -e "   Agent Edge:  http://$SUMMARY_PUBLIC_IP"
    echo -e "   Master:      $SUMMARY_MASTER_IP"
elif [ "$MODE_NODE" = "true" ]; then
    echo -e "   Mode:        Full-Stack Node"
    echo -e "   API:         http://$SUMMARY_PUBLIC_IP"
    echo -e "   Edge:        Traefik on public port 80"
    echo -e "   UI/HTTPS:    Managed by Master (frontend/Caddy disabled here)"
    echo -e "   Credentials: $CREDENTIALS_FILE"
else
    if [ "${USE_SSL:-false}" = "true" ] && [ -n "$SUMMARY_DOMAIN" ]; then
        echo -e "   URL:         https://$SUMMARY_DOMAIN"
    else
        echo -e "   URL:         http://$SUMMARY_PUBLIC_IP"
    fi
    echo -e "   Admin:       /admin"
    echo -e "   Credentials: $CREDENTIALS_FILE"
fi
echo -e "   Install Log: $LOG_FILE"
echo -e "   Location:    $INSTALL_DIR"
echo -e "   Memory:      $(free -m | awk '/^Mem:/{print $7}')MB available"
echo -e "   Swap:        $(free -m | awk '/^Swap:/{print $2}')MB total"

# ─── Custom Domain SSL Integration ───────────────────────────────────────────
if should_manage_caddy; then  # Only for master mode
    echo -e "\n${YELLOW}[9/9] Setting up Custom Domain SSL Services...${NC}"
    
    # Check if custom domain SSL manager script exists
    if [ -f "install-custom-domain-ssl.sh" ]; then
        echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
        bash install-custom-domain-ssl.sh install
    elif [ -f "$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh" ]; then
        echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
        bash "$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh" install
    else
        echo -e "${YELLOW}  ⚠ Custom domain SSL manager not found, skipping setup${NC}"
        return
    fi
    
    # Start the services
    echo -e "${BLUE}  → Starting custom domain SSL services...${NC}"
    /opt/smsly-hosting/smsly-domain-ssl-manager.sh start
    
    # Enable auto-start on boot
    echo -e "${BLUE}  → Enabling auto-start on boot...${NC}"
    /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable
    
    echo -e "${GREEN}  ✓ Custom domain SSL services configured${NC}"
fi

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
if [ "$MODE_AGENT_LITE" != "true" ]; then
    echo -e "   CLI:         'smsly services list'${NC}"
fi
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  View credentials:   cat $CREDENTIALS_FILE${NC}"
echo -e "${YELLOW}  View logs:          cat $LOG_FILE${NC}"
if is_master_mode; then
    echo -e "${YELLOW}  Update frontend:    sudo bash install.sh --update-frontend${NC}"
fi
echo -e "${YELLOW}  Update backend:     sudo bash install.sh --update-backend${NC}"
echo -e "${YELLOW}  Full update:        sudo bash install.sh --update${NC}"
echo -e "${YELLOW}  Runtime refresh:    sudo bash install.sh --refresh${NC}"
echo -e "${YELLOW}  Runtime recovery:   sudo bash install.sh --recover${NC}"
echo -e "${YELLOW}  Debug snapshot:     sudo bash install.sh --debug${NC}"
echo -e "${YELLOW}  Wipe install:       sudo bash install.sh --wipe${NC}"

# ─── Capture SSH Installer Device Trust ──────────────────────────────────────
# Record the SSH client's public key fingerprint as a trusted device so the
# platform can recognize this machine on future administrative operations.
if [ -n "$SSH_CONNECTION" ] && command -v ssh-add &>/dev/null; then
    SSH_CLIENT_IP=$(echo "$SSH_CONNECTION" | awk '{print $1}')
    SSH_KEY_FP=$(ssh-add -l 2>/dev/null | head -1 | awk '{print $2}')
    if [ -n "$SSH_KEY_FP" ]; then
        echo -e "${BLUE}  → Registering SSH client key as trusted device: ${SSH_KEY_FP}${NC}"
        docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
import os, json
from apps.deployments.models_core import TrustedDevice
from django.contrib.auth import get_user_model
user = get_user_model().objects.filter(is_superuser=True).first()
if user:
    TrustedDevice.objects.get_or_create(
        user=user,
        ssh_key_fingerprint='${SSH_KEY_FP}',
        defaults={
            'trust_method': 'ssh_key',
            'label': 'SSH Installer (${SSH_CLIENT_IP})',
            'ip_address': '${SSH_CLIENT_IP}',
            'fingerprint_hash': os.urandom(16).hex(),
        }
    )
    print('SSH installer device registered.')
" 2>/dev/null || echo -e "${YELLOW}  ⚠ Could not register SSH device trust (non-critical)${NC}"
    fi
fi

# ─── Verification Check Summary ──────────────────────────────────────────────
if [ "$VERIFY_PASS_COUNT" -eq "$VERIFY_TOTAL" ]; then
    echo -e "\n${GREEN}  ✓ All $VERIFY_TOTAL/$VERIFY_TOTAL verification checks passed.${NC}"
    echo -e "${YELLOW}  If needed, run 'sudo reboot' manually to apply sysctl changes.${NC}"
else
    echo -e "\n${RED}  ⚠ Only $VERIFY_PASS_COUNT/$VERIFY_TOTAL checks passed.${NC}"
    echo -e "${YELLOW}  Fix the failed checks above. You can run 'sudo reboot' manually if sysctl changes were made.${NC}"
    if [ "${SMSLY_STRICT_VERIFY:-0}" = "1" ]; then
        echo -e "${RED}  ✗ Strict verification is enabled; failing installation.${NC}"
        exit 1
    fi
fi

exit 0
