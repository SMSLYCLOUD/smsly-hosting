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
REPO_URL="${MEDIA_REPO_URL:-}"
REPO_TOKEN="${MEDIA_REPO_TOKEN:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --update)    UPDATE_MODE=true; shift ;;
        --debug)     DEBUG_MODE=true; shift ;;
        --resume)    RESUME_MODE=true; shift ;;
        --repo-url)  REPO_URL="$2"; shift 2 ;;
        --repo-token) REPO_TOKEN="$2"; shift 2 ;;
        --help|-h)
            echo "Usage: sudo bash install-media-node.sh [OPTIONS]"
            echo ""
            echo "  (no args)     Fresh install"
            echo "  --update      Pull latest code, rebuild smsly-media-mgmt, restart services"
            echo "  --debug       Show system status and exit"
            echo "  --resume      Skip already-completed steps"
            echo "  --repo-url    Git URL containing the proprietary media scripts"
            echo "  --repo-token  Scoped token to authenticate the clone"
            exit 0
            ;;
        *)
            shift
            ;;
    esac
done

if [ -n "$REPO_URL" ]; then
    echo -e "${BLUE}Cloning proprietary media scripts from $REPO_URL...${NC}"
    # Never delete our own cwd out from under ourselves: manual runs are
    # often launched from inside /opt/smsly-media-scripts, which is exactly
    # the directory about to be replaced.
    cd /tmp 2>/dev/null || cd /
    # Extract the domain to inject the token for HTTPS clones
    if [[ "$REPO_URL" == https://* ]] && [ -n "$REPO_TOKEN" ]; then
        AUTH_URL="${REPO_URL/https:\/\//https:\/\/oauth2:${REPO_TOKEN}@}"
    else
        AUTH_URL="$REPO_URL"
    fi
    rm -rf /opt/smsly-media-scripts
    git clone "$AUTH_URL" /opt/smsly-media-scripts
    SCRIPT_DIR="/opt/smsly-media-scripts"
fi

# ─── Mode defaults for shared lib files ─────────────────────────────────
# lib/*.sh helpers are shared with the main installer and expect its mode
# variables to exist (everything runs under `set -u`). A media node is
# none of agent-lite/node/master — define the full family explicitly so
# sourcing can never trip on unbound variables.
MODE_AGENT_LITE="${MODE_AGENT_LITE:-false}"
MODE_NODE="${MODE_NODE:-false}"
MODE_MEDIA_NODE=true
MODE_MASTER="${MODE_MASTER:-false}"
INSTALL_MODE="${INSTALL_MODE:-media-node}"
NODE_TYPE="${NODE_TYPE:-media-node}"
REFRESH_MODE="${REFRESH_MODE:-false}"
RECOVER_MODE="${RECOVER_MODE:-false}"

# ─── Runtime constants (early: sourced lib files may reference them) ────
LOG_FILE="/var/log/smsly-media-install.log"
INSTALL_DIR="/opt/smsly-hosting-media"
ENV_FILE="$INSTALL_DIR/.env"
LOCK_FILE="/tmp/smsly-media-install.lock"

# ─── Source shared helpers ────────────────────────────────────────────────
LIB_DIR="$SCRIPT_DIR/lib"
for lib in "$LIB_DIR"/*.sh; do
    [ -f "$lib" ] || continue
    # Skip master-only install flows and mode-specific logic that
    # conflicts with the media-node installer flow. Every fresh_*.sh /
    # update_*.sh module runs its phase inline at source time against a
    # Docker/Django stack that media nodes don't have; media phases live
    # in lib/media-node.sh (install_media_node / update_media_node).
    case "$(basename "$lib")" in
        fresh*.sh|update*.sh|agent-lite.sh|media-node.sh|media-node-ops.sh) continue ;;
    esac
    source "$lib"
done

# ─── Source media-node functions ──────────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/lib/media-node.sh" ]; then
    echo -e "${RED}ERROR: lib/media-node.sh not found at $SCRIPT_DIR/lib/media-node.sh${NC}"
    echo -e "       Make sure install-media-node.sh is in the same directory as lib/"
    exit 1
fi
source "$SCRIPT_DIR/lib/media-node.sh"
echo -e "${GREEN}  ✓ Shared libraries loaded${NC}"

# ─── Re-assert media paths (AFTER sourcing) ──────────────────────────────
# Shared libs (notably common.sh) unconditionally assign master paths
# (INSTALL_DIR=/opt/smsly-hosting, its LOG/COMPOSE/LOCK files) at source
# time, clobbering the values above. The media installer owns these —
# force them back before anything (lock, log, state, phases) uses them.
INSTALL_DIR="/opt/smsly-hosting-media"
ENV_FILE="$INSTALL_DIR/.env"
STATE_FILE="$INSTALL_DIR/.media-install-state"
LOG_FILE="/var/log/smsly-media-install.log"
LOCK_FILE="/tmp/smsly-media-install.lock"
COMPOSE_FILE=""

# ─── Acquire lock ────────────────────────────────────────────────────────────
if [ -f "$LOCK_FILE" ]; then
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
    for svc in postgresql redis-server wireguard kamailio freeswitch rtpengine coturn smsly-voice-api smsly-video smsly-media-mgmt openresty; do
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

    # Call the shared update function from lib/media-node.sh
    update_media_node "$SCRIPT_DIR"
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
    stop_stale_media_listeners
    check_media_ports
    state_set phase0 done
fi

# Phase 1: Install packages
if [ "$(state_get phase1)" != "done" ]; then
    echo -e "\n${BLUE}Phase 1: Installing media infrastructure packages${NC}"
    install_media_packages
    state_set phase1 done
fi

# Phase 1.5: Security hardening (fail2ban, UFW, kernel, auditd)
# Media nodes run bare-metal services exposed to the internet — they need
# the same security stack as Docker-based nodes.
if [ "$(state_get phase1h)" != "done" ]; then
    echo -e "\n${BLUE}Phase 1.5: Security hardening${NC}"
    if type harden_security_bootstrap &>/dev/null; then
        harden_security_bootstrap || echo -e "${YELLOW}  ⚠ Some hardening layers had issues (non-fatal)${NC}"
    else
        echo -e "${YELLOW}  ⚠ harden_security_bootstrap not found — skipping (ensure lib/harden.sh is sourced)${NC}"
    fi
    state_set phase1h done
fi

# Phase 2: Create install dir + generate secrets
if [ "$(state_get phase2)" != "done" ]; then
    echo -e "\n${BLUE}Phase 2: Generating secrets and environment${NC}"
    mkdir -p "$INSTALL_DIR"
    generate_media_secrets "$ENV_FILE"
    state_set phase2 done
fi

# Phase 2.5: Build the management daemon (Rust). Without this there is no
# /usr/local/bin/smsly-media-mgmt and Phase 5/6 fail. (The update path
# builds it via update_media_node(); fresh installs must do it here.)
if [ "$(state_get phase2b)" != "done" ]; then
    echo -e "\n${BLUE}Phase 2.5: Building management daemon${NC}"
    build_media_mgmt "$SCRIPT_DIR"
    state_set phase2b done
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
    if type harden_security_verify &>/dev/null; then
        harden_security_verify || true
    fi
    state_set phase6 done
fi

echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✓ Media node installation complete${NC}"
echo -e "${GREEN}  → Config: ${ENV_FILE}${NC}"
echo -e "${GREEN}  → Logs:   ${LOG_FILE}${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
