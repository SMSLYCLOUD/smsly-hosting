#!/bin/bash

# =============================================================================
# Grid by SMSLY - Universal Installer v3.2.4 (Production Hardened)
# VERSION: 2026-05-07-0219
# =============================================================================
# Orchestrator — sources modular lib/ files and dispatches to the
# appropriate mode (--update, --wipe, --fix-domain, fresh install, etc.)
# =============================================================================

set -euo pipefail

# ─── Defaults for unset env vars ─────────────────────────────────────────────
export SMSLY_SERVICE_PROXY_UPSTREAM=${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}

# ─── Root Check ──────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo -e "\033[0;31mERROR: This script must be run as root.\033[0m"
    echo -e "Please use: sudo bash $0 $*"
    exit 1
fi

# ─── Early defaults & flag parsing ───────────────────────────────────────────
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

# Read and export all variables from .env early if it exists
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
    --no-screen|--skip-screen) NO_SCREEN=true ;;
    --wipe)            NO_SCREEN=true; rm -f "/opt/smsly-hosting/.smsly_install_state" "/opt/smsly-hosting/.smsly_install_state.mode" ;;
    --fix-domain)      NO_SCREEN=true ;;
    --fix-permissions) NO_SCREEN=true ;;
    --recover|--refresh|--debug|--verify|--clear|--help|-h|--recreate-traefik)
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
    MODE_AGENT_LITE=true; MODE_NODE=false; MODE="agent"; NODE_TYPE="agent-lite" ;;
  node)
    MODE_AGENT_LITE=false; MODE_NODE=true; MODE="node"; NODE_TYPE="node" ;;
  master)
    MODE_AGENT_LITE=false; MODE_NODE=false; MODE="master"; NODE_TYPE="master" ;;
  *)
    echo -e "\033[0;31mERROR: Unknown install mode: $INSTALL_MODE\033[0m"; exit 1 ;;
esac
export INSTALL_MODE MODE NODE_TYPE

# ─── Resolve script path ─────────────────────────────────────────────────────
SCRIPT_PATH="$(readlink -f "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

# ─── Screen Guard ────────────────────────────────────────────────────────────
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
    exit 0
fi

# ─── Workdir ──────────────────────────────────────────────────────────────────
if [ -n "${SMSLY_INSTALL_WORKDIR:-}" ] && [ -d "${SMSLY_INSTALL_WORKDIR}" ]; then
    cd "${SMSLY_INSTALL_WORKDIR}" 2>/dev/null || cd /root 2>/dev/null || cd /
else
    cd /root 2>/dev/null || cd /
fi

# ─── Colors ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"
export NEEDRESTART_MODE="${NEEDRESTART_MODE:-a}"

# ─── Mode variable defaults (must be set before lib/ops.sh sourcing) ───────────
UPDATE_MODE=""
WIPE_MODE="false"
RECOVER_MODE="false"
REFRESH_MODE="false"
DEBUG_MODE="false"
VERIFY_MODE="false"
CLEAR_MODE="false"
FIX_DOMAIN_MODE="false"
FIX_PERMISSIONS_MODE="false"
FORCE_REDEPLOY="false"
RECREATE_TRAEFIK="false"

# ─── Source library modules ───────────────────────────────────────────────────
LIB_DIR="$SCRIPT_DIR/lib"
for lib in "$LIB_DIR"/*.sh; do
    # Skip mode-entry files — they are sourced on-demand by the
    # mode dispatch below (they contain inline code, not just functions).
    case "$lib" in */fresh.sh|*/update.sh) continue ;; esac
    [ -f "$lib" ] && source "$lib"
done

# ─── Runtime constants ────────────────────────────────────────────────────────
LOG_FILE="/var/log/smsly-install.log"
INSTALL_DIR="/opt/smsly-hosting"
CREDENTIALS_FILE="$INSTALL_DIR/.credentials"
SNAPSHOT_FILE="$INSTALL_DIR/.update-safe-snapshot"
BACKUP_DIR="$INSTALL_DIR/.update-backups"
LOCK_FILE="/tmp/smsly-install.lock"
CADDY_LAST_GOOD="$INSTALL_DIR/caddy-config/Caddyfile.smsly-last-good"

# ─── Second argument parser ────────────────────────────────────────────────────

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
            echo "Usage: sudo bash install.sh [--mode=...] [--update|--update-half|--update-frontend|--update-backend|--refresh|--recover|--debug|--wipe|--clear|--fix-domain|--fix-permissions]"
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
            echo "  --recreate-traefik One-time safe recreate of traefik (preserves acme.json + certs)"
            exit 0
            ;;
    esac
done

if [ "$MODE_AGENT_LITE" = "true" ]; then
    COMPOSE_FILE="infrastructure/docker/docker-compose.agent-lite.yml"
fi

# ─── Log setup + lock + traps ──────────────────────────────────────────────────
exec > >(tee -a "$LOG_FILE") 2>&1
acquire_install_lock
trap 'release_install_lock' EXIT

MODE_LABEL="fresh-install"
if [ "$MODE_AGENT_LITE" = "true" ]; then MODE_LABEL="agent-lite-install"
elif [ "$MODE_NODE" = "true" ]; then MODE_LABEL="node-install"; fi
if [ -n "$UPDATE_MODE" ]; then MODE_LABEL="update-$UPDATE_MODE"
elif [ "$REFRESH_MODE" = "true" ]; then MODE_LABEL="refresh"
elif [ "$RECOVER_MODE" = "true" ]; then MODE_LABEL="recover"
elif [ "$DEBUG_MODE" = "true" ]; then MODE_LABEL="debug"
elif [ "$WIPE_MODE" = "true" ]; then MODE_LABEL="wipe"
elif [ "${FIX_DOMAIN_MODE:-false}" = "true" ]; then MODE_LABEL="fix-domain"
fi

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  SMSLY Hosting Install Log — $(date -Iseconds)"
echo "  Mode: $MODE_LABEL"
echo "═══════════════════════════════════════════════════════════"

# ─── Rollback Trap ──────────────────────────────────────────────────────────
cleanup_on_failure() {
    local exit_code=$?
    if [ -n "${HEARTBEAT_PID:-}" ]; then
        kill "$HEARTBEAT_PID" 2>/dev/null || true
        wait "$HEARTBEAT_PID" 2>/dev/null || true
    fi
    if [ $exit_code -ne 0 ]; then
        echo -e "\n${RED}════════════════════════════════════════════════════════════${NC}"
        echo -e "${RED}  INSTALLATION FAILED (exit code: $exit_code)${NC}"
        echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
        if [ -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
            cd "$INSTALL_DIR" 2>/dev/null || true
            dump_diagnostic_logs "$INSTALL_DIR/.env" || true
        fi
        echo -e "${YELLOW}  → Rolling back...${NC}"
        restore_last_good_caddy >/dev/null 2>&1 || true
        if [ "${SMSLY_ROLLBACK_DOWN:-false}" = "true" ] && [ -f "$INSTALL_DIR/$COMPOSE_FILE" ]; then
            cd "$INSTALL_DIR" 2>/dev/null || true
            docker compose -f "$COMPOSE_FILE" down 2>/dev/null || true
        else
            echo -e "${YELLOW}  Runtime containers left running to avoid avoidable downtime.${NC}"
        fi
        if [ -f "$INSTALL_DIR/.env.backup" ]; then
            echo -e "${YELLOW}  → Restoring previous .env from backup${NC}"
            mv "$INSTALL_DIR/.env.backup" "$INSTALL_DIR/.env" 2>/dev/null || true
        fi
        if [ -f "$INSTALL_DIR/.git-stash-marker" ]; then
            echo -e "${YELLOW}  → Restoring git stash (rolling back code changes)${NC}"
            cd "$INSTALL_DIR" && git stash pop 2>/dev/null || true
            rm -f "$INSTALL_DIR/.git-stash-marker"
        fi
        echo -e "${YELLOW}  Full log: $LOG_FILE${NC}"
        echo -e "${RED}  Please review the log and re-run the installer.${NC}"
        echo -e "${YELLOW}  ↳ Tip: Use --resume to skip completed steps: sudo bash install.sh --resume${NC}"
        if [ -n "${STY:-}" ]; then
            echo -e "\n${YELLOW}  [GUARD] Installation failed inside a screen session.${NC}"
            echo -e "${YELLOW}  Screen session will remain open for debugging.${NC}"
            echo -e "${YELLOW}  Type 'exit' to close this window.${NC}"
            release_install_lock
            exec bash
        fi
    fi
    release_install_lock
}
trap cleanup_on_failure EXIT

sync_install_state_flavor

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   Grid - Production Installer v3.2.4${NC}"
echo -e "${BLUE}   Target: Ubuntu LTS${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}\n"

# =============================================================================
# MODE DISPATCH
# =============================================================================

# ─── Wipe Mode ────────────────────────────────────────────────────────────────
if [ "$WIPE_MODE" = "true" ]; then
    wipe_existing_install
    exit 0
fi

# ─── Fix Domain Mode ──────────────────────────────────────────────────────────
if [ "${FIX_DOMAIN_MODE:-false}" = "true" ]; then
    fix_domain_sync
    exit 0
fi

# ─── Fix Permissions Mode ────────────────────────────────────────────────────
if [ "${FIX_PERMISSIONS_MODE:-false}" = "true" ]; then
    fix_env_permissions "$INSTALL_DIR/.env"
    echo -e "\n${GREEN}  ✓ Permissions fixed. You can now run the installer or update.${NC}"
    exit 0
fi

# ─── Recover Mode ─────────────────────────────────────────────────────────────
if [ "$RECOVER_MODE" = "true" ]; then
    recover_runtime_stack
    exit 0
fi

# ─── Refresh Mode ─────────────────────────────────────────────────────────────
if [ "$REFRESH_MODE" = "true" ]; then
    safe_refresh_runtime_services
    exit 0
fi

# ─── Verify Mode ──────────────────────────────────────────────────────────────
if [ "$VERIFY_MODE" = "true" ]; then
    # Re-source ops.sh — the verify dispatch block at the end is gated by
    # VERIFY_MODE, which is now set, so it will execute and exit 0.
    source "$LIB_DIR/ops.sh"
    exit 0
fi

# ─── Debug Mode ───────────────────────────────────────────────────────────────
if [ "$DEBUG_MODE" = "true" ]; then
    debug_platform_status
    exit 0
fi

# ─── Update Mode ──────────────────────────────────────────────────────────────
if [ -n "$UPDATE_MODE" ]; then
    source "$LIB_DIR/update.sh"
    exit 0
fi

# =============================================================================
# FRESH INSTALL (fallthrough)
# =============================================================================
source "$LIB_DIR/fresh.sh"
