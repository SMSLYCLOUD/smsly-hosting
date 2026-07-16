#!/bin/bash
# =============================================================================
# install-media-node.sh — SMSLY Media Node Standalone Installer
# VERSION: 2026-07-11-0001
# =============================================================================
# Provisions a bare Ubuntu 22.04/24.04 LTS machine as a SMSLY media node.
# Usage:
#   sudo bash install-media-node.sh
#   sudo bash install-media-node.sh --update
#   sudo bash install-media-node.sh --debug
# =============================================================================

set -euo pipefail

export PATH="/usr/local/bin:$PATH"

# ─── Root Check ──────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo -e "\033[0;31mERROR: This script must be run as root.\033[0m"
    echo -e "Please use: sudo bash $0 $*"
    exit 1
fi

# ─── Resolve script path ─────────────────────────────────────────────────────
SCRIPT_PATH="$(readlink -f "$0" || echo "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

# ─── Bootstrap lib/ if running from curl (co-locate lib/media-node.sh) ────────
if [ ! -f "$SCRIPT_DIR/lib/media-node.sh" ] && [ -d "/opt/smsly-hosting" ]; then
    SCRIPT_DIR="/opt/smsly-hosting"
fi

# ─── Colors ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"
export NEEDRESTART_MODE="${NEEDRESTART_MODE:-a}"

# ─── Flag parsing ────────────────────────────────────────────────────────────
UPDATE_MODE=false
DEBUG_MODE=false
RESUME_MODE=false

for arg in "$@"; do
    case "$arg" in
        --update)    UPDATE_MODE=true ;;
        --debug)     DEBUG_MODE=true ;;
        --resume)    RESUME_MODE=true ;;
        --help|-h)
            echo "Usage: sudo bash install-media-node.sh [OPTIONS]"
            echo ""
            echo "  (no args)     Fresh install"
            echo "  --update      Pull latest code, rebuild smsly-media-mgmt, restart services"
            echo "  --debug       Show system status and exit"
            echo "  --resume      Skip already-completed steps"
            exit 0
            ;;
    esac
done

# ─── Source shared helpers ────────────────────────────────────────────────────
LIB_DIR="$SCRIPT_DIR/lib"
for lib in "$LIB_DIR"/*.sh; do
    [ -f "$lib" ] && source "$lib"
done

# ─── Source media-node functions ──────────────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/lib/media-node.sh" ]; then
    echo -e "${RED}ERROR: lib/media-node.sh not found at $SCRIPT_DIR/lib/media-node.sh${NC}"
    echo -e "       Make sure install-media-node.sh is in the same directory as lib/"
    exit 1
fi
source "$SCRIPT_DIR/lib/media-node.sh"

# ─── Runtime constants ────────────────────────────────────────────────────────
LOG_FILE="/var/log/smsly-media-install.log"
INSTALL_DIR="/opt/smsly-hosting-media"
ENV_FILE="$INSTALL_DIR/.env"
LOCK_FILE="/tmp/smsly-media-install.lock"

# ─── Acquire lock ────────────────────────────────────────────────────────────
if [ -f "$LOCK_FILE" ]; then
    local pid
    pid="$(cat "$LOCK_FILE" || true)"
    if [ -n "$pid" ] && kill -0 "$pid"; then
        echo -e "${RED}ERROR: Another instance (PID $pid) is already running.${NC}"
        exit 1
    fi
fi
echo "$$" > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# ─── Log setup ────────────────────────────────────────────────────────────────
exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  SMSLY Media Node Install Log — $(date -Iseconds)"
echo "  Mode: $( [ "$UPDATE_MODE" = "true" ] && echo "update" || echo "fresh" )"
echo "═══════════════════════════════════════════════════════════"

# ─── Debug Mode ───────────────────────────────────────────────────────────────
if [ "$DEBUG_MODE" = "true" ]; then
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  SMSLY Media Node — Debug${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

    echo -e "${BLUE}  → System info:${NC}"
    echo "    Host:     $(hostname -f || hostname)"
    echo "    Kernel:   $(uname -r)"
    echo "    CPU:      $(nproc) cores"
    echo "    RAM:      $(awk '/MemTotal/{printf "%.0f MB", $2/1024}' /proc/meminfo)"
    echo "    Disk:     $(df -h / | awk 'NR==2{print $2 " total, " $4 " free"}')"
    echo "    Public IP: $(detect_public_ip || echo 'unknown')"

    echo -e "\n${BLUE}  → Service status:${NC}"
    for svc in postgresql redis-server wireguard kamailio freeswitch rtpengine coturn livekit-server smsly-voice-api smsly-video smsly-media-mgmt openresty; do
        if systemctl is-active "$svc"; then
            echo -e "    ${GREEN}✓${NC} $svc"
        elif systemctl is-enabled "$svc"; then
            echo -e "    ${YELLOW}○${NC} $svc (enabled, not running)"
        else
            echo -e "    ${RED}✗${NC} $svc"
        fi
    done

    echo -e "\n${BLUE}  → Media node config:${NC}"
    if [ -f "$ENV_FILE" ]; then
        echo "    NODE_ID:    $(grep -m1 '^NODE_ID=' "$ENV_FILE" | cut -d= -f2-)"
        echo "    NODE_TYPE:  $(grep -m1 '^NODE_TYPE=' "$ENV_FILE" | cut -d= -f2-)"
        echo "    PUBLIC_IP:  $(grep -m1 '^PUBLIC_IP=' "$ENV_FILE" | cut -d= -f2-)"
        echo "    MASTER_URL: $(grep -m1 '^MASTER_API_URL=' "$ENV_FILE" | cut -d= -f2-)"
    else
        echo "    (no config found)"
    fi

    echo -e "\n${BLUE}  → Listening ports:${NC}"
    ss -tlnp | head -30 || netstat -tlnp | head -30 || echo "    (ss/netstat unavailable)"

    echo -e "\n${BLUE}  → Log: ${LOG_FILE}${NC}"
    exit 0
fi

# ─── Update Mode ──────────────────────────────────────────────────────────────
if [ "$UPDATE_MODE" = "true" ]; then
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  SMSLY Media Node — Update${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

    if [ ! -d "$SCRIPT_DIR" ]; then
        echo -e "${RED}  ERROR: Script directory not found${NC}"
        exit 1
    fi

    # Pull latest code
    echo -e "${BLUE}  → Pulling latest code...${NC}"
    cd "$SCRIPT_DIR" && git pull --ff-only || {
        echo -e "${YELLOW}  ⚠ git pull failed — using local copy${NC}"
    }

    # Rebuild smsly-media-mgmt if Cargo.toml exists
    if [ -f "$SCRIPT_DIR/../smsly-media-mgmt/Cargo.toml" ]; then
        echo -e "${BLUE}  → Rebuilding smsly-media-mgmt...${NC}"
        cd "$SCRIPT_DIR/../smsly-media-mgmt" && cargo build --release 2>&1 | tail -5
        if [ -f target/release/smsly-media-mgmt ]; then
            cp target/release/smsly-media-mgmt /usr/local/bin/smsly-media-mgmt
            systemctl restart smsly-media-mgmt
            echo -e "${GREEN}  ✓ smsly-media-mgmt updated${NC}"
        fi
    fi

    # Update systemd units
    echo -e "${BLUE}  → Updating systemd units...${NC}"
    for unit in "$SCRIPT_DIR/scripts/systemd"/*.service; do
        [ -f "$unit" ] || continue
        cp -f "$unit" /etc/systemd/system/
    done
    systemctl daemon-reload

    # Restart services
    echo -e "${BLUE}  → Restarting media services...${NC}"
    for svc in smsly-media-mgmt smsly-voice-api smsly-video livekit-server freeswitch kamailio rtpengine; do
        systemctl restart "$svc" || true
    done

    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✓ Update complete${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    exit 0
fi

# ─── Resume Support ───────────────────────────────────────────────────────────
STATE_FILE="$INSTALL_DIR/.media-install-state"
[ "$RESUME_MODE" = "true" ] || rm -f "$STATE_FILE"
state_get() { grep -m1 "^${1}=" "$STATE_FILE" | cut -d= -f2- || echo ""; }
state_set() { mkdir -p "$(dirname "$STATE_FILE")"; echo "${1}=${2}" >> "$STATE_FILE" || true; }

# ─── Fresh Install ────────────────────────────────────────────────────────────
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  SMSLY Media Node — Fresh Install${NC}"
echo -e "${BLUE}  $(date -Iseconds)${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

# Phase 0: Pre-flight
if [ "$(state_get phase0)" != "done" ]; then
    echo -e "\n${BLUE}Phase 0: Pre-flight checks${NC}"
    detect_media_hardware
    check_media_ports
    state_set phase0 done
fi

# Phase 1: Install packages
if [ "$(state_get phase1)" != "done" ]; then
    echo -e "\n${BLUE}Phase 1: Installing media infrastructure packages${NC}"
    install_media_packages
    state_set phase1 done
fi

# Phase 2: Create install dir + generate secrets
if [ "$(state_get phase2)" != "done" ]; then
    echo -e "\n${BLUE}Phase 2: Generating secrets and environment${NC}"
    mkdir -p "$INSTALL_DIR"
    generate_media_secrets "$ENV_FILE"
    state_set phase2 done
fi

# Phase 3: Deploy configs + systemd
if [ "$(state_get phase3)" != "done" ]; then
    echo -e "\n${BLUE}Phase 3: Deploying configs and systemd units${NC}"
    deploy_media_configs "$SCRIPT_DIR"
    deploy_media_systemd_units "$SCRIPT_DIR"
    state_set phase3 done
fi

# Phase 4: Template configs with env vars
if [ "$(state_get phase4)" != "done" ]; then
    echo -e "\n${BLUE}Phase 4: Templating environment variables into configs${NC}"
    template_media_configs "$ENV_FILE"
    state_set phase4 done
fi

# Phase 5: Start services
if [ "$(state_get phase5)" != "done" ]; then
    echo -e "\n${BLUE}Phase 5: Starting media services${NC}"
    start_media_services
    state_set phase5 done
fi

# Phase 6: Verify
if [ "$(state_get phase6)" != "done" ]; then
    echo -e "\n${BLUE}Phase 6: Verifying services${NC}"
    sleep 3
    verify_media_services
    state_set phase6 done
fi

echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✓ Media node installation complete${NC}"
echo -e "${GREEN}  → Config: ${ENV_FILE}${NC}"
echo -e "${GREEN}  → Logs:   ${LOG_FILE}${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
