#!/bin/bash

# =============================================================================
# Grid by SMSLY - Universal Installer v3.2.4 (Production Hardened)
# VERSION: 2026-05-07-0219
# =============================================================================
# Orchestrator — sources modular lib/ files and dispatches to the
# appropriate mode (--update, --wipe, --fix-domain, fresh install, etc.)
# =============================================================================

set -euo pipefail

export PATH="/usr/local/bin:$PATH"

# ─── Defaults for unset env vars ─────────────────────────────────────────────
export SMSLY_SERVICE_PROXY_UPSTREAM=${SMSLY_SERVICE_PROXY_UPSTREAM:-traefik:80}
export SMSLY_BRANCH="${SMSLY_BRANCH:-master}"
export SMSLY_GIT_REMOTE="${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"

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
COMPOSE_FILE="${COMPOSE_FILE:-/opt/smsly-hosting/docker-compose.prod.yml}"
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
    --with-replica)    REPLICA_MODE="true" ;;
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

# ─── Registry Self-Healing ──────────────────────────────────────────────────
# Verify the container registry URL in .env is actually reachable.  If not,
# try the Docker overlay DNS name (registry:5000) or the host loopback
# (127.0.0.1:5000) and update .env automatically.
# This prevents deploy failures when the .env has a stale or unreachable URL.
# Inline env helpers (lib/env.sh is not sourced yet at this point)
_env_get_value() {
    grep -m1 "^${2}=" "$1" | cut -d= -f2- | sed 's/^"//;s/"$//;s/^'\''//;s/'\''$//' || true
}
_env_set_value() {
    python3 - "$1" "$2" "$3" <<'PY'
from pathlib import Path; import sys
env_path = Path(sys.argv[1]); key = sys.argv[2]; value = sys.argv[3]; prefix = f"{key}="
if not env_path.exists(): env_path.write_text(f"{key}={value}\n"); sys.exit(0)
lines = env_path.read_text().splitlines(); updated = []; found = False
for line in lines:
    if line.startswith(prefix):
        if not found: updated.append(f"{key}={value}"); found = True
        continue
    updated.append(line)
if not found: updated.append(f"{key}={value}")
env_path.write_text("\n".join(updated) + "\n")
PY
}

_registry_self_heal() {
    # Ensure 'registry' hostname resolves to localhost on the host OS for local pulls/pushes
    if [ -f "/etc/hosts" ] && ! grep -q -w "registry" /etc/hosts; then
        if [ "$EUID" -eq 0 ] || [ "$(id -u)" -eq 0 ]; then
            echo "127.0.0.1 registry" >> /etc/hosts || true
        elif command -v sudo; then
            echo "127.0.0.1 registry" | sudo tee -a /etc/hosts || true
        fi
    fi

    local env_file="${1:-/opt/smsly-hosting/.env}"
    [ -f "$env_file" ] || return 0

    local configured_url
    configured_url="$(_env_get_value "$env_file" "CONTAINER_REGISTRY_URL")"
    [ -n "$configured_url" ] || configured_url="127.0.0.1:5000"

    # If docker isn't available, skip
    command -v docker || return 0

    _test_registry() {
        local url="$1"
        local host="${url%%:*}"
        local port="${url##*:}"
        local code

        # Try HEAD /v2/ — if it returns anything (even 401/403), the registry is reachable
        code=$(timeout -k 5 5 curl -sfk -o /dev/null -w "%{http_code}" "https://${url}/v2/")
        if echo "$code" | grep -qE '^(200|401|403)$'; then
            echo -e "  \033[0;32m→ $url OK (HTTPS $code)\033[0m"
            return 0
        fi
        code=$(timeout -k 5 5 curl -sfk -o /dev/null -w "%{http_code}" "http://${url}/v2/")
        if echo "$code" | grep -qE '^(200|401|403)$'; then
            echo -e "  \033[0;32m→ $url OK (HTTP $code)\033[0m"
            return 0
        fi

        # Diagnostics: show the actual failure reason for debugging
        local curl_err
        curl_err=$(timeout -k 5 3 curl -sv "http://${url}/v2/" 2>&1 | grep -E '^(curl:|Connected|Connection refused|Operation timed out|resolve|ssl|SSL)' | head -5)
        [ -n "$curl_err" ] && echo -e "  \033[0;33m  ↳ $url ${code:-timeout/error} — ${curl_err//$'\n'/ | }\033[0m"

        # Fallback: try docker pull
        local pull_out pull_short
        pull_out=$(timeout -k 5 10 docker pull "${url}/alpine:latest" || true)
        if echo "$pull_out" | grep -qE "Pulled|up to date|Image is up to date"; then
            echo -e "  \033[0;32m→ $url OK (docker pull)\033[0m"
            return 0
        fi
        pull_short=$(echo "$pull_out" | tail -3 | tr '\n' ' ')
        [ -n "$pull_short" ] && echo -e "  \033[0;33m  ↳ $url pull: $pull_short\033[0m"
        return 1
    }

    local candidates=()
    # Build a deduplicated list of candidates
    for candidate in "$configured_url" "registry:5000" "127.0.0.1:5000"; do
        local already=false
        for c in "${candidates[@]}"; do
            [ "$c" = "$candidate" ] && already=true && break
        done
        $already || candidates+=("$candidate")
    done

    local working_url=""
    for url in "${candidates[@]}"; do
        if _test_registry "$url"; then
            working_url="$url"
            break
        fi
    done

    if [ -z "$working_url" ]; then
        echo -e "\033[0;33m⚠ Registry self-heal: no reachable registry found (all candidates unreachable).\033[0m"
        return 0
    fi

    if [ "$working_url" != "$configured_url" ]; then
        echo -e "\033[0;33m⚠ Registry self-heal: CONTAINER_REGISTRY_URL=$configured_url is unreachable.\033[0m"
        echo -e "\033[0;33m  → Updating to $working_url (verified reachable).\033[0m"
        _env_set_value "$env_file" "CONTAINER_REGISTRY_URL" "$working_url"
    fi
}

# Run self-heal if we're in non-interactive mode (install/update path)
if [ -f "/opt/smsly-hosting/.env" ]; then
    _registry_self_heal
fi
unset -f _registry_self_heal _test_registry _env_get_value _env_set_value

# ─── Resolve script path ─────────────────────────────────────────────────────
SCRIPT_PATH="$(readlink -f "$0" || echo "$0")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"

# ─── Bootstrap lib/ when running from a standalone install.sh ──────────────────
# When invoked via `curl ... -o /tmp/install.sh && bash /tmp/install.sh`, lib/
# is not co-located with install.sh. Detect that and fetch lib/ from the same
# source so the rest of the script can source lib/*.sh normally.
if [ ! -d "$SCRIPT_DIR/lib" ] && [ -n "${SMSLY_GIT_REMOTE:-}" ]; then
    BOOTSTRAP_LIB_DIR="/tmp/smsly-lib-$$"
    mkdir -p "$BOOTSTRAP_LIB_DIR"
    echo -e "\033[0;34m  → Bootstrapping lib/ from $SMSLY_GIT_REMOTE ...\033[0m"
    # Try tarball download first (cheap and deterministic)
    _lib_branch="${SMSLY_BRANCH:-master}"
    _lib_tar_url="${SMSLY_GIT_REMOTE%.git}/archive/refs/heads/${_lib_branch}.tar.gz"
    if command -v curl; then
        if curl -fsSL "$SMSLY_GIT_REMOTE/archive/refs/heads/${_lib_branch}.tar.gz" -o "/tmp/smsly-repo-${$}.tar.gz" \
            && tar -xzf "/tmp/smsly-repo-${$}.tar.gz" -C /tmp; then
            _extracted_dir=$(tar -tzf "/tmp/smsly-repo-${$}.tar.gz" | head -1 | cut -d/ -f1)
            if [ -d "/tmp/$_extracted_dir/lib" ]; then
                cp -r "/tmp/$_extracted_dir/lib/"* "$BOOTSTRAP_LIB_DIR/" && \
                    echo -e "\033[0;32m  ✓ lib/ bootstrapped to $BOOTSTRAP_LIB_DIR\033[0m"
            fi
            rm -rf "/tmp/smsly-repo-${$}.tar.gz" "/tmp/$_extracted_dir"
        fi
    fi
    # Fallback: try git clone if tarball failed
    if [ ! -d "$BOOTSTRAP_LIB_DIR/common.sh" ] && command -v git; then
        _tmp_clone=$(mktemp -d)
        if git clone --depth 1 --branch "${SMSLY_BRANCH:-master}" "$SMSLY_GIT_REMOTE" "$_tmp_clone"; then
            if [ -d "$_tmp_clone/lib" ]; then
                cp -r "$_tmp_clone/lib/"* "$BOOTSTRAP_LIB_DIR/" && \
                    echo -e "\033[0;32m  ✓ lib/ bootstrapped via git clone\033[0m"
            fi
        fi
        rm -rf "$_tmp_clone"
    fi
    if [ -d "$BOOTSTRAP_LIB_DIR" ] && [ -f "$BOOTSTRAP_LIB_DIR/common.sh" ]; then
        SCRIPT_DIR="$BOOTSTRAP_LIB_DIR/.."
        LIB_DIR="$BOOTSTRAP_LIB_DIR"
    else
        echo -e "\033[1;33m  ⚠ Warning: could not bootstrap lib/. Install.sh must be run with lib/ co-located or from a full repo checkout.\033[0m"
    fi
    unset _lib_branch _lib_tar_url _extracted_dir _tmp_clone
fi

# ─── Screen Guard ────────────────────────────────────────────────────────────
if [ "${NO_SCREEN:-false}" != "true" ] && [ "$NON_INTERACTIVE" != "true" ] && [ -t 0 ] && [ -z "${STY:-}" ] && [[ "${TERM:-}" != screen* ]] && [ -z "${TMUX:-}" ]; then
    if command -v screen; then
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
    cd "${SMSLY_INSTALL_WORKDIR}" || cd /root || cd /
else
    cd /root || cd /
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
OBSERVABILITY_MODE="false"
REPLICA_MODE="false"

# ─── Source library modules ───────────────────────────────────────────────────
# LIB_DIR is set by either the bootstrap block above (standalone install.sh) or
# by the repo's own $SCRIPT_DIR/lib when run from a clone.
# --- BEGIN_LIB_SOURCING ---
if [ -z "${LIB_DIR:-}" ] || [ ! -d "$LIB_DIR" ]; then
    LIB_DIR="$SCRIPT_DIR/lib"
fi
for lib in "$LIB_DIR"/*.sh; do
    # Skip mode-entry files — they are sourced on-demand by the
    # mode dispatch below (they contain inline code, not just functions).
    case "$lib" in */fresh*.sh|*/update*.sh|*/harden_*.sh|*/install-gvisor.sh|*/install-kata.sh) continue ;; esac
    [ -f "$lib" ] && source "$lib"
done
# --- END_LIB_SOURCING ---

# ─── Runtime constants ────────────────────────────────────────────────────────
LOG_FILE="/var/log/smsly-install.log"
INSTALL_DIR="/opt/smsly-hosting"
CREDENTIALS_FILE="$INSTALL_DIR/.credentials"
SNAPSHOT_FILE="$INSTALL_DIR/.update-safe-snapshot"
BACKUP_DIR="$INSTALL_DIR/.update-backups"
LOCK_FILE="/tmp/smsly-install.lock"
CADDY_LAST_GOOD="$INSTALL_DIR/caddy-config/Caddyfile.smsly-last-good"
SMSLY_BRANCH="${SMSLY_BRANCH:-master}"
SMSLY_GIT_REMOTE="${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"

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
        --observability) OBSERVABILITY_MODE="true" ;;
        --help|-h)
            echo "Usage: sudo bash install.sh [--mode=...] [--update|--update-half|--update-frontend|--update-backend|--refresh|--recover|--debug|--wipe|--clear|--fix-domain|--fix-permissions|--with-replica]"
            echo ""
            echo "  (no args)          Fresh install (Legacy Python | Full-Stack Master)"
            echo "  --mode=agent-lite  Install as a Lite Agent (shared-DB node)"
            echo "  --mode=node        Install as a Full-Stack Node (own DB, no frontend)"
            echo "  --update           Pull latest code and rebuild all services (full rebuild)"
            echo "  --update-half      Pull latest code, restart backend only — no Docker image rebuild"
            echo "  --clear            Wipes stale addons and frees up docker resources"
            echo "  --fix-domain       Fix domain/IP sync between .env, DB PlatformConfig, and Caddy"
            echo "  --fix-permissions  Fix .env and shared directory permissions for container write access"
            echo "  --with-replica     After install, enable PostgreSQL streaming replication (warm-standby read replica on the same host)"
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
elif [ "${REPLICA_MODE:-false}" = "true" ]; then MODE_LABEL="with-replica"
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
        kill "$HEARTBEAT_PID" || true
        wait "$HEARTBEAT_PID" || true
    fi
    if [ $exit_code -ne 0 ]; then
        echo -e "\n${RED}════════════════════════════════════════════════════════════${NC}"
        echo -e "${RED}  INSTALLATION FAILED (exit code: $exit_code)${NC}"
        echo -e "${RED}════════════════════════════════════════════════════════════${NC}"
        if [ -f "$COMPOSE_FILE" ]; then
            cd "$INSTALL_DIR" || true
            dump_diagnostic_logs "$INSTALL_DIR/.env" || true
        fi
        echo -e "${YELLOW}  → Rolling back...${NC}"
        restore_last_good_caddy || true
        if [ "${SMSLY_ROLLBACK_DOWN:-false}" = "true" ] && [ -f "$COMPOSE_FILE" ]; then
            cd "$INSTALL_DIR" || true
            docker compose -f "$COMPOSE_FILE" down || true
        else
            echo -e "${YELLOW}  Runtime containers left running to avoid avoidable downtime.${NC}"
        fi
        if [ -f "$INSTALL_DIR/.env.backup" ]; then
            echo -e "${YELLOW}  → Restoring previous .env from backup${NC}"
            mv "$INSTALL_DIR/.env.backup" "$INSTALL_DIR/.env" || true
        fi
        if [ -f "$INSTALL_DIR/.git-stash-marker" ]; then
            echo -e "${YELLOW}  → Restoring git stash (rolling back code changes)${NC}"
            cd "$INSTALL_DIR" && git stash pop || true
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
    FORCE_WIPE=1
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
    # Security: bootstrap services (fire-and-forget), then refresh, then verify
    if [ -f "$INSTALL_DIR/lib/harden.sh" ]; then
        source "$INSTALL_DIR/lib/harden.sh"
        harden_security_bootstrap
    fi
    safe_refresh_runtime_services
    if command -v harden_security_verify; then
        harden_security_verify
    fi
    exit 0
fi

# ─── Observability Mode ────────────────────────────────────────────────────────
deploy_observability_stack() {
    echo -e "${BLUE}  → Deploying observability stack (Grafana, Loki, Prometheus)...${NC}"
    if [ -f "$INSTALL_DIR/infrastructure/docker/docker-compose.observability.yml" ]; then
        # Ensure Grafana has a non-empty admin password (Grafana >=11 requires it).
        if [ -z "${GRAFANA_PASSWORD:-}" ]; then
            GRAFANA_PASSWORD="$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits+'-_') for _ in range(40)))" || openssl rand -base64 30 | tr -d '+/=' )"
            export GRAFANA_PASSWORD
            if [ -f "$INSTALL_DIR/.env" ]; then
                if grep -q '^GRAFANA_PASSWORD=' "$INSTALL_DIR/.env"; then
                    sed -i "s|^GRAFANA_PASSWORD=.*|GRAFANA_PASSWORD=$GRAFANA_PASSWORD|" "$INSTALL_DIR/.env"
                else
                    echo "GRAFANA_PASSWORD=$GRAFANA_PASSWORD" >> "$INSTALL_DIR/.env"
                fi
            fi
            echo -e "${GREEN}  ✓ Auto-generated Grafana admin password${NC}"
        fi

        # Ensure prometheus-targets dir exists with correct ownership for
        # non-root container uid 1000.
        mkdir -p "$INSTALL_DIR/prometheus-targets"
        if ! chown -R 1000:1000 "$INSTALL_DIR/prometheus-targets"; then
            echo -e "${YELLOW}  ⚠ Could not chown prometheus-targets to uid 1000 — target files may fail${NC}"
        fi
        chmod 2777 "$INSTALL_DIR/prometheus-targets" || true

        # Ensure scripts mounted into containers are executable (git may not preserve +x).
        chmod +x "$INSTALL_DIR"/scripts/alertmanager-entrypoint.sh || true
        chmod +x "$INSTALL_DIR"/scripts/enable-replica.sh || true

        docker compose \
            --env-file "$INSTALL_DIR/.env" \
            -f "$INSTALL_DIR/infrastructure/docker/docker-compose.observability.yml" \
            pull --ignore-pull-failures || \
            echo -e "${YELLOW}  ⚠ Observability stack pull failed (non-fatal)${NC}"
        docker compose \
            --env-file "$INSTALL_DIR/.env" \
            -f "$INSTALL_DIR/infrastructure/docker/docker-compose.observability.yml" \
            up -d --pull always || \
            echo -e "${YELLOW}  ⚠ Observability stack start failed (non-fatal)${NC}"
        echo -e "${GREEN}  ✓ Observability stack deployed${NC}"
    else
        echo -e "${YELLOW}  ⚠ Observability compose file not found at infrastructure/docker/docker-compose.observability.yml${NC}"
    fi
}

if [ "${OBSERVABILITY_MODE:-false}" = "true" ]; then
    deploy_observability_stack
    exit 0
fi

# ─── Replica Mode (standalone: enable the warm-standby replica on an existing stack) ──
deploy_replica_stack() {
    echo -e "${BLUE}  → Enabling PostgreSQL read replica (streaming replication)...${NC}"
    local script="$INSTALL_DIR/scripts/enable-replica.sh"
    if [ ! -f "$script" ]; then
        echo -e "${RED}  ✗ $script not found. Pull the latest code and re-run.${NC}"
        return 1
    fi
    chmod +x "$script" || true
    # Non-interactive: enable-replica.sh does not prompt.
    if bash "$script"; then
        echo -e "${GREEN}  ✓ Read replica enabled${NC}"
    else
        local rc=$?
        echo -e "${RED}  ✗ enable-replica.sh exited with code $rc — replica NOT enabled${NC}"
        echo -e "${YELLOW}    You can re-run it manually: sudo $INSTALL_DIR/scripts/enable-replica.sh${NC}"
        return $rc
    fi
}

# Standalone usage: `sudo bash install.sh --with-replica` (no other args)
# against an already-deployed stack. This is the entry point operators
# use to add the replica later, after the initial install.
if [ "${REPLICA_MODE:-false}" = "true" ] && [ -z "${UPDATE_MODE:-}" ] \
    && [ "${OBSERVABILITY_MODE:-false}" != "true" ] \
    && [ "${FIX_DOMAIN_MODE:-false}" != "true" ] \
    && [ "${FIX_PERMISSIONS_MODE:-false}" != "true" ] \
    && [ "${WIPE_MODE:-false}" != "true" ] \
    && [ "${RECOVER_MODE:-false}" != "true" ] \
    && [ "${REFRESH_MODE:-false}" != "true" ] \
    && [ "${DEBUG_MODE:-false}" != "true" ] \
    && [ "${VERIFY_MODE:-false}" != "true" ] \
    && [ "${CLEAR_MODE:-false}" != "true" ]; then
    deploy_replica_stack
    exit $?
fi

# ─── Verify Mode ──────────────────────────────────────────────────────────────
if [ "$VERIFY_MODE" = "true" ]; then
    verify_endpoints
fi

# ─── Debug Mode ───────────────────────────────────────────────────────────────
if [ "$DEBUG_MODE" = "true" ]; then
    cd "$INSTALL_DIR"  || cd /root  || cd /
    debug_platform_status
    exit 0
fi

# ─── Clear Mode ───────────────────────────────────────────────────────────────
if [ "${CLEAR_MODE:-false}" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --clear)${NC}"
        exit 1
    fi
    echo -e "\n${BLUE}  🧹 Running Maintenance Clear...${NC}"

    # Prune unused docker resources
    echo -e "  → Pruning unused Docker containers and images..."
    docker container prune -f || echo -e "${YELLOW}    ⚠ docker container prune failed${NC}"
    docker image prune -af || echo -e "${YELLOW}    ⚠ docker image prune failed${NC}"

    # Stop and remove all stale smsly-addon-* containers (only those NOT running)
    echo -e "  → Removing stale/orphaned service addons (protecting active databases)..."
    ADDON_IDS=$(docker ps -a -q --filter "name=smsly-addon" --filter "status=exited" --filter "status=created" --filter "status=dead")
    if [ -n "$ADDON_IDS" ]; then
        docker rm -f $ADDON_IDS || echo -e "${YELLOW}    ⚠ docker rm addons failed${NC}"
        echo -e "${GREEN}  ✓ Removed inactive orphaned addon containers.${NC}"
    else
        echo -e "${YELLOW}  - No inactive orphaned addons found.${NC}"
    fi

    # Stop and remove all stale deployment/blue-green containers
    echo -e "  → Removing stale deployment containers (protecting active routes)..."
    GREEN_IDS=$(docker ps -a -q --filter "name=-green-" --filter "status=exited" --filter "status=created" --filter "status=dead")
    ROUTER_IDS=$(docker ps -a -q --filter "name=ai-router" --filter "status=exited" --filter "status=created" --filter "status=dead")

    if [ -n "$GREEN_IDS" ]; then
        docker rm -f $GREEN_IDS || echo -e "${YELLOW}    ⚠ docker rm green containers failed${NC}"
        echo -e "${GREEN}  ✓ Removed inactive deployment containers.${NC}"
    fi
    if [ -n "$ROUTER_IDS" ]; then
        docker rm -f $ROUTER_IDS || echo -e "${YELLOW}    ⚠ docker rm routers failed${NC}"
        echo -e "${GREEN}  ✓ Removed inactive AI routers.${NC}"
    fi

    # Clean caches
    echo -e "  → Cleaning system caches..."
    rm -rf /opt/smsly-cache/*  || true
    echo -e "${GREEN}  ✓ Cleared /opt/smsly-cache/.${NC}"

    echo -e "\n${GREEN}  ✨ Maintenance complete. You can now re-run deployments.${NC}"
    exit 0
fi

# ─── Recreate-Traefik Mode ───────────────────────────────────────────────────
if [ "$RECREATE_TRAEFIK" = "true" ]; then
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --recreate-traefik)${NC}"
        exit 1
    fi
    if [ ! -f "$COMPOSE_FILE" ]; then
        echo -e "${RED}x Missing $COMPOSE_FILE. Run fresh install first.${NC}"
        exit 1
    fi
    cd "$INSTALL_DIR"
    should_manage_caddy || {
        echo -e "${YELLOW}  WARN should_manage_caddy=false; aborting to avoid clobbering.${NC}"
        exit 1
    }
    recreate_traefik_preserving_certs
    exit $?
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
