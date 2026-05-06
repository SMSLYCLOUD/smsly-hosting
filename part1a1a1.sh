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
