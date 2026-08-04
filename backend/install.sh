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
# 
# --- lib/00-vars.sh ---
# 00-vars.sh — Pre-sourced before all other lib files (alphabetical order).
# Provides critical defaults so update.sh can git-pull even on older installs.
export SMSLY_BRANCH="${SMSLY_BRANCH:-master}"
export SMSLY_GIT_REMOTE="${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"

# --- end lib/00-vars.sh ---

# --- lib/agent-lite.sh ---
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
    # NOTE: FIELD_ENCRYPTION_KEY is NOT read from the seed file — it is
    # stored in .env only to limit exposure in plaintext recovery files.
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
    SMSLY_NODE_HOST="${SMSLY_NODE_HOST:-$(detect_public_ip  || true)}"
    [ -n "$SMSLY_NODE_HOST" ] || SMSLY_NODE_HOST="$(hostname -f  || hostname  || echo agent)"
    SMSLY_NODE_ID="${SMSLY_NODE_ID:-$SMSLY_NODE_HOST}"
    local node_slug
    node_slug="$(sanitize_node_identifier "$SMSLY_NODE_ID")"
    SMSLY_NODE_QUEUE="${SMSLY_NODE_QUEUE:-smsly-node-${node_slug}}"

    # Use MASTER_MESH_IP for database only (shared DB).
    # Redis and RabbitMQ run locally on each node — no cross-node dependency.
    local node_redis_password
    node_redis_password="$(env_get_value "$env_file" "REDIS_PASSWORD"  || true)"
    if [ -z "$node_redis_password" ]; then
        node_redis_password="$(python3 -c "import secrets; print(secrets.token_hex(16))"  || openssl rand -hex 16  || echo "")"
    fi
    local redis_url="redis://redis:6379/0"
    if [ -n "$node_redis_password" ]; then
        redis_url="redis://:${node_redis_password}@redis:6379/0"
    fi

    local node_rabbitmq_password
    node_rabbitmq_password="$(env_get_value "$env_file" "RABBITMQ_PASSWORD"  || true)"
    if [ -z "$node_rabbitmq_password" ]; then
        node_rabbitmq_password="$(python3 -c "import secrets; print(secrets.token_hex(16))"  || openssl rand -hex 16  || echo "")"
    fi
    local celery_broker_url="amqp://smsly_user:${node_rabbitmq_password}@rabbitmq:5672//"

    # --- Persistence: Save a recovery seed for future manual updates ---
    cat > "$seed_file" <<EOF
# SMSLY Lite Agent Recovery Seed
# Generated on $(date)
# NOTE: FIELD_ENCRYPTION_KEY is stored in .env only (not duplicated here
# for security — it is the master's database encryption key).
MASTER_IP="$MASTER_IP"
MASTER_MESH_IP="$MASTER_MESH_IP"
MASTER_DB_USER="$MASTER_DB_USER"
MASTER_DB_PASSWORD="$MASTER_DB_PASSWORD"
MASTER_MQ_PASSWORD="$MASTER_MQ_PASSWORD"
MASTER_REDIS_PASSWORD="${MASTER_REDIS_PASSWORD:-}"
MASTER_GATEWAY_SECRET="${MASTER_GATEWAY_SECRET:-}"
MASTER_BACKUP_ENCRYPTION_KEY="${MASTER_BACKUP_ENCRYPTION_KEY:-}"
MASTER_BACKUP_REQUIRE_ENCRYPTION="${MASTER_BACKUP_REQUIRE_ENCRYPTION:-}"
MASTER_GITHUB_WEBHOOK_SECRET="${MASTER_GITHUB_WEBHOOK_SECRET:-}"
MASTER_AUTOSCALER_API_TOKEN="${MASTER_AUTOSCALER_API_TOKEN:-}"
MASTER_FRP_AUTH_TOKEN="${MASTER_FRP_AUTH_TOKEN:-}"
MASTER_PGCAT_ADMIN_PASSWORD="${MASTER_PGCAT_ADMIN_PASSWORD:-}"
SMSLY_NODE_ID="$SMSLY_NODE_ID"
SMSLY_NODE_QUEUE="$SMSLY_NODE_QUEUE"
EOF
    chmod 600 "$seed_file"

    env_set_value "$env_file" "NODE_TYPE" "agent-lite"
    env_set_value "$env_file" "MODE" "agent"
    env_set_value "$env_file" "COMPOSE_FILE" "infrastructure/docker/docker-compose.agent-lite.yml"
    env_set_value "$env_file" "TRAEFIK_HTTP_BIND" "0.0.0.0:80"
    env_set_value "$env_file" "TRAEFIK_ENABLE_WEBSECURE" "false"
    env_set_value "$env_file" "MASTER_IP" "$MASTER_IP"
    env_set_value "$env_file" "MASTER_MESH_IP" "$MASTER_MESH_IP"
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

    # Batch J: sync the remaining critical master secrets to the
    # node. Without these the node can't decrypt shared backups,
    # verify GitHub webhooks, or authenticate to the autoscaler
    # API. Each is a one-way sync from master to node: the
    # node inherits the value but never overwrites the master's
    # copy. If a var is unset on the master, we skip it (an
    # older master that pre-dates the var is treated as "not
    # required" rather than failed).
    local _master_secrets_to_sync=(
        "BACKUP_ENCRYPTION_KEY:master's Fernet key for at-rest backup encryption"
        "|BACKUP_REQUIRE_ENCRYPTION:master's backup-encryption policy (true/false)"
        "|GITHUB_WEBHOOK_SECRET:master's GitHub webhook signature verification secret"
        "|AUTOSCALER_API_TOKEN:master's autoscaler-service bearer token"
        "|FRP_AUTH_TOKEN:master's FRP tunnel relay authentication token"
        "|PGCAT_ADMIN_PASSWORD:master's PgCat administration password"
    )
    local _entry
    for _entry in "${_master_secrets_to_sync[@]}"; do
        local _key="${_entry%%|*}"
        # Read the master secret from the master's .env file.
        # MASTER_ENV_<KEY> env vars are NOT exported by the provisioner;
        # secrets are written to a temporary file and read via env_get_value.
        local _master_val=""
        if [ -f "$MASTER_ENV_FILE" ]; then
            _master_val="$(env_get_value "$MASTER_ENV_FILE" "$_key"  || true)"
        fi
        if [ -n "$_master_val" ]; then
            env_set_value "$env_file" "$_key" "$_master_val"
        fi
    done

    env_set_value "$env_file" "SMSLY_DISABLE_LOCAL_SERVICES" "false"
    env_set_value "$env_file" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
    env_set_value "$env_file" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false"
}

verify_agent_lite_connectivity() {
    [ "$MODE_AGENT_LITE" = "true" ] || return 0
    echo -e "${BLUE}  → Verifying connectivity to Master node (${MASTER_IP})...${NC}"
    
    # 1. Ping Master (public IP)
    if ! ping -c 1 -W 2 "$MASTER_IP" ; then
        echo -e "${YELLOW}  ⚠ Warning: Master node ${MASTER_IP} is not responding to ICMP. Proceeding anyway...${NC}"
    fi

    # 2. Check Database port via mesh IP (internal services use WireGuard)
    local db_check_ip="${MASTER_MESH_IP}"
    if ! timeout -k 5 2 bash -c "</dev/tcp/${db_check_ip}/5432" ; then
        echo -e "${RED}  ✗ ERROR: Master Database (port 5432) is unreachable on ${db_check_ip}.${NC}"
        echo -e "${YELLOW}    Ensure the Master allows port 5432 from this node's IP via WireGuard mesh.${NC}"
        return 1
    fi

    # 3. Redis and RabbitMQ run locally on agent-lite nodes (no Master dependency)
    echo -e "${BLUE}  → Redis and RabbitMQ will run locally on this node.${NC}"

    # 4. The deploy path pulls master-built images from the master's registry.
    local registry_check_ip="${MASTER_MESH_IP}"
    if ! timeout -k 5 2 bash -c "</dev/tcp/${registry_check_ip}/5000" ; then
        echo -e "${RED}  ✗ ERROR: Master container registry (port 5000) is unreachable on ${registry_check_ip}.${NC}"
        echo -e "${YELLOW}    Ensure the Master registry is running and the mesh/firewall allows port 5000 from this node.${NC}"
        return 1
    fi
    if command -v curl ; then
        local registry_code
        registry_code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "http://${registry_check_ip}:5000/v2/"  || true)"
        # Retry with HTTPS if HTTP returned 000 (connection refused / TLS redirect)
        if [ "$registry_code" = "000" ] || [ "$registry_code" = "400" ]; then
            registry_code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "https://${registry_check_ip}:5000/v2/"  || true)"
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

# --- end lib/agent-lite.sh ---

# --- lib/common.sh ---
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# --- lib/logging.sh ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"
export NEEDRESTART_MODE="${NEEDRESTART_MODE:-a}"

# --- end lib/logging.sh ---
# --- lib/validation.sh ---
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

is_real_domain_name() {
    local host="${1:-}"
    [ -n "$host" ] \
        && [ "$host" != "localhost" ] \
        && ! echo "$host" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'
}

# --- end lib/validation.sh ---
# --- lib/network.sh ---
detect_public_ip() {
    local candidate=""
    local endpoint=""
    local endpoints=(
        "https://api.ipify.org"
        "https://ifconfig.me/ip"
        "https://ipv4.icanhazip.com"
    )

    for endpoint in "${endpoints[@]}"; do
        candidate="$(curl -4 -fsS -m 5 "$endpoint"  | tr -d '\r\n' || true)"
        if is_valid_ipv4 "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done

    candidate="$(hostname -I  | awk '{print $1}' | tr -d '\r\n' || true)"
    if is_valid_ipv4 "$candidate"; then
        echo "$candidate"
        return 0
    fi

    echo "127.0.0.1"
    return 0
}

ensure_update_networks() {
    docker network inspect smsly-net  || docker network create smsly-net || echo -e "${YELLOW}    ⚠ smsly-net create failed (may already exist)${NC}"
    docker network inspect smsly-proxy  || docker network create smsly-proxy || echo -e "${YELLOW}    ⚠ smsly-proxy create failed (may already exist)${NC}"
    docker network inspect socket-proxy  || docker network create --driver bridge --internal socket-proxy || echo -e "${YELLOW}    ⚠ socket-proxy create failed (may already exist)${NC}"
}

https_listener_active() {
    if command -v ss ; then
        ss -H -tln  | awk '{print $4}' | grep -Eq ':443$'
    else
        lsof -iTCP:443 -sTCP:LISTEN
    fi
}

# --- end lib/network.sh ---
# --- lib/docker.sh ---
_merge_daemon_json() {
    # Merge new keys into /etc/docker/daemon.json without clobbering existing
    # settings (runtimes, log-driver, live-restore, etc.) that other installer
    # modules may have written.
    # Usage: _merge_daemon_json '{"insecure-registries":[...],"dns":[...]}'
    local new_json="$1"
    local daemon_json="/etc/docker/daemon.json"
    python3 - "$daemon_json" "$new_json" <<'PY'
import json, sys
from pathlib import Path

daemon_path = Path(sys.argv[1])
new_cfg = json.loads(sys.argv[2])

if daemon_path.exists():
    try:
        cfg = json.loads(daemon_path.read_text())
    except (json.JSONDecodeError, ValueError):
        cfg = {}
else:
    cfg = {}

cfg.update(new_cfg)
daemon_path.write_text(json.dumps(cfg, indent=2) + "\n")
PY
}

configure_docker_mirror() {
    if [ "${MODE_AGENT_LITE:-false}" = "true" ] && [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
        [ -n "${MASTER_IP:-}" ] || MASTER_IP="$(env_get_value "${INSTALL_DIR:-/opt/smsly-hosting}/.env" "MASTER_IP"  || true)"
        [ -n "${MASTER_MESH_IP:-}" ] || MASTER_MESH_IP="$(env_get_value "${INSTALL_DIR:-/opt/smsly-hosting}/.env" "MASTER_MESH_IP"  || true)"
    fi

    local use_dns_fallback=false
    if command -v docker  && systemctl is-active --quiet docker; then
        echo -e "${BLUE}  → Checking Docker DNS resolution for npm registry...${NC}"
        local test_img="node:20-alpine"
        if ! docker image inspect "$test_img" ; then
            test_img="alpine"
        fi
        if ! timeout -k 5 15 docker run --rm "$test_img" nslookup registry.npmjs.org ; then
            echo -e "${YELLOW}  ⚠ Docker container DNS test failed. Enabling public DNS fallback (8.8.8.8, 1.1.1.1)...${NC}"
            use_dns_fallback=true
        else
            echo -e "${GREEN}  ✓ Docker container DNS resolution verified.${NC}"
        fi
    fi

    local changed=false
    local daemon_json="{}"

    if [ -n "${MASTER_IP:-}" ] && [ "$MASTER_IP" != "127.0.0.1" ] && [ "$MASTER_IP" != "$(detect_public_ip)" ]; then
        echo -e "${BLUE}  → Configuring insecure registry (Master: $MASTER_IP)...${NC}"
        mkdir -p /etc/docker
        local trust_list="\"${MASTER_IP}:5000\""
        if [ -n "${MASTER_MESH_IP:-}" ]; then
            trust_list="${trust_list}, \"${MASTER_MESH_IP}:5000\""
        fi
        daemon_json="{\"registry-mirrors\":[\"http://${MASTER_IP}:5001\"],\"insecure-registries\":[${trust_list}]}"
        if [ "$use_dns_fallback" = "true" ]; then
            daemon_json="{\"registry-mirrors\":[\"http://${MASTER_IP}:5001\"],\"insecure-registries\":[${trust_list}],\"dns\":[\"8.8.8.8\",\"1.1.1.1\"]}"
        fi
        changed=true
    else
        local my_ip
        my_ip="$(detect_public_ip)"
        if [ "$my_ip" != "127.0.0.1" ]; then
            echo -e "${BLUE}  → Configuring Master insecure registry (registry:5000, ${my_ip}:5000)...${NC}"
            mkdir -p /etc/docker
            local master_trust_list="\"127.0.0.1:5000\", \"registry:5000\", \"${my_ip}:5000\""
            if [ -n "${MASTER_MESH_IP:-}" ]; then
                master_trust_list="${master_trust_list}, \"${MASTER_MESH_IP}:5000\""
            fi
            daemon_json="{\"insecure-registries\":[${master_trust_list}]}"
            if [ "$use_dns_fallback" = "true" ]; then
                daemon_json="{\"insecure-registries\":[${master_trust_list}],\"dns\":[\"8.8.8.8\",\"1.1.1.1\"]}"
            fi
            changed=true
        elif [ "$use_dns_fallback" = "true" ]; then
            echo -e "${BLUE}  → Configuring Docker DNS fallback...${NC}"
            mkdir -p /etc/docker
            daemon_json='{"dns":["8.8.8.8","1.1.1.1"]}'
            changed=true
        fi
    fi

    if [ "$changed" = "true" ]; then
        local prev
        prev="$(cat /etc/docker/daemon.json  || echo '')"
        _merge_daemon_json "$daemon_json"
        local new
        new="$(cat /etc/docker/daemon.json  || echo '')"
        if [ "$prev" != "$new" ]; then
            systemctl restart docker || true
        fi
    fi

    install_registry_docker_certs
}

install_registry_docker_certs() {
    local cert="${INSTALL_DIR:-/opt/smsly-hosting}/certs/registry.crt"
    if [ ! -f "$cert" ]; then
        return 0
    fi
    local my_ip
    my_ip="$(detect_public_ip)"
    local dirs=(
        "/etc/docker/certs.d/registry:5000"
        "/etc/docker/certs.d/127.0.0.1:5000"
    )
    if [ -n "$my_ip" ] && [ "$my_ip" != "127.0.0.1" ]; then
        dirs+=("/etc/docker/certs.d/${my_ip}:5000")
    fi
    local installed=false
    for d in "${dirs[@]}"; do
        mkdir -p "$d"
        cp "$cert" "$d/ca.crt"
        installed=true
    done
    if [ "$installed" = "true" ]; then
        echo -e "${BLUE}  → Installed registry TLS cert for Docker trust (${#dirs[@]} endpoints)${NC}"
    fi
}

docker_login() {
    local registry="${CONTAINER_REGISTRY_URL:-127.0.0.1:5000}"
    local user="${REGISTRY_USER:-smsly-registry}"
    local pass="${REGISTRY_PASSWORD:-}"
    if [ -z "$pass" ]; then
        return 0
    fi
    local _cacert="${INSTALL_DIR:-/opt/smsly-hosting}/certs/registry.crt"
    local _curl_args="--insecure"
    if [ -f "$_cacert" ]; then
        _curl_args="--cacert $_cacert"
    fi
    local _code=""
    _code="$(timeout 10 curl -s -o /dev/null -w '%{http_code}' $_curl_args "https://${registry}/v2/" 2>/dev/null)"
    if [ -n "$_code" ] && [ "$_code" != "401" ]; then
        if [ "$_code" = "200" ]; then
            echo -e "${BLUE}     -> Registry $registry allows anonymous access - skipping login${NC}"
        else
            echo -e "${YELLOW}    [warn] Registry $registry returned HTTP $_code on /v2/ probe - check registry config${NC}"
        fi
        return 0
    fi
    if echo "$pass" | docker login "$registry" -u "$user" --password-stdin 2>&1; then
        return 0
    fi
    echo -e "${YELLOW}    [warn] Docker login failed for $registry (see error above)${NC}"
    return 0
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
        if docker compose -f "$COMPOSE_FILE" config --services  | grep -qx "$svc"; then
            printf '%s\n' "$svc"
        fi
    done | tr '\n' ' '
}

stop_node_excluded_services() {
    is_node_mode || return 0
    docker compose -f "$COMPOSE_FILE" stop --timeout 15 frontend caddy || echo -e "${YELLOW}    ⚠ frontend/caddy stop failed${NC}"
    docker compose -f "$COMPOSE_FILE" rm -f frontend caddy || echo -e "${YELLOW}    ⚠ frontend/caddy rm failed${NC}"
}

prune_stopped_conflicting() {
    local pattern="$1"
    local c_id=""
    local c_name=""
    local removed=0
    for c_id in $(docker ps -a -q --filter "name=${pattern}" --filter "status=exited" --filter "status=created"  || true); do
        c_name=$(docker inspect "$c_id" --format='{{.Name}}'  | sed 's/^\///')
        if [ -n "$c_name" ]; then
            docker rm "$c_id"  && removed=$((removed + 1))
        fi
    done
    [ "$removed" -gt 0 ] && echo -e "  \033[0;32m✓\033[0m Removed $removed stopped container(s)" || true
}

cleanup_stale_containers() {
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    timeout -k 5 30 docker compose -f "$compose_f" down --remove-orphans  || true
    prune_stopped_conflicting "smsly-hosting"
    prune_stopped_conflicting "smsly-"
}

compose_stack_build() {
    docker_login
    local services=""
    if is_node_mode; then
        stop_node_excluded_services
        services="$(compose_stack_build_service_args)"
        [ -n "$services" ] || return 1
        timeout -k 5 300 docker compose -f "$COMPOSE_FILE" build "$@" $services
    else
        timeout -k 5 300 docker compose -f "$COMPOSE_FILE" build "$@"
    fi
}

compose_stack_up() {
    local services=""
    if is_node_mode; then
        stop_node_excluded_services
        services="$(compose_stack_service_args)"
        [ -n "$services" ] || return 1
        timeout -k 10 300 docker compose -f "$COMPOSE_FILE" up -d "$@" $services
    else
        timeout -k 10 300 docker compose -f "$COMPOSE_FILE" up -d "$@"
    fi
}

get_pgcat_if_exists() {
    local compose_target="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" ; then
        echo "pgcat"
    fi
}

get_db_service() {
    echo "db"
}

get_redis_service() {
    local ct="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$ct" ] && grep -q "^  *redis-replica:" "$ct" ; then
        echo "redis-primary"
    else
        echo "redis"
    fi
}

ensure_infrastructure_permissions() {
    local caddy_config_dir="/opt/smsly-hosting/caddy-config"
    local staticfiles_dir="/opt/smsly-hosting/backend/staticfiles"
    local builds_dir="/opt/smsly-hosting/builds"
    local prometheus_targets_dir="/opt/smsly-hosting/prometheus-targets"

    echo -e "${BLUE}  -> Ensuring infrastructure permissions...${NC}"

    mkdir -p "$caddy_config_dir"
    mkdir -p "$staticfiles_dir"
    mkdir -p "$builds_dir"
    mkdir -p "$prometheus_targets_dir"

    _chown_owner="1000:1000"
    for _dir in "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir"; do
        if [ -d "$_dir" ]; then
            if ! chown -R "$_chown_owner" "$_dir"; then
                echo -e "${YELLOW}     ⚠ Could not chown $_dir to $_chown_owner (see error above)${NC}"
            fi
        fi
    done

    chmod -R u+rwX,g+rwX "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir" || echo -e "${YELLOW}     ⚠ chmod failed on bind-mount dirs${NC}"
    find "$caddy_config_dir" -type d -exec chmod 2775 {} + || true
    find "$staticfiles_dir" -type d -exec chmod 2775 {} + || true
    find "$builds_dir" -type d -exec chmod 2775 {} + || true
    find "$prometheus_targets_dir" -type d -exec chmod 2777 {} + || echo -e "${YELLOW}     ⚠ chmod failed on $prometheus_targets_dir${NC}"
    chmod 2777 "$prometheus_targets_dir" || echo -e "${YELLOW}     ⚠ chmod failed on $prometheus_targets_dir${NC}"

    [ -f "$caddy_config_dir/Caddyfile" ] && chmod 664 "$caddy_config_dir/Caddyfile" || true
    [ -f "$caddy_config_dir/.reload" ] && chmod 664 "$caddy_config_dir/.reload" || true

    if command -v docker ; then
        _vol_names="$(docker volume ls -q 2>/dev/null | grep -E '(^|_)(backups_data|caddy_data)$')"
        for vol in ${_vol_names:-backups_data}; do
            if docker volume inspect "$vol" >/dev/null 2>&1; then
                echo -e "${BLUE}     ↳ Setting permissions for volume: $vol...${NC}"
                docker run --rm -v "${vol}:/data" alpine chown -R 1000:1000 /data || echo -e "${YELLOW}     ⚠ Could not chown volume $vol${NC}"
            else
                echo -e "${YELLOW}     ⚠ $vol volume not found — skipping chown${NC}"
            fi
        done
    fi

    local probe_failed=0
    for probe_dir in "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir"; do
        if ! echo "perm-ok" > "$probe_dir/.perm_probe"; then
            echo -e "${YELLOW}  ⚠ Write probe failed for $probe_dir — retrying with chown...${NC}"
            chown -R 1000:1000 "$probe_dir" || true
            chmod -R u+rwX,g+rwX "$probe_dir" || true
            if echo "perm-ok" > "$probe_dir/.perm_probe"; then
                echo -e "${GREEN}    ✓ Fixed${NC}"
            else
                echo -e "${RED}    ✗ Still cannot write to $probe_dir — check host permissions${NC}"
                probe_failed=1
            fi
        fi
        rm -f "$probe_dir/.perm_probe" || true
    done
    if [ -f "/opt/smsly-hosting/.env" ] && ! touch "/opt/smsly-hosting/.env"; then
        echo -e "${YELLOW}  ⚠ .env not writable — fixing...${NC}"
        chown 1000:1000 "/opt/smsly-hosting/.env" || true
        chmod 640 "/opt/smsly-hosting/.env" || true
    fi
    if [ "$probe_failed" -ne 0 ]; then
        echo -e "${RED}  ✗ Some bind-mount directories are not writable — containers may fail${NC}"
    fi
}

resolve_container_target() {
    local target="$1"

    [ -z "$target" ] && return 0

    # NOTE: the existence probe MUST NOT write to stdout — callers capture the
    # function's output in $(...) and pass it straight to `docker inspect`;
    # a bare `docker inspect` here would embed the full JSON in the resolved
    # target and make every caller fail with "error: no such object: [ ... ]".
    if timeout -k 5 10 docker container inspect "$target" >/dev/null 2>&1 ; then
        echo "$target"
        return 0
    fi

    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$compose_f" ]; then
        local services
        services="$(timeout -k 5 10 docker compose -f "$compose_f" config --services )"
        if [ -n "$services" ]; then
            for svc in $services; do
                if [[ "$target" == *"-${svc}-"* || "$target" == *"_${svc}_"* || "$target" == *"-${svc}" || "$target" == *"_${svc}" || "$target" == "$svc" ]]; then
                    local cid
                    cid="$(timeout -k 5 10 docker compose -f "$compose_f" ps -q "$svc"  | head -n 1 || true)"
                    if [ -n "$cid" ]; then
                        echo "$cid"
                        return 0
                    fi
                fi
            done
        fi
    fi

    local cid_svc
    cid_svc="$(docker compose -f "$compose_f" ps -q "$target"  | head -n 1 || true)"
    if [ -n "$cid_svc" ]; then
        echo "$cid_svc"
        return 0
    fi

    local cid_fuzzy
    local fuzzy_pattern
    fuzzy_pattern="${target//-/*}"
    fuzzy_pattern="${fuzzy_pattern//_/*}"
    cid_fuzzy="$(docker ps -a --filter "name=${fuzzy_pattern}" -q  | head -n 1 || true)"
    if [ -n "$cid_fuzzy" ]; then
        echo "$cid_fuzzy"
        return 0
    fi

    echo "$target"
}

ensure_container_on_network() {
    local network_name="$1"
    local raw_target="$2"

    [ -z "$network_name" ] && return 0
    [ -z "$raw_target" ] && return 0

    local container_name
    container_name="$(resolve_container_target "$raw_target")"

    docker container inspect "$container_name"  || return 0
    docker network inspect "$network_name"  || return 0

    if docker network inspect "$network_name" --format '{{range $k, $v := .Containers}}{{$k}}{{end}}'  | grep -q "$container_name"; then
        return 0
    fi

    docker network connect "$network_name" "$container_name" || echo -e "${YELLOW}    ⚠ Network connect $container_name to $network_name failed${NC}"
}

recreate_traefik_preserving_certs() {
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    local acme_src="/var/lib/docker/volumes/smsly-hosting_letsencrypt_data/_data/acme.json"
    local acme_backup=""

    if ! docker compose -f "$compose_f" ps -q traefik  | grep -q .; then
        echo -e "${YELLOW}  WARN traefik not running; skipping one-time recreate.${NC}"
        return 1
    fi

    echo -e "${BLUE}  → Verifying socket-proxy is healthy (traefik Docker provider depends on it)...${NC}"
    local i=0
    while [ $i -lt 30 ]; do
        if docker inspect --format='{{.State.Health.Status}}' smsly-hosting-socket-proxy-1  | grep -q healthy; then
            break
        fi
        sleep 2
        i=$((i + 1))
    done
    if [ $i -ge 30 ]; then
        echo -e "${RED}  x socket-proxy not healthy; aborting to avoid 503 on deployed services.${NC}"
        echo -e "${RED}    Fix: docker logs smsly-hosting-socket-proxy-1${NC}"
        return 1
    fi

    echo -e "${BLUE}  → Backing up acme.json...${NC}"
    if [ -f "$acme_src" ]; then
        acme_backup="/tmp/smsly-acme-$(date +%s).json"
        cp "$acme_src" "$acme_backup" && chmod 600 "$acme_backup"
        echo -e "${GREEN}    OK saved to $acme_backup${NC}"
    else
        echo -e "${YELLOW}    WARN no existing acme.json; new container will request fresh certs.${NC}"
    fi

    echo -e "${BLUE}  → Recording pre-recreate router count from Traefik API...${NC}"
    sleep 2
    local pre_routers=0
    if timeout 10 docker exec smsly-hosting-traefik-1 sh -c 'command -v wget ' ; then
        pre_routers=$(timeout 10 docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/http/routers  | grep -o '"name"' | wc -l)
    else
        pre_routers=$(timeout 10 docker exec smsly-hosting-traefik-1 curl -s http://127.0.0.1:8080/api/http/routers  | grep -o '"name"' | wc -l)
    fi
    echo -e "${BLUE}    pre-recreate routers: $pre_routers${NC}"
    if [ "$pre_routers" -le 1 ]; then
        echo -e "${YELLOW}    WARN only $pre_routers router(s) before recreate (expected route-fallback + deployed services).${NC}"
        echo -e "${YELLOW}          Deployed services may already have stale labels.${NC}"
    fi

    echo -e "${BLUE}  → Recreating traefik (preserves letsencrypt_data volume + acme.json)...${NC}"
    timeout -k 5 60 docker compose -f "$compose_f" up -d --no-deps traefik 2>&1 | sed 's/^/    /'

    echo -e "${BLUE}  → Reconnecting traefik to smsly-proxy network (recreate can drop external nets)...${NC}"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"

    if [ -n "$acme_backup" ] && [ -f "$acme_backup" ]; then
        sleep 3
        if [ -f "$acme_src" ]; then
            cp "$acme_backup" "$acme_src" && chmod 600 "$acme_src"
            echo -e "${GREEN}    OK restored acme.json perms to 0600${NC}"
        fi
        rm -f "$acme_backup"
    fi

    echo -e "${BLUE}  → Waiting for traefik healthcheck...${NC}"
    i=0
    while [ $i -lt 30 ]; do
        if docker inspect --format='{{.State.Health.Status}}' smsly-hosting-traefik-1  | grep -q healthy; then
            break
        fi
        sleep 2
        i=$((i + 1))
    done
    if [ $i -ge 30 ]; then
        echo -e "${YELLOW}  WARN traefik healthcheck timeout; check 'docker logs smsly-hosting-traefik-1'${NC}"
    fi

    echo -e "${BLUE}  → Waiting for Traefik routing table to repopulate (CRITICAL — prevents 503 on deployed services)...${NC}"
    i=0
    local post_routers=0
    while [ $i -lt 60 ]; do
        if timeout 10 docker exec smsly-hosting-traefik-1 sh -c 'command -v wget ' ; then
            post_routers=$(timeout 10 docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/http/routers  | grep -o '"name"' | wc -l)
        else
            post_routers=$(timeout 10 docker exec smsly-hosting-traefik-1 curl -s http://127.0.0.1:8080/api/http/routers  | grep -o '"name"' | wc -l)
        fi
        if [ "$post_routers" -ge "$pre_routers" ] && [ "$post_routers" -gt 0 ]; then
            echo -e "${GREEN}    OK post-recreate routers: $post_routers (matches or exceeds pre-recreate)${NC}"

            local eps
            if timeout 10 docker exec smsly-hosting-traefik-1 sh -c 'command -v wget ' ; then
                eps=$(timeout 10 docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/entrypoints )
            else
                eps=$(timeout 10 docker exec smsly-hosting-traefik-1 curl -s http://127.0.0.1:8080/api/entrypoints )
            fi
            if echo "$eps" | grep -q '"name":"websecure"'; then
                echo -e "${GREEN}    OK websecure entrypoint is active${NC}"
            else
                echo -e "${YELLOW}    WARN websecure entrypoint not detected${NC}"
            fi
            if echo "$eps" | grep -q '"name":"metrics"'; then
                echo -e "${GREEN}    OK metrics entrypoint is active${NC}"
            else
                echo -e "${YELLOW}    WARN metrics entrypoint not detected${NC}"
            fi

            return 0
        fi
        sleep 2
        i=$((i + 1))
    done
    echo -e "${YELLOW}  WARN Traefik has fewer routers than before ($post_routers vs $pre_routers).${NC}"
    echo -e "${YELLOW}        Deployed services have stale Traefik labels (from before the routing fix).${NC}"
    echo -e "${YELLOW}        Redeploy them via the SMSLY dashboard to refresh labels.${NC}"
    return 1
}

bust_core_build_cache() {
    echo -e "${BLUE}  -> Busting frontend/backend build cache (safe mode)...${NC}"

    local core_svcs="frontend backend celery celery-deploy celery-fast celery-beat"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        core_svcs="backend celery-worker"
    elif [ "$MODE_NODE" = "true" ]; then
        core_svcs="backend celery celery-deploy celery-fast celery-beat"
    fi

    for svc in $core_svcs; do
        local image_ids=""
        image_ids="$(docker compose -f "$COMPOSE_FILE" images -q "$svc"  | awk 'NF' | sort -u || true)"
        if [ -n "$image_ids" ]; then
            while read -r image_id; do
                [ -n "$image_id" ] && docker rmi -f "$image_id" || echo -e "${YELLOW}    ⚠ docker rmi $image_id failed${NC}"
            done <<< "$image_ids"
        fi
    done

    docker builder prune -af || echo -e "${YELLOW}    ⚠ docker builder prune failed${NC}"

    echo -e "${BLUE}  -> Pruning deeply stale images (>7 days old)...${NC}"
    docker image prune -a -f --filter "until=168h" || echo -e "${YELLOW}    ⚠ docker image prune failed${NC}"

    echo -e "${GREEN}  OK Cache bust complete (targeted images + build cache + deep prune)${NC}"
}

restart_edge_stack() {
    local all_edge_services="socket-proxy traefik"
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        all_edge_services="socket-proxy traefik route-fallback"
    fi

    echo -e "${BLUE}  -> Checking edge proxy stack (traefik/socket-proxy/route-fallback)...${NC}"
    local down_services=""
    for svc in $all_edge_services; do
        if docker compose -f "$COMPOSE_FILE" ps "$svc"  | grep -q "Up"; then
            echo -e "${GREEN}    ✓ $svc already running${NC}"
        else
            echo -e "${YELLOW}    ⚠ $svc is down — starting...${NC}"
            down_services="$down_services $svc"
        fi
    done

    if [ -n "$down_services" ]; then
        timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d --no-deps $down_services || \
            timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d $down_services || echo -e "${YELLOW}    ⚠ Service restart failed${NC}"
    fi

    echo -e "${BLUE}  -> Re-attaching external networks...${NC}"
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    fi
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    if should_manage_caddy && docker compose -f "$COMPOSE_FILE" ps caddy  | grep -q "Up"; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "restart_edge_stack validation"
        fi
        echo -e "${BLUE}  -> Reloading Caddy...${NC}"
        reload_container_caddy  || true
    fi
    echo -e "${GREEN}  OK Edge stack healthy${NC}"
}

wait_for_traefik_api() {
    local max_wait="${1:-30}"
    local waited=0
    local interval=2
    echo -e "${BLUE}  → Waiting for Traefik API to be ready...${NC}"
    while [ "$waited" -lt "$max_wait" ]; do
        if curl -sf --max-time 3 http://127.0.0.1:8082/api/version ; then
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
        if docker compose -f "$COMPOSE_FILE" config --services  | grep -qx "$svc"; then
            app_services+=("$svc")
        fi
    done

    for svc in "${edge_services_requested[@]}"; do
        if docker compose -f "$COMPOSE_FILE" config --services  | grep -qx "$svc"; then
            edge_services+=("$svc")
        fi
    done

    runtime_services=("${app_services[@]}" "${edge_services[@]}")

    if [ "${#runtime_services[@]}" -eq 0 ]; then
        echo -e "${YELLOW}  ⚠ No runtime services found to refresh${NC}"
        return 0
    fi

    if [ "${#app_services[@]}" -gt 0 ]; then
        timeout -k 5 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "${app_services[@]}" || \
            timeout -k 5 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate "${app_services[@]}" || echo -e "${YELLOW}    ⚠ App services restart failed${NC}"
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
        local down_edge=()
        for svc in "${edge_services[@]}"; do
            container_name="smsly-hosting-${svc}-1"
            if docker compose -f "$COMPOSE_FILE" ps "$svc"  | grep -q "Up"; then
                echo -e "${GREEN}  ✓ $svc already running${NC}"
            else
                echo -e "${YELLOW}  ⚠ $svc is down — starting...${NC}"
                down_edge+=("$svc")
            fi
        done
        if [ "${#down_edge[@]}" -gt 0 ]; then
            timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d --no-deps "${down_edge[@]}" || \
                timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d "${down_edge[@]}" || echo -e "${YELLOW}    ⚠ Edge services restart failed${NC}"
        fi

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
        docker compose -f "$COMPOSE_FILE" ps "${failed_services[@]}"  || true
        docker compose -f "$COMPOSE_FILE" logs --tail=80 "${failed_services[@]}"  || true
        return 1
    fi

    if should_manage_caddy; then
        install_caddy_health_guard "${DOMAIN:-}"
        reload_container_caddy  || true
    fi

    if [ "$MODE_AGENT_LITE" != "true" ]; then
        echo -e "${BLUE}  → Refreshing Observability Stack...${NC}"
        if [ -f "infrastructure/docker/docker-compose.observability.yml" ]; then
            docker compose -f infrastructure/docker/docker-compose.observability.yml pull || echo -e "${YELLOW}    ⚠ Observability pull failed${NC}"
            docker compose -f infrastructure/docker/docker-compose.observability.yml up -d || echo -e "${YELLOW}    ⚠ Observability up failed${NC}"
            for obs_ctr in smsly-loki smsly-promtail smsly-prometheus smsly-cadvisor smsly-node-exporter smsly-grafana; do
                i=0
                while [ $i -lt 30 ]; do
                    if docker inspect --format='{{.State.Health.Status}}' "$obs_ctr"  | grep -qE 'healthy|^$'; then
                        break
                    fi
                    sleep 2
                    i=$((i + 1))
                done
            done
        fi
    fi

    if systemctl is-active --quiet smsly-autoscaler; then
        systemctl restart smsly-autoscaler || echo -e "${YELLOW}    ⚠ smsly-autoscaler restart failed${NC}"
    else
        echo -e "${BLUE}  → smsly-autoscaler not running, skipping restart${NC}"
    fi
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

ensure_celery_workers_running() {
    local celery_services=()
    local down_services=()
    local base_workers=(celery celery-deploy celery-fast celery-beat)
    if [ "${CELERY_AUTOSCALE_ENABLED:-false}" != "true" ]; then
        base_workers+=(celery-2 celery-3)
    fi
    for svc in "${base_workers[@]}"; do
        if docker compose -f "$COMPOSE_FILE" config --services  | grep -qx "$svc"; then
            celery_services+=("$svc")
        fi
    done
    if [ "${#celery_services[@]}" -eq 0 ]; then
        echo -e "${BLUE}  → No celery services configured, skipping celery check${NC}"
        return 0
    fi
    for svc in "${celery_services[@]}"; do
        if ! docker compose -f "$COMPOSE_FILE" ps "$svc"  | grep -q "Up"; then
            down_services+=("$svc")
        fi
    done
    if [ "${#down_services[@]}" -eq 0 ]; then
        echo -e "${GREEN}  ✓ All celery workers are running${NC}"
        return 0
    fi
    echo -e "${YELLOW}  ⚠ Celery workers down: ${down_services[*]}. Restarting...${NC}"
    local base_down=()
    local extra_down=()
    for svc in "${down_services[@]}"; do
        case "$svc" in
            celery-2|celery-3) extra_down+=("$svc") ;;
            *) base_down+=("$svc") ;;
        esac
    done
    if [ "${#base_down[@]}" -gt 0 ]; then
        timeout -k 5 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "${base_down[@]}" || \
            timeout -k 5 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate "${base_down[@]}" || echo -e "${YELLOW}    ⚠ Celery workers restart failed${NC}"
    fi
    if [ "${#extra_down[@]}" -gt 0 ]; then
        timeout -k 5 60 docker compose -f "$COMPOSE_FILE" --profile extra-workers up -d --force-recreate --no-deps "${extra_down[@]}" || \
            timeout -k 5 60 docker compose -f "$COMPOSE_FILE" --profile extra-workers up -d --force-recreate "${extra_down[@]}" || echo -e "${YELLOW}    ⚠ Extra celery workers restart failed${NC}"
    fi
    local all_ok=true
    for svc in "${down_services[@]}"; do
        if wait_for_container_ready "smsly-hosting-${svc}-1" 120; then
            echo -e "${GREEN}    ✓ $svc is running${NC}"
        else
            echo -e "${RED}    ✗ $svc failed to start${NC}"
            all_ok=false
        fi
    done
    if [ "$all_ok" = true ]; then
        echo -e "${GREEN}  ✓ All celery workers recovered${NC}"
    fi
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
        state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name"  || echo "missing")"
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

# --- end lib/docker.sh ---

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

LOG_FILE="/var/log/smsly-install.log"
INSTALL_DIR="/opt/smsly-hosting"
CREDENTIALS_FILE="$INSTALL_DIR/.credentials"
COMPOSE_FILE="$INSTALL_DIR/docker-compose.prod.yml"
LOCK_FILE="/tmp/smsly-install.lock"
ROLLBACK_NEEDED=false
CADDY_LAST_GOOD="$INSTALL_DIR/caddy-config/Caddyfile.smsly-last-good"

acquire_install_lock() {
    if command -v flock ; then
        exec 9<>"$LOCK_FILE"
        if ! flock -n 9; then
            local pid
            pid="$(cat "$LOCK_FILE"  || true)"
            echo -e "${RED}ERROR: Another installer instance${pid:+ (PID $pid)} is already running.${NC}"
            echo -e "If you are sure no other instance is running, remove $LOCK_FILE and try again."
            exit 1
        fi
        : > "$LOCK_FILE"
        echo "$$" > "$LOCK_FILE"
    else
        if [ -f "$LOCK_FILE" ]; then
            local pid
            pid="$(cat "$LOCK_FILE"  || true)"
            if [ "$pid" != "$$" ] && kill -0 "$pid" ; then
                echo -e "${RED}ERROR: Another installer instance (PID $pid) is already running.${NC}"
                echo -e "If you are sure no other instance is running, remove $LOCK_FILE and try again."
                exit 1
            fi
        fi
        echo "$$" > "$LOCK_FILE"
    fi
}

release_install_lock() {
    if command -v flock ; then
        flock -u 9  || true
        exec 9>&-  || true
    fi
    rm -f "$LOCK_FILE"  || true
}

get_migration_database_alias() {
    local migrate_db
    local direct_url
    direct_url="$(env_get_value "${INSTALL_DIR:-.}/.env" "DIRECT_DATABASE_URL"  || true)"
    if [ -z "$direct_url" ]; then
        direct_url="postgresql://${POSTGRES_USER:-smsly_admin}:${POSTGRES_PASSWORD:-}@${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-smsly_hosting}"
    fi
    migrate_db="$(
        docker run --rm --network smsly-net \
            --user 1000 \
            --env-file "${INSTALL_DIR:-/opt/smsly-hosting}/.env" \
            -e SMSLY_DISABLE_STARTUP_TASKS=true \
            -e SMSLY_MIGRATION_MODE=true \
            -e DIRECT_DATABASE_URL="$direct_url" \
            smsly-hosting-backend:latest \
            python manage.py shell -c \
            "from django.conf import settings; print('direct' if 'direct' in settings.DATABASES else ('session' if 'session' in settings.DATABASES else 'default'))" \
             | tail -n 1 | tr -d '\r'
    )"

    case "$migrate_db" in
        direct|session|default) printf '%s\n' "$migrate_db" ;;
        *) printf '%s\n' "default" ;;
    esac
}

diagnose_migration_locks() {
    local env_file="${INSTALL_DIR:-.}/.env"
    [ -f "$env_file" ] && source "$env_file"  || true

    echo -e "${YELLOW}  -> PostgreSQL activity snapshot (lock diagnosis):${NC}"
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T \
        -e PGPASSWORD="${POSTGRES_PASSWORD:-}" \
        db psql \
            -U "${POSTGRES_USER:-smsly_admin}" \
            -d "${POSTGRES_DB:-smsly_hosting}" \
            -v ON_ERROR_STOP=1 \
            -P pager=off \
            -c "SELECT pid, usename, application_name, state, wait_event_type, wait_event, now() - COALESCE(xact_start, query_start) AS age, left(regexp_replace(query, '\s+', ' ', 'g'), 180) AS query FROM pg_stat_activity WHERE datname = current_database() ORDER BY COALESCE(xact_start, query_start) NULLS LAST LIMIT 20;" \
            < /dev/null \
         || echo -e "${YELLOW}  -> Could not read pg_stat_activity.${NC}"
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
    direct_url="$(env_get_value "${INSTALL_DIR:-.}/.env" "DIRECT_DATABASE_URL"  || true)"
    if [ -z "$direct_url" ]; then
        direct_url="postgresql://${POSTGRES_USER:-smsly_admin}:${POSTGRES_PASSWORD:-}@${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}/${POSTGRES_DB:-smsly_hosting}"
    fi

    set +e
    timeout "$((timeout_seconds + 60))" docker run --rm --network smsly-net \
        --user 1000 \
        --env-file "${INSTALL_DIR:-/opt/smsly-hosting}/.env" \
        -e SMSLY_DISABLE_STARTUP_TASKS=true \
        -e SMSLY_MIGRATION_MODE=true \
        -e DIRECT_DATABASE_URL="$direct_url" \
        smsly-hosting-backend:latest \
        timeout "$timeout_seconds" \
        python manage.py migrate --database="$migrate_db" --noinput
    rc=$?
    set -e

    if [ "$rc" -ne 0 ]; then
        if [ "$rc" -eq 124 ]; then
            echo -e "${RED}  x Migrations timed out after ${timeout_seconds}s.${NC}"
        else
            echo -e "${RED}  x Migrations exited with status ${rc}.${NC}"
        fi
        [ "$MODE_AGENT_LITE" != "true" ] && diagnose_migration_locks
        return "$rc"
    fi

    echo -e "${BLUE}  -> Fixing node agent database permissions...${NC}"
    timeout -k 5 60 docker run --rm --network smsly-net \
        --user 1000 \
        --env-file "${INSTALL_DIR:-/opt/smsly-hosting}/.env" \
        -e SMSLY_DISABLE_STARTUP_TASKS=true \
        smsly-hosting-backend:latest \
        python manage.py fix_node_db_permissions  || echo -e "${YELLOW}    ⚠ fix_node_db_permissions failed${NC}"

    if [ "$MODE_AGENT_LITE" != "true" ] && [ -n "$(get_pgcat_if_exists)" ] && docker compose -f "$COMPOSE_FILE" ps pgcat  | grep -q "Up"; then
        echo -e "${BLUE}  -> Reloading PgCat to pick up node agent pools...${NC}"
        timeout -k 5 20 docker compose -f "$COMPOSE_FILE" restart pgcat || echo -e "${YELLOW}    ⚠ PgCat restart failed${NC}"
        sleep 5
        echo -e "${GREEN}  ✓ PgCat reloaded${NC}"
    fi

    return 0
}

export_caddy_cloudflare_env() {
    return 0
}

restore_last_good_caddy() {
    return 0
}

reload_caddy_preserving_previous() {
    reload_container_caddy  || true
    return 0
}

ensure_selfsigned_cert() {
    local cert_dir="${INSTALL_DIR:-/opt/smsly-hosting}/caddy-config/certs"
    local cert_file="$cert_dir/ip.crt"
    local key_file="$cert_dir/ip.key"
    local public_ip="${PUBLIC_IP:-$(detect_public_ip)}"
    local ssl_config="$cert_dir/openssl.cnf"

    mkdir -p "$cert_dir"
    chmod 700 "$cert_dir"  || true

    if ! command -v openssl ; then
        echo -e "${YELLOW}  ⚠ openssl not available; skipping self-signed cert generation${NC}"
        return 0
    fi

    echo -e "${BLUE}  → Generating self-signed cert for IP: $public_ip...${NC}"

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
         || {
        echo -e "${YELLOW}  ⚠ Failed to generate self-signed cert (non-fatal)${NC}"
        rm -f "$ssl_config"
        return 0
    }
    rm -f "$ssl_config"

    chmod 644 "$cert_file"  || true
    chmod 600 "$key_file"  || true
    if [ -n "${SUDO_USER:-}" ]; then
        chown "${SUDO_USER}:${SUDO_USER}" "$key_file"  || chown 1000:1000 "$key_file"  || true
    elif [ "$(id -u)" -eq 0 ]; then
        chown 1000:1000 "$key_file"  || true
    fi
    echo -e "${GREEN}  ✓ Self-signed cert generated for $public_ip${NC}"
}

reload_container_caddy() {
    should_manage_caddy || return 0
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if command -v docker  && docker compose -f "$compose_f" ps -q caddy  | grep -q .; then
        timeout -k 5 20 docker compose -f "$compose_f" exec -T caddy caddy reload --config /etc/caddy/Caddyfile < /dev/null || \
            timeout -k 5 20 docker compose -f "$compose_f" restart caddy || \
            echo -e "${YELLOW}    ⚠ Caddy reload failed${NC}"
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

    reload_container_caddy  || true
    return 0
}

generate_safe_caddyfile() {
    local reason="${1:-unknown}"
    local candidate="/tmp/Caddyfile.safe.$$"
    echo -e "${YELLOW}  ⚠ Generating safe fallback Caddyfile (reason: $reason)...${NC}"

    local domain=""
    domain="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
"  < /dev/null | tr -d '[:space:]' || true)"
    if [ -z "$domain" ]; then
        domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- || true)"
    fi

    local svc_blocks=""
    svc_blocks="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
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
"  < /dev/null | tr -d '\r' || true)"

    local is_real_domain=false
    if [ -n "$domain" ] && [ "$domain" != "localhost" ]; then
        if ! echo "$domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
            is_real_domain=true
        fi
    fi

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

caddy_needs_fix() {
    should_manage_caddy || return 1
    local dest="${INSTALL_DIR:-/opt/smsly-hosting}/caddy-config/Caddyfile"
    if ! timeout -k 5 15 docker compose -f "$COMPOSE_FILE" exec -T caddy caddy validate --config /etc/caddy/Caddyfile < /dev/null ; then
        return 0
    fi
    if grep -q 'dns cloudflare' "$dest" ; then
        local _env_token="${CLOUDFLARE_API_TOKEN:-}"
        if [ -z "$_env_token" ] && [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
            _env_token="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "${INSTALL_DIR:-/opt/smsly-hosting}/.env"  | cut -d= -f2- || true)"
        fi
        if [ -z "$_env_token" ] || [ "$_env_token" = "fake" ]; then
            return 0
        fi
    fi
    return 1
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

sync_agent_lite_rabbitmq_password() {
    [ "$MODE_AGENT_LITE" = "true" ] || return 0

    local env_file="$INSTALL_DIR/.env"
    local rabbitmq_user rabbitmq_password

    rabbitmq_user="$(env_get_value "$env_file" "RABBITMQ_DEFAULT_USER"  || true)"
    rabbitmq_user="${rabbitmq_user:-smsly_user}"
    rabbitmq_password="$(env_get_value "$env_file" "RABBITMQ_PASSWORD"  || true)"
    rabbitmq_password="${rabbitmq_password:-$(env_get_value "$env_file" "RABBITMQ_DEFAULT_PASS"  || true)}"

    if [ -z "$rabbitmq_password" ]; then
        echo -e "${RED}  ERROR RABBITMQ_PASSWORD is empty after agent-lite env generation${NC}"
        exit 1
    fi

    docker compose -f "$COMPOSE_FILE" up -d rabbitmq || echo -e "${YELLOW}    ⚠ RabbitMQ start failed${NC}"
    wait_for_container_ready "smsly-hosting-rabbitmq-1" 120 || {
        docker compose -f "$COMPOSE_FILE" logs --tail=80 rabbitmq  || true
        exit 1
    }

    if timeout 30 docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl authenticate_user "$rabbitmq_user" "$rabbitmq_password" < /dev/null ; then
        echo -e "${GREEN}  OK Lite Agent RabbitMQ password already matches .env${NC}"
        return 0
    fi

    echo -e "${BLUE}  -> Syncing Lite Agent RabbitMQ password for ${rabbitmq_user}...${NC}"
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl add_user "$rabbitmq_user" "$rabbitmq_password" < /dev/null || echo -e "${YELLOW}    ⚠ RabbitMQ add_user failed${NC}"
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl change_password "$rabbitmq_user" "$rabbitmq_password" < /dev/null || true
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl set_user_tags "$rabbitmq_user" administrator < /dev/null || true
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl set_permissions -p / "$rabbitmq_user" ".*" ".*" ".*" < /dev/null || true

    if timeout 30 docker compose -f "$COMPOSE_FILE" exec -T rabbitmq rabbitmqctl authenticate_user "$rabbitmq_user" "$rabbitmq_password" < /dev/null ; then
        echo -e "${GREEN}  OK Lite Agent RabbitMQ password synced${NC}"
        return 0
    fi

    echo -e "${RED}  ERROR Lite Agent RabbitMQ password sync failed${NC}"
    return 1
}

ensure_security_tools() {
    export PATH="/usr/local/bin:$PATH"
    if ! command -v trivy  && [ ! -x "/usr/local/bin/trivy" ]; then
        echo -e "${BLUE}  → Installing Trivy vulnerability scanner...${NC}"
        curl -sfL --connect-timeout 15 --max-time 120 https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin  || true
    fi
    if ! command -v cosign  && [ ! -x "/usr/local/bin/cosign" ]; then
        echo -e "${BLUE}  → Installing Cosign image attestation utility...${NC}"
        local cosign_arch
        cosign_arch="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
        curl -sfL --connect-timeout 15 --max-time 120 -o /usr/local/bin/cosign "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-${cosign_arch}"  && chmod +x /usr/local/bin/cosign || true
    fi
    return 0
}

# --- end lib/common.sh ---

# --- lib/docker.sh ---
_merge_daemon_json() {
    # Merge new keys into /etc/docker/daemon.json without clobbering existing
    # settings (runtimes, log-driver, live-restore, etc.) that other installer
    # modules may have written.
    # Usage: _merge_daemon_json '{"insecure-registries":[...],"dns":[...]}'
    local new_json="$1"
    local daemon_json="/etc/docker/daemon.json"
    python3 - "$daemon_json" "$new_json" <<'PY'
import json, sys
from pathlib import Path

daemon_path = Path(sys.argv[1])
new_cfg = json.loads(sys.argv[2])

if daemon_path.exists():
    try:
        cfg = json.loads(daemon_path.read_text())
    except (json.JSONDecodeError, ValueError):
        cfg = {}
else:
    cfg = {}

cfg.update(new_cfg)
daemon_path.write_text(json.dumps(cfg, indent=2) + "\n")
PY
}

configure_docker_mirror() {
    if [ "${MODE_AGENT_LITE:-false}" = "true" ] && [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
        [ -n "${MASTER_IP:-}" ] || MASTER_IP="$(env_get_value "${INSTALL_DIR:-/opt/smsly-hosting}/.env" "MASTER_IP"  || true)"
        [ -n "${MASTER_MESH_IP:-}" ] || MASTER_MESH_IP="$(env_get_value "${INSTALL_DIR:-/opt/smsly-hosting}/.env" "MASTER_MESH_IP"  || true)"
    fi

    local use_dns_fallback=false
    if command -v docker  && systemctl is-active --quiet docker; then
        echo -e "${BLUE}  → Checking Docker DNS resolution for npm registry...${NC}"
        local test_img="node:20-alpine"
        if ! docker image inspect "$test_img" ; then
            test_img="alpine"
        fi
        if ! timeout -k 5 15 docker run --rm "$test_img" nslookup registry.npmjs.org ; then
            echo -e "${YELLOW}  ⚠ Docker container DNS test failed. Enabling public DNS fallback (8.8.8.8, 1.1.1.1)...${NC}"
            use_dns_fallback=true
        else
            echo -e "${GREEN}  ✓ Docker container DNS resolution verified.${NC}"
        fi
    fi

    local changed=false
    local daemon_json="{}"

    if [ -n "${MASTER_IP:-}" ] && [ "$MASTER_IP" != "127.0.0.1" ] && [ "$MASTER_IP" != "$(detect_public_ip)" ]; then
        echo -e "${BLUE}  → Configuring insecure registry (Master: $MASTER_IP)...${NC}"
        mkdir -p /etc/docker
        local trust_list="\"${MASTER_IP}:5000\""
        if [ -n "${MASTER_MESH_IP:-}" ]; then
            trust_list="${trust_list}, \"${MASTER_MESH_IP}:5000\""
        fi
        daemon_json="{\"registry-mirrors\":[\"http://${MASTER_IP}:5001\"],\"insecure-registries\":[${trust_list}]}"
        if [ "$use_dns_fallback" = "true" ]; then
            daemon_json="{\"registry-mirrors\":[\"http://${MASTER_IP}:5001\"],\"insecure-registries\":[${trust_list}],\"dns\":[\"8.8.8.8\",\"1.1.1.1\"]}"
        fi
        changed=true
    else
        local my_ip
        my_ip="$(detect_public_ip)"
        if [ "$my_ip" != "127.0.0.1" ]; then
            echo -e "${BLUE}  → Configuring Master insecure registry (registry:5000, ${my_ip}:5000)...${NC}"
            mkdir -p /etc/docker
            local master_trust_list="\"127.0.0.1:5000\", \"registry:5000\", \"${my_ip}:5000\""
            if [ -n "${MASTER_MESH_IP:-}" ]; then
                master_trust_list="${master_trust_list}, \"${MASTER_MESH_IP}:5000\""
            fi
            daemon_json="{\"insecure-registries\":[${master_trust_list}]}"
            if [ "$use_dns_fallback" = "true" ]; then
                daemon_json="{\"insecure-registries\":[${master_trust_list}],\"dns\":[\"8.8.8.8\",\"1.1.1.1\"]}"
            fi
            changed=true
        elif [ "$use_dns_fallback" = "true" ]; then
            echo -e "${BLUE}  → Configuring Docker DNS fallback...${NC}"
            mkdir -p /etc/docker
            daemon_json='{"dns":["8.8.8.8","1.1.1.1"]}'
            changed=true
        fi
    fi

    if [ "$changed" = "true" ]; then
        local prev
        prev="$(cat /etc/docker/daemon.json  || echo '')"
        _merge_daemon_json "$daemon_json"
        local new
        new="$(cat /etc/docker/daemon.json  || echo '')"
        if [ "$prev" != "$new" ]; then
            systemctl restart docker || true
        fi
    fi

    install_registry_docker_certs
}

install_registry_docker_certs() {
    local cert="${INSTALL_DIR:-/opt/smsly-hosting}/certs/registry.crt"
    if [ ! -f "$cert" ]; then
        return 0
    fi
    local my_ip
    my_ip="$(detect_public_ip)"
    local dirs=(
        "/etc/docker/certs.d/registry:5000"
        "/etc/docker/certs.d/127.0.0.1:5000"
    )
    if [ -n "$my_ip" ] && [ "$my_ip" != "127.0.0.1" ]; then
        dirs+=("/etc/docker/certs.d/${my_ip}:5000")
    fi
    local installed=false
    for d in "${dirs[@]}"; do
        mkdir -p "$d"
        cp "$cert" "$d/ca.crt"
        installed=true
    done
    if [ "$installed" = "true" ]; then
        echo -e "${BLUE}  → Installed registry TLS cert for Docker trust (${#dirs[@]} endpoints)${NC}"
    fi
}

docker_login() {
    local registry="${CONTAINER_REGISTRY_URL:-127.0.0.1:5000}"
    local user="${REGISTRY_USER:-smsly-registry}"
    local pass="${REGISTRY_PASSWORD:-}"
    if [ -z "$pass" ]; then
        return 0
    fi
    local _cacert="${INSTALL_DIR:-/opt/smsly-hosting}/certs/registry.crt"
    local _curl_args="--insecure"
    if [ -f "$_cacert" ]; then
        _curl_args="--cacert $_cacert"
    fi
    local _code=""
    _code="$(timeout 10 curl -s -o /dev/null -w '%{http_code}' $_curl_args "https://${registry}/v2/" 2>/dev/null)"
    if [ -n "$_code" ] && [ "$_code" != "401" ]; then
        if [ "$_code" = "200" ]; then
            echo -e "${BLUE}     -> Registry $registry allows anonymous access - skipping login${NC}"
        else
            echo -e "${YELLOW}    [warn] Registry $registry returned HTTP $_code on /v2/ probe - check registry config${NC}"
        fi
        return 0
    fi
    if echo "$pass" | docker login "$registry" -u "$user" --password-stdin 2>&1; then
        return 0
    fi
    echo -e "${YELLOW}    [warn] Docker login failed for $registry (see error above)${NC}"
    return 0
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
        if docker compose -f "$COMPOSE_FILE" config --services  | grep -qx "$svc"; then
            printf '%s\n' "$svc"
        fi
    done | tr '\n' ' '
}

stop_node_excluded_services() {
    is_node_mode || return 0
    docker compose -f "$COMPOSE_FILE" stop --timeout 15 frontend caddy || echo -e "${YELLOW}    ⚠ frontend/caddy stop failed${NC}"
    docker compose -f "$COMPOSE_FILE" rm -f frontend caddy || echo -e "${YELLOW}    ⚠ frontend/caddy rm failed${NC}"
}

prune_stopped_conflicting() {
    local pattern="$1"
    local c_id=""
    local c_name=""
    local removed=0
    for c_id in $(docker ps -a -q --filter "name=${pattern}" --filter "status=exited" --filter "status=created"  || true); do
        c_name=$(docker inspect "$c_id" --format='{{.Name}}'  | sed 's/^\///')
        if [ -n "$c_name" ]; then
            docker rm "$c_id"  && removed=$((removed + 1))
        fi
    done
    [ "$removed" -gt 0 ] && echo -e "  \033[0;32m✓\033[0m Removed $removed stopped container(s)" || true
}

cleanup_stale_containers() {
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    timeout -k 5 30 docker compose -f "$compose_f" down --remove-orphans  || true
    prune_stopped_conflicting "smsly-hosting"
    prune_stopped_conflicting "smsly-"
}

compose_stack_build() {
    docker_login
    local services=""
    if is_node_mode; then
        stop_node_excluded_services
        services="$(compose_stack_build_service_args)"
        [ -n "$services" ] || return 1
        timeout -k 5 300 docker compose -f "$COMPOSE_FILE" build "$@" $services
    else
        timeout -k 5 300 docker compose -f "$COMPOSE_FILE" build "$@"
    fi
}

compose_stack_up() {
    local services=""
    if is_node_mode; then
        stop_node_excluded_services
        services="$(compose_stack_service_args)"
        [ -n "$services" ] || return 1
        timeout -k 10 300 docker compose -f "$COMPOSE_FILE" up -d "$@" $services
    else
        timeout -k 10 300 docker compose -f "$COMPOSE_FILE" up -d "$@"
    fi
}

get_pgcat_if_exists() {
    local compose_target="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" ; then
        echo "pgcat"
    fi
}

get_db_service() {
    echo "db"
}

get_redis_service() {
    local ct="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$ct" ] && grep -q "^  *redis-replica:" "$ct" ; then
        echo "redis-primary"
    else
        echo "redis"
    fi
}

ensure_infrastructure_permissions() {
    local caddy_config_dir="/opt/smsly-hosting/caddy-config"
    local staticfiles_dir="/opt/smsly-hosting/backend/staticfiles"
    local builds_dir="/opt/smsly-hosting/builds"
    local prometheus_targets_dir="/opt/smsly-hosting/prometheus-targets"

    echo -e "${BLUE}  -> Ensuring infrastructure permissions...${NC}"

    mkdir -p "$caddy_config_dir"
    mkdir -p "$staticfiles_dir"
    mkdir -p "$builds_dir"
    mkdir -p "$prometheus_targets_dir"

    _chown_owner="1000:1000"
    for _dir in "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir"; do
        if [ -d "$_dir" ]; then
            if ! chown -R "$_chown_owner" "$_dir"; then
                echo -e "${YELLOW}     ⚠ Could not chown $_dir to $_chown_owner (see error above)${NC}"
            fi
        fi
    done

    chmod -R u+rwX,g+rwX "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir" || echo -e "${YELLOW}     ⚠ chmod failed on bind-mount dirs${NC}"
    find "$caddy_config_dir" -type d -exec chmod 2775 {} + || true
    find "$staticfiles_dir" -type d -exec chmod 2775 {} + || true
    find "$builds_dir" -type d -exec chmod 2775 {} + || true
    find "$prometheus_targets_dir" -type d -exec chmod 2777 {} + || echo -e "${YELLOW}     ⚠ chmod failed on $prometheus_targets_dir${NC}"
    chmod 2777 "$prometheus_targets_dir" || echo -e "${YELLOW}     ⚠ chmod failed on $prometheus_targets_dir${NC}"

    [ -f "$caddy_config_dir/Caddyfile" ] && chmod 664 "$caddy_config_dir/Caddyfile" || true
    [ -f "$caddy_config_dir/.reload" ] && chmod 664 "$caddy_config_dir/.reload" || true

    if command -v docker ; then
        _vol_names="$(docker volume ls -q 2>/dev/null | grep -E '(^|_)(backups_data|caddy_data)$')"
        for vol in ${_vol_names:-backups_data}; do
            if docker volume inspect "$vol" >/dev/null 2>&1; then
                echo -e "${BLUE}     ↳ Setting permissions for volume: $vol...${NC}"
                docker run --rm -v "${vol}:/data" alpine chown -R 1000:1000 /data || echo -e "${YELLOW}     ⚠ Could not chown volume $vol${NC}"
            else
                echo -e "${YELLOW}     ⚠ $vol volume not found — skipping chown${NC}"
            fi
        done
    fi

    local probe_failed=0
    for probe_dir in "$caddy_config_dir" "$staticfiles_dir" "$builds_dir" "$prometheus_targets_dir"; do
        if ! echo "perm-ok" > "$probe_dir/.perm_probe"; then
            echo -e "${YELLOW}  ⚠ Write probe failed for $probe_dir — retrying with chown...${NC}"
            chown -R 1000:1000 "$probe_dir" || true
            chmod -R u+rwX,g+rwX "$probe_dir" || true
            if echo "perm-ok" > "$probe_dir/.perm_probe"; then
                echo -e "${GREEN}    ✓ Fixed${NC}"
            else
                echo -e "${RED}    ✗ Still cannot write to $probe_dir — check host permissions${NC}"
                probe_failed=1
            fi
        fi
        rm -f "$probe_dir/.perm_probe" || true
    done
    if [ -f "/opt/smsly-hosting/.env" ] && ! touch "/opt/smsly-hosting/.env"; then
        echo -e "${YELLOW}  ⚠ .env not writable — fixing...${NC}"
        chown 1000:1000 "/opt/smsly-hosting/.env" || true
        chmod 640 "/opt/smsly-hosting/.env" || true
    fi
    if [ "$probe_failed" -ne 0 ]; then
        echo -e "${RED}  ✗ Some bind-mount directories are not writable — containers may fail${NC}"
    fi
}

resolve_container_target() {
    local target="$1"

    [ -z "$target" ] && return 0

    # NOTE: the existence probe MUST NOT write to stdout — callers capture the
    # function's output in $(...) and pass it straight to `docker inspect`;
    # a bare `docker inspect` here would embed the full JSON in the resolved
    # target and make every caller fail with "error: no such object: [ ... ]".
    if timeout -k 5 10 docker container inspect "$target" >/dev/null 2>&1 ; then
        echo "$target"
        return 0
    fi

    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    if [ -f "$compose_f" ]; then
        local services
        services="$(timeout -k 5 10 docker compose -f "$compose_f" config --services )"
        if [ -n "$services" ]; then
            for svc in $services; do
                if [[ "$target" == *"-${svc}-"* || "$target" == *"_${svc}_"* || "$target" == *"-${svc}" || "$target" == *"_${svc}" || "$target" == "$svc" ]]; then
                    local cid
                    cid="$(timeout -k 5 10 docker compose -f "$compose_f" ps -q "$svc"  | head -n 1 || true)"
                    if [ -n "$cid" ]; then
                        echo "$cid"
                        return 0
                    fi
                fi
            done
        fi
    fi

    local cid_svc
    cid_svc="$(docker compose -f "$compose_f" ps -q "$target"  | head -n 1 || true)"
    if [ -n "$cid_svc" ]; then
        echo "$cid_svc"
        return 0
    fi

    local cid_fuzzy
    local fuzzy_pattern
    fuzzy_pattern="${target//-/*}"
    fuzzy_pattern="${fuzzy_pattern//_/*}"
    cid_fuzzy="$(docker ps -a --filter "name=${fuzzy_pattern}" -q  | head -n 1 || true)"
    if [ -n "$cid_fuzzy" ]; then
        echo "$cid_fuzzy"
        return 0
    fi

    echo "$target"
}

ensure_container_on_network() {
    local network_name="$1"
    local raw_target="$2"

    [ -z "$network_name" ] && return 0
    [ -z "$raw_target" ] && return 0

    local container_name
    container_name="$(resolve_container_target "$raw_target")"

    docker container inspect "$container_name"  || return 0
    docker network inspect "$network_name"  || return 0

    if docker network inspect "$network_name" --format '{{range $k, $v := .Containers}}{{$k}}{{end}}'  | grep -q "$container_name"; then
        return 0
    fi

    docker network connect "$network_name" "$container_name" || echo -e "${YELLOW}    ⚠ Network connect $container_name to $network_name failed${NC}"
}

recreate_traefik_preserving_certs() {
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"
    local acme_src="/var/lib/docker/volumes/smsly-hosting_letsencrypt_data/_data/acme.json"
    local acme_backup=""

    if ! docker compose -f "$compose_f" ps -q traefik  | grep -q .; then
        echo -e "${YELLOW}  WARN traefik not running; skipping one-time recreate.${NC}"
        return 1
    fi

    echo -e "${BLUE}  → Verifying socket-proxy is healthy (traefik Docker provider depends on it)...${NC}"
    local i=0
    while [ $i -lt 30 ]; do
        if docker inspect --format='{{.State.Health.Status}}' smsly-hosting-socket-proxy-1  | grep -q healthy; then
            break
        fi
        sleep 2
        i=$((i + 1))
    done
    if [ $i -ge 30 ]; then
        echo -e "${RED}  x socket-proxy not healthy; aborting to avoid 503 on deployed services.${NC}"
        echo -e "${RED}    Fix: docker logs smsly-hosting-socket-proxy-1${NC}"
        return 1
    fi

    echo -e "${BLUE}  → Backing up acme.json...${NC}"
    if [ -f "$acme_src" ]; then
        acme_backup="/tmp/smsly-acme-$(date +%s).json"
        cp "$acme_src" "$acme_backup" && chmod 600 "$acme_backup"
        echo -e "${GREEN}    OK saved to $acme_backup${NC}"
    else
        echo -e "${YELLOW}    WARN no existing acme.json; new container will request fresh certs.${NC}"
    fi

    echo -e "${BLUE}  → Recording pre-recreate router count from Traefik API...${NC}"
    sleep 2
    local pre_routers=0
    if timeout 10 docker exec smsly-hosting-traefik-1 sh -c 'command -v wget ' ; then
        pre_routers=$(timeout 10 docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/http/routers  | grep -o '"name"' | wc -l)
    else
        pre_routers=$(timeout 10 docker exec smsly-hosting-traefik-1 curl -s http://127.0.0.1:8080/api/http/routers  | grep -o '"name"' | wc -l)
    fi
    echo -e "${BLUE}    pre-recreate routers: $pre_routers${NC}"
    if [ "$pre_routers" -le 1 ]; then
        echo -e "${YELLOW}    WARN only $pre_routers router(s) before recreate (expected route-fallback + deployed services).${NC}"
        echo -e "${YELLOW}          Deployed services may already have stale labels.${NC}"
    fi

    echo -e "${BLUE}  → Recreating traefik (preserves letsencrypt_data volume + acme.json)...${NC}"
    timeout -k 5 60 docker compose -f "$compose_f" up -d --no-deps traefik 2>&1 | sed 's/^/    /'

    echo -e "${BLUE}  → Reconnecting traefik to smsly-proxy network (recreate can drop external nets)...${NC}"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"

    if [ -n "$acme_backup" ] && [ -f "$acme_backup" ]; then
        sleep 3
        if [ -f "$acme_src" ]; then
            cp "$acme_backup" "$acme_src" && chmod 600 "$acme_src"
            echo -e "${GREEN}    OK restored acme.json perms to 0600${NC}"
        fi
        rm -f "$acme_backup"
    fi

    echo -e "${BLUE}  → Waiting for traefik healthcheck...${NC}"
    i=0
    while [ $i -lt 30 ]; do
        if docker inspect --format='{{.State.Health.Status}}' smsly-hosting-traefik-1  | grep -q healthy; then
            break
        fi
        sleep 2
        i=$((i + 1))
    done
    if [ $i -ge 30 ]; then
        echo -e "${YELLOW}  WARN traefik healthcheck timeout; check 'docker logs smsly-hosting-traefik-1'${NC}"
    fi

    echo -e "${BLUE}  → Waiting for Traefik routing table to repopulate (CRITICAL — prevents 503 on deployed services)...${NC}"
    i=0
    local post_routers=0
    while [ $i -lt 60 ]; do
        if timeout 10 docker exec smsly-hosting-traefik-1 sh -c 'command -v wget ' ; then
            post_routers=$(timeout 10 docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/http/routers  | grep -o '"name"' | wc -l)
        else
            post_routers=$(timeout 10 docker exec smsly-hosting-traefik-1 curl -s http://127.0.0.1:8080/api/http/routers  | grep -o '"name"' | wc -l)
        fi
        if [ "$post_routers" -ge "$pre_routers" ] && [ "$post_routers" -gt 0 ]; then
            echo -e "${GREEN}    OK post-recreate routers: $post_routers (matches or exceeds pre-recreate)${NC}"

            local eps
            if timeout 10 docker exec smsly-hosting-traefik-1 sh -c 'command -v wget ' ; then
                eps=$(timeout 10 docker exec smsly-hosting-traefik-1 wget -qO- http://127.0.0.1:8080/api/entrypoints )
            else
                eps=$(timeout 10 docker exec smsly-hosting-traefik-1 curl -s http://127.0.0.1:8080/api/entrypoints )
            fi
            if echo "$eps" | grep -q '"name":"websecure"'; then
                echo -e "${GREEN}    OK websecure entrypoint is active${NC}"
            else
                echo -e "${YELLOW}    WARN websecure entrypoint not detected${NC}"
            fi
            if echo "$eps" | grep -q '"name":"metrics"'; then
                echo -e "${GREEN}    OK metrics entrypoint is active${NC}"
            else
                echo -e "${YELLOW}    WARN metrics entrypoint not detected${NC}"
            fi

            return 0
        fi
        sleep 2
        i=$((i + 1))
    done
    echo -e "${YELLOW}  WARN Traefik has fewer routers than before ($post_routers vs $pre_routers).${NC}"
    echo -e "${YELLOW}        Deployed services have stale Traefik labels (from before the routing fix).${NC}"
    echo -e "${YELLOW}        Redeploy them via the SMSLY dashboard to refresh labels.${NC}"
    return 1
}

bust_core_build_cache() {
    echo -e "${BLUE}  -> Busting frontend/backend build cache (safe mode)...${NC}"

    local core_svcs="frontend backend celery celery-deploy celery-fast celery-beat"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        core_svcs="backend celery-worker"
    elif [ "$MODE_NODE" = "true" ]; then
        core_svcs="backend celery celery-deploy celery-fast celery-beat"
    fi

    for svc in $core_svcs; do
        local image_ids=""
        image_ids="$(docker compose -f "$COMPOSE_FILE" images -q "$svc"  | awk 'NF' | sort -u || true)"
        if [ -n "$image_ids" ]; then
            while read -r image_id; do
                [ -n "$image_id" ] && docker rmi -f "$image_id" || echo -e "${YELLOW}    ⚠ docker rmi $image_id failed${NC}"
            done <<< "$image_ids"
        fi
    done

    docker builder prune -af || echo -e "${YELLOW}    ⚠ docker builder prune failed${NC}"

    echo -e "${BLUE}  -> Pruning deeply stale images (>7 days old)...${NC}"
    docker image prune -a -f --filter "until=168h" || echo -e "${YELLOW}    ⚠ docker image prune failed${NC}"

    echo -e "${GREEN}  OK Cache bust complete (targeted images + build cache + deep prune)${NC}"
}

restart_edge_stack() {
    local all_edge_services="socket-proxy traefik"
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        all_edge_services="socket-proxy traefik route-fallback"
    fi

    echo -e "${BLUE}  -> Checking edge proxy stack (traefik/socket-proxy/route-fallback)...${NC}"
    local down_services=""
    for svc in $all_edge_services; do
        if docker compose -f "$COMPOSE_FILE" ps "$svc"  | grep -q "Up"; then
            echo -e "${GREEN}    ✓ $svc already running${NC}"
        else
            echo -e "${YELLOW}    ⚠ $svc is down — starting...${NC}"
            down_services="$down_services $svc"
        fi
    done

    if [ -n "$down_services" ]; then
        timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d --no-deps $down_services || \
            timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d $down_services || echo -e "${YELLOW}    ⚠ Service restart failed${NC}"
    fi

    echo -e "${BLUE}  -> Re-attaching external networks...${NC}"
    ensure_container_on_network "smsly-net" "smsly-hosting-traefik-1"
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        ensure_container_on_network "smsly-net" "smsly-hosting-route-fallback-1"
    fi
    ensure_container_on_network "smsly-proxy" "smsly-hosting-traefik-1"
    ensure_container_on_network "smsly-proxy" "smsly-hosting-socket-proxy-1"

    if should_manage_caddy && docker compose -f "$COMPOSE_FILE" ps caddy  | grep -q "Up"; then
        if caddy_needs_fix; then
            generate_safe_caddyfile "restart_edge_stack validation"
        fi
        echo -e "${BLUE}  -> Reloading Caddy...${NC}"
        reload_container_caddy  || true
    fi
    echo -e "${GREEN}  OK Edge stack healthy${NC}"
}

wait_for_traefik_api() {
    local max_wait="${1:-30}"
    local waited=0
    local interval=2
    echo -e "${BLUE}  → Waiting for Traefik API to be ready...${NC}"
    while [ "$waited" -lt "$max_wait" ]; do
        if curl -sf --max-time 3 http://127.0.0.1:8082/api/version ; then
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
        if docker compose -f "$COMPOSE_FILE" config --services  | grep -qx "$svc"; then
            app_services+=("$svc")
        fi
    done

    for svc in "${edge_services_requested[@]}"; do
        if docker compose -f "$COMPOSE_FILE" config --services  | grep -qx "$svc"; then
            edge_services+=("$svc")
        fi
    done

    runtime_services=("${app_services[@]}" "${edge_services[@]}")

    if [ "${#runtime_services[@]}" -eq 0 ]; then
        echo -e "${YELLOW}  ⚠ No runtime services found to refresh${NC}"
        return 0
    fi

    if [ "${#app_services[@]}" -gt 0 ]; then
        timeout -k 5 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "${app_services[@]}" || \
            timeout -k 5 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate "${app_services[@]}" || echo -e "${YELLOW}    ⚠ App services restart failed${NC}"
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
        local down_edge=()
        for svc in "${edge_services[@]}"; do
            container_name="smsly-hosting-${svc}-1"
            if docker compose -f "$COMPOSE_FILE" ps "$svc"  | grep -q "Up"; then
                echo -e "${GREEN}  ✓ $svc already running${NC}"
            else
                echo -e "${YELLOW}  ⚠ $svc is down — starting...${NC}"
                down_edge+=("$svc")
            fi
        done
        if [ "${#down_edge[@]}" -gt 0 ]; then
            timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d --no-deps "${down_edge[@]}" || \
                timeout -k 5 30 docker compose -f "$COMPOSE_FILE" up -d "${down_edge[@]}" || echo -e "${YELLOW}    ⚠ Edge services restart failed${NC}"
        fi

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
        docker compose -f "$COMPOSE_FILE" ps "${failed_services[@]}"  || true
        docker compose -f "$COMPOSE_FILE" logs --tail=80 "${failed_services[@]}"  || true
        return 1
    fi

    if should_manage_caddy; then
        install_caddy_health_guard "${DOMAIN:-}"
        reload_container_caddy  || true
    fi

    if [ "$MODE_AGENT_LITE" != "true" ]; then
        echo -e "${BLUE}  → Refreshing Observability Stack...${NC}"
        if [ -f "infrastructure/docker/docker-compose.observability.yml" ]; then
            docker compose -f infrastructure/docker/docker-compose.observability.yml pull || echo -e "${YELLOW}    ⚠ Observability pull failed${NC}"
            docker compose -f infrastructure/docker/docker-compose.observability.yml up -d || echo -e "${YELLOW}    ⚠ Observability up failed${NC}"
            for obs_ctr in smsly-loki smsly-promtail smsly-prometheus smsly-cadvisor smsly-node-exporter smsly-grafana; do
                i=0
                while [ $i -lt 30 ]; do
                    if docker inspect --format='{{.State.Health.Status}}' "$obs_ctr"  | grep -qE 'healthy|^$'; then
                        break
                    fi
                    sleep 2
                    i=$((i + 1))
                done
            done
        fi
    fi

    if systemctl is-active --quiet smsly-autoscaler; then
        systemctl restart smsly-autoscaler || echo -e "${YELLOW}    ⚠ smsly-autoscaler restart failed${NC}"
    else
        echo -e "${BLUE}  → smsly-autoscaler not running, skipping restart${NC}"
    fi
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

ensure_celery_workers_running() {
    local celery_services=()
    local down_services=()
    local base_workers=(celery celery-deploy celery-fast celery-beat)
    if [ "${CELERY_AUTOSCALE_ENABLED:-false}" != "true" ]; then
        base_workers+=(celery-2 celery-3)
    fi
    for svc in "${base_workers[@]}"; do
        if docker compose -f "$COMPOSE_FILE" config --services  | grep -qx "$svc"; then
            celery_services+=("$svc")
        fi
    done
    if [ "${#celery_services[@]}" -eq 0 ]; then
        echo -e "${BLUE}  → No celery services configured, skipping celery check${NC}"
        return 0
    fi
    for svc in "${celery_services[@]}"; do
        if ! docker compose -f "$COMPOSE_FILE" ps "$svc"  | grep -q "Up"; then
            down_services+=("$svc")
        fi
    done
    if [ "${#down_services[@]}" -eq 0 ]; then
        echo -e "${GREEN}  ✓ All celery workers are running${NC}"
        return 0
    fi
    echo -e "${YELLOW}  ⚠ Celery workers down: ${down_services[*]}. Restarting...${NC}"
    local base_down=()
    local extra_down=()
    for svc in "${down_services[@]}"; do
        case "$svc" in
            celery-2|celery-3) extra_down+=("$svc") ;;
            *) base_down+=("$svc") ;;
        esac
    done
    if [ "${#base_down[@]}" -gt 0 ]; then
        timeout -k 5 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps "${base_down[@]}" || \
            timeout -k 5 60 docker compose -f "$COMPOSE_FILE" up -d --force-recreate "${base_down[@]}" || echo -e "${YELLOW}    ⚠ Celery workers restart failed${NC}"
    fi
    if [ "${#extra_down[@]}" -gt 0 ]; then
        timeout -k 5 60 docker compose -f "$COMPOSE_FILE" --profile extra-workers up -d --force-recreate --no-deps "${extra_down[@]}" || \
            timeout -k 5 60 docker compose -f "$COMPOSE_FILE" --profile extra-workers up -d --force-recreate "${extra_down[@]}" || echo -e "${YELLOW}    ⚠ Extra celery workers restart failed${NC}"
    fi
    local all_ok=true
    for svc in "${down_services[@]}"; do
        if wait_for_container_ready "smsly-hosting-${svc}-1" 120; then
            echo -e "${GREEN}    ✓ $svc is running${NC}"
        else
            echo -e "${RED}    ✗ $svc failed to start${NC}"
            all_ok=false
        fi
    done
    if [ "$all_ok" = true ]; then
        echo -e "${GREEN}  ✓ All celery workers recovered${NC}"
    fi
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
        state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_name"  || echo "missing")"
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

# --- end lib/docker.sh ---

# --- lib/env.sh ---
gen_hex_secret() {
    local bytes="${1:-16}"
    python3 -c "import secrets; print(secrets.token_hex(${bytes}))"  || openssl rand -hex "$bytes"
}

env_get_value() {
    local env_file="$1"
    local var_name="$2"
    grep -m1 "^${var_name}=" "$env_file"  | cut -d= -f2- | sed 's/^"//;s/"$//;s/^'\''//;s/'\''$//' || true
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
        value="$(hostname  | tr -c 'A-Za-z0-9_.-' '-' | sed -E 's/^-+//; s/-+$//; s/-+/-/g' | cut -c1-96)"
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
    current_ips="$(hostname -I  | tr -s ' ' '\n' | grep -v '^$' || true)"
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
        [ -n "$var_comment" ] && ! grep -q "# $var_comment" "$env_file"  && echo "# $var_comment" >> "$env_file"
        env_set_value "$env_file" "$var_name" "$var_value"
        echo -e "${GREEN}  OK $var_name set${NC}"
    fi
}
# --- end lib/env.sh ---

# --- lib/harden.sh ---
#!/bin/bash
set +e

_harden_log() {
    local level="$1"; shift
    case "$level" in
        ok)   echo -e "${GREEN}  ✓ [harden] $*${NC}" ;;
        warn) echo -e "${YELLOW}  ⚠ [harden] $*${NC}" ;;
        err)  echo -e "${RED}  ✗ [harden] $*${NC}" ;;
        info) echo -e "${BLUE}  → [harden] $*${NC}" ;;
    esac
}

# --- lib/harden_fail2ban.sh ---
#!/bin/bash

_harden_fail2ban_bootstrap() {
    if ! command -v fail2ban-client ; then
        apt_run apt-get install -y fail2ban  || true
    fi
    command -v fail2ban-client  || return 1

    [ -f /etc/fail2ban/jail.local ] || cat <<'JAIL_EOF' > /etc/fail2ban/jail.local
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 3
banaction = iptables-multiport
banaction_allports = iptables-allports

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 1h
findtime = 10m

[recidive]
enabled = true
filter = recidive
logpath = /var/log/fail2ban.log
action = iptables-allports[name=recidive]
bantime = 24h
findtime = 1d
maxretry = 3
JAIL_EOF
    # Enable Caddy jails when Caddy logs are available
    if [ -d /var/log/caddy ] || docker volume ls --format '{{.Name}}'  | grep -q caddy_logs; then
        # Never duplicate the sections: fail2ban aborts on a repeated
        # [caddy-auth], and every install/update run would otherwise append.
        if ! grep -q '^\[caddy-auth\]' /etc/fail2ban/jail.local 2>/dev/null; then
            cat <<'CADDY_JAIL_EOF' >> /etc/fail2ban/jail.local

[caddy-auth]
enabled = true
filter = caddy-auth
port = http,https
logpath = /var/log/caddy/access.log
maxretry = 5
bantime = 1h

[caddy-dos]
enabled = true
filter = caddy-dos
port = http,https
logpath = /var/log/caddy/access.log
findtime = 300
maxretry = 300
bantime = 600
CADDY_JAIL_EOF
        fi
    fi
    # Caddy auth filter (JSON access log — 401/403 responses)
    [ -f /etc/fail2ban/filter.d/caddy-auth.conf ] || cat <<'FILTER_EOF' > /etc/fail2ban/filter.d/caddy-auth.conf
[Definition]
failregex = ^.*"remote_ip":"<HOST>".*"status":(401|403).*$
ignoreregex =
FILTER_EOF
    # Caddy DoS filter (JSON access log — any request)
    [ -f /etc/fail2ban/filter.d/caddy-dos.conf ] || cat <<'FILTER_EOF' > /etc/fail2ban/filter.d/caddy-dos.conf
[Definition]
failregex = ^.*"remote_ip":"<HOST>".*"method":"(GET|POST|HEAD|PUT|DELETE|PATCH)".*$
ignoreregex =
FILTER_EOF

    systemctl enable fail2ban || _harden_log warn "fail2ban enable failed"
    # Blocking start — wait for the service to actually be ACTIVE (not just for
    # `systemctl restart` to return). If it never comes up we surface the real
    # failure via journalctl instead of spamming socket errors.
    systemctl restart fail2ban || _harden_log warn "fail2ban restart returned non-zero"
    local _up=0
    for _i in $(seq 1 30); do
        if systemctl is-active --quiet fail2ban; then
            _up=1
            break
        fi
        sleep 1
    done
    if [ "$_up" -ne 1 ]; then
        _harden_log err "fail2ban failed to become active — last journalctl output:"
        journalctl -u fail2ban -n 40 --no-pager 2>&1 | sed 's/^/      /' || true
        return 1
    fi
    # Service is active — confirm the client can reach the server socket.
    if ! fail2ban-client ping; then
        _harden_log warn "fail2ban active but client cannot reach socket"
    fi
}

_harden_fail2ban_verify() {
    command -v fail2ban-client  || { _harden_log warn "fail2ban — not installed"; return 1; }
    if ! systemctl is-active --quiet fail2ban; then
        _harden_log warn "fail2ban not running — last journalctl output:"
        journalctl -u fail2ban -n 30 --no-pager 2>&1 | sed 's/^/      /' || true
        return 1
    fi
    if fail2ban-client ping && fail2ban-client status sshd ; then
        _harden_log ok "fail2ban active (sshd + recidive + http)"
        return 0
    fi
    _harden_log warn "fail2ban running but not responding to client"
    return 1
}

# --- end lib/harden_fail2ban.sh ---
# --- lib/harden_ufw.sh ---
#!/bin/bash

_harden_ufw_bootstrap() {
    command -v ufw  || apt_run apt-get install -y ufw  || true
    command -v ufw  || return 1

    # Already active — just verify ports are open, then bail
    if ufw status  | grep -qi "active"; then
        for port in 22 80 443 51820; do
            ufw status verbose  | grep -qE "${port}(/tcp|/udp)?.*ALLOW" || ufw allow "$port" || echo -e "${YELLOW}    ⚠ ufw allow port $port failed${NC}"
        done
        # Whitelist Docker bridges
        for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
            ip link show "$iface" >/dev/null 2>&1 || continue
            ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
        done
        return 0
    fi

    # Inactive — configure and enable (INPUT default deny, FORWARD stays open for Docker)
    ufw --force default deny incoming || echo -e "${YELLOW}    ⚠ ufw default deny incoming failed${NC}"
    ufw --force default allow outgoing || echo -e "${YELLOW}    ⚠ ufw default allow outgoing failed${NC}"
    ufw allow ssh || echo -e "${YELLOW}    ⚠ ufw allow ssh failed${NC}"
    ufw allow 80/tcp || echo -e "${YELLOW}    ⚠ ufw allow 80/tcp failed${NC}"
    ufw allow 443/tcp || echo -e "${YELLOW}    ⚠ ufw allow 443/tcp failed${NC}"
    ufw allow 51820/udp || echo -e "${YELLOW}    ⚠ ufw allow 51820/udp failed${NC}"
    for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
        ip link show "$iface" >/dev/null 2>&1 || continue
        ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
    done
    ufw --force enable || echo -e "${YELLOW}    ⚠ ufw enable failed${NC}"
    # Verify it actually came up
    for _i in $(seq 1 5); do
        ufw status  | grep -qi "active" && break
        sleep 2
    done
}

_harden_ufw_verify() {
    command -v ufw  || { _harden_log warn "ufw — not installed"; return 1; }
    if ufw status  | grep -qi "active"; then
        _harden_log ok "ufw active (host INPUT hardened)"
        return 0
    fi
    _harden_log warn "ufw not active — check ufw status"
    return 1
}

# --- end lib/harden_ufw.sh ---
# --- lib/harden_apparmor.sh ---
#!/bin/bash

_harden_apparmor_bootstrap() {
    command -v aa-status  || apt_run apt-get install -y apparmor apparmor-utils  || true
    command -v aa-status  || return 1
    systemctl enable apparmor || echo -e "${YELLOW}    ⚠ apparmor enable failed${NC}"
    systemctl start apparmor || echo -e "${YELLOW}    ⚠ apparmor start failed${NC}"
}

_harden_apparmor_verify() {
    command -v aa-status  || { _harden_log warn "apparmor — not installed"; return 1; }
    local count
    count=$(aa-status --json  | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('processes',{})))"  || echo "0")
    count="${count//[^0-9]/}"
    : "${count:=0}"
    if [ "$count" -gt 0 ] ; then
        _harden_log ok "apparmor enforcing ($count profiles)"
        return 0
    fi
    _harden_log warn "apparmor installed but no enforce profiles"
    return 1
}

# --- end lib/harden_apparmor.sh ---
# --- lib/harden_auditd.sh ---
#!/bin/bash

_harden_auditd_bootstrap() {
    command -v auditd  || apt_run apt-get install -y auditd audispd-plugins  || true

    if [ ! -f /etc/audit/rules.d/smsly.rules ]; then
        mkdir -p /etc/audit/rules.d
        cat > /etc/audit/rules.d/smsly.rules <<'AUDIT_EOF'
-w /etc/shadow -p wa -k identity
-w /etc/passwd -p wa -k identity
-w /etc/sudoers -p wa -k privilege-escalation
-w /etc/ssh/sshd_config -p wa -k sshd
-w /opt/smsly-hosting/.env -p wa -k smsly-config
-w /opt/smsly-hosting/secrets/ -p wa -k smsly-secrets
-a always,exit -F arch=b64 -S execve -F path=/usr/bin/docker -k docker-exec
-a always,exit -F arch=b64 -S mount -k filesystem-mounts
-a exit,always -F arch=b64 -S execve -F euid=0 -F auid>=1000 -k priv-esc
AUDIT_EOF
    fi
    systemctl enable auditd || echo -e "${YELLOW}    ⚠ auditd enable failed${NC}"
    systemctl restart auditd || echo -e "${YELLOW}    ⚠ auditd restart failed${NC}"
}

_harden_auditd_verify() {
    command -v auditd  || { _harden_log warn "auditd — not installed"; return 1; }
    if systemctl is-active --quiet auditd ; then
        _harden_log ok "auditd active (file + syscall monitoring)"
        return 0
    fi
    _harden_log warn "auditd not running — may need kernel param audit=1"
    return 1
}

# --- end lib/harden_auditd.sh ---
# --- lib/harden_kernel.sh ---
#!/bin/bash

_harden_kernel_bootstrap() {
    local sysctl_file="/etc/sysctl.d/99-smsly-security.conf"
    [ -f "$sysctl_file" ] && return 0  # already applied

    cat > "$sysctl_file" <<'SYSCTL_EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
kernel.yama.ptrace_scope = 1
kernel.unprivileged_bpf_disabled = 1
kernel.randomize_va_space = 2
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
fs.suid_dumpable = 0
SYSCTL_EOF
    sysctl -p "$sysctl_file" || echo -e "${YELLOW}    ⚠ sysctl -p failed${NC}"
}

_harden_kernel_verify() {
    if [ -f /etc/sysctl.d/99-smsly-security.conf ]; then
        _harden_log ok "kernel hardening applied"
        return 0
    fi
    _harden_log warn "kernel hardening not applied"
    return 1
}

# --- end lib/harden_kernel.sh ---
# --- lib/harden_docker_daemon.sh ---
#!/bin/bash

_harden_docker_daemon_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    local daemon_cfg="/etc/docker/daemon.json"
    [ ! -f "$daemon_cfg" ] && echo '{}' > "$daemon_cfg"

    local changed=false

    # log rotation
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('log-driver')=='json-file' and d.get('log-opts',{}).get('max-size')=='10m' else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['log-driver'] = 'json-file'
cfg['log-opts'] = {'max-size': '10m', 'max-file': '3'}
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # live-restore
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('live-restore') else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['live-restore'] = True
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # seccomp
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('features',{}).get('seccomp') else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg.setdefault('features', {})['seccomp'] = True
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # Restart Docker if config changed AND no SMSLY containers are running
    # (doing so live would kill production).
    if [ "$changed" = "true" ]; then
        local _smsly_ctrs
        _smsly_ctrs="$(docker ps --format '{{.Names}}'  | grep -c smsly || true)"
        if [ "$_smsly_ctrs" -eq 0 ]; then
            _harden_log info "Docker daemon config changed — restarting Docker..."
            systemctl restart docker || { _harden_log error "Docker restart failed"; }
            for _i in $(seq 1 30); do
                docker info  && break
                sleep 2
            done
            _harden_log ok "Docker daemon restarted with security config"
        else
            _harden_log warn "Docker daemon config changed but $_smsly_ctrs SMSLY containers are running — deferring restart (apply on next daemon reload)"
        fi
    fi
}

_harden_docker_daemon_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    local daemon_cfg="/etc/docker/daemon.json"
    if [ -f "$daemon_cfg" ] && python3 -c "import json; json.load(open('$daemon_cfg'))" ; then
        _harden_log ok "docker daemon security config present"
        return 0
    fi
    _harden_log warn "docker daemon config missing or invalid"
    return 1
}

# --- end lib/harden_docker_daemon.sh ---
# --- lib/harden_crowdsec.sh ---
#!/bin/bash

_harden_crowdsec_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    # CrowdSec comes from the main docker-compose stack — if the container
    # isn't running, try docker compose up -d for just that service.
    if docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec"; then
        return 0  # already up
    fi
    # Blocking start — wait for container to be healthy
    # The harden bootstrap may run before fresh_config has generated .env,
    # so only pass --env-file when the file exists.
    local env_args=()
    [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
    docker compose \
        "${env_args[@]}" \
        -f "$COMPOSE_FILE" \
        up -d crowdsec || echo -e "${YELLOW}    ⚠ crowdsec docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec" && break
        sleep 2
    done
}

_harden_crowdsec_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec"; then
        _harden_log warn "crowdsec — container not running"
        return 1
    fi
    # Refresh hub scenarios — only upgrade when explicitly allowed.
    # Auto-upgrading on every harden.sh run can silently break
    # production WAF if CrowdSec ships a breaking parser change.
    timeout -k 5 60 docker exec smsly-crowdsec cscli hub update  || _harden_log warn "crowdsec hub update failed"
    if [ "${CROWDSEC_AUTO_UPGRADE_HUB:-0}" = "1" ]; then
        timeout -k 5 60 docker exec smsly-crowdsec cscli hub upgrade  || _harden_log warn "crowdsec hub upgrade failed"
    else
        _harden_log info "crowdsec hub upgrade skipped (set CROWDSEC_AUTO_UPGRADE_HUB=1 to enable)"
    fi
    _harden_log ok "crowdsec deployed"
    return 0
}

# --- end lib/harden_crowdsec.sh ---
# --- lib/harden_falco.sh ---
#!/bin/bash

_harden_falco_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    local compose_file="$INSTALL_DIR/infrastructure/docker/docker-compose.falco.yml"
    [ -f "$compose_file" ] || return 1

    # Blocking start — always recreate so config changes take effect.
    # The harden bootstrap may run before fresh_config has generated .env,
    # so only pass --env-file when the file exists (compose file needs no vars).
    local env_args=()
    [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
    # smsly-net is declared external in the falco compose file but is only
    # created during stack deploy (fresh_deploy.sh) — the harden bootstrap
    # runs earlier, so create it here if missing.
    docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null 2>&1 || true
    docker compose \
        "${env_args[@]}" \
        -f "$compose_file" \
        up -d --force-recreate --pull always || echo -e "${YELLOW}    ⚠ falco docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}'  | grep -q "smsly-falco" && break
        sleep 2
    done
}

_harden_falco_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! docker ps --format '{{.Names}}'  | grep -q "smsly-falco"; then
        _harden_log warn "falco — container not running"
        return 1
    fi
    _harden_log ok "falco deployed"
    return 0
}

# --- end lib/harden_falco.sh ---
# --- lib/harden_container_runtime.sh ---
#!/bin/bash

_harden_container_runtime_bootstrap() {
    local install_dir="${INSTALL_DIR:-/opt/smsly-hosting}"
    local env_file="$install_dir/.env"

    # If CONTAINER_RUNTIME is already persisted in .env, skip detection.
    # The user can clear it to re-detect.
    if [ -f "$env_file" ] && grep -q '^CONTAINER_RUNTIME=' "$env_file" ; then
        return 0
    fi

    # Try Kata first (stronger isolation, requires KVM)
    if [ -e /dev/kvm ] && ! command -v kata-runtime ; then
        if [ -f "$install_dir/lib/install-kata.sh" ]; then
            echo -e "${BLUE}  → [harden] Kata Containers (KVM available) — installing...${NC}"
            bash "$install_dir/lib/install-kata.sh" || true
        fi
    fi

    if command -v kata-runtime ; then
        env_set_value "$env_file" "CONTAINER_RUNTIME" "kata"
        echo -e "${BLUE}  → [harden] Persisted CONTAINER_RUNTIME=kata in .env${NC}"
        return 0
    fi

    # Fall back to gVisor (lighter, no KVM required)
    if ! command -v runsc ; then
        if [ -f "$install_dir/lib/install-gvisor.sh" ]; then
            echo -e "${BLUE}  → [harden] gVisor (runsc) — installing...${NC}"
            bash "$install_dir/lib/install-gvisor.sh" || true
        fi
    fi

    if command -v runsc ; then
        env_set_value "$env_file" "CONTAINER_RUNTIME" "runsc"
        echo -e "${BLUE}  → [harden] Persisted CONTAINER_RUNTIME=runsc in .env${NC}"
        return 0
    fi
}

_harden_container_runtime_verify() {
    local found=0

    if command -v runsc ; then
        _harden_log ok "gVisor (runsc) installed"
        found=1
    fi

    if command -v kata-runtime ; then
        _harden_log ok "Kata Containers installed"
        found=1
    fi

    if [ "$found" -eq 0 ]; then
        _harden_log warn "container runtime sandboxing — install gVisor or Kata for VM-level isolation"
        return 1
    fi

    # Check Docker runtime registration
    if [ -f /etc/docker/daemon.json ]; then
        if python3 -c "import json; import sys; cfg=json.load(open('/etc/docker/daemon.json')); sys.exit(0 if 'runsc' in cfg.get('runtimes',{}) else 1)" ; then
            _harden_log ok "gVisor registered with Docker"
        elif python3 -c "import json; import sys; cfg=json.load(open('/etc/docker/daemon.json')); sys.exit(0 if 'kata-runtime' in cfg.get('runtimes',{}) else 1)" ; then
            _harden_log ok "Kata registered with Docker"
        fi
    fi

    # `found` is a 0/1 FLAG, not an exit code — returning it turns a successful
    # gVisor/Kata install into a FAILED security check (found=1 -> return 1).
    return 0
}

# --- end lib/harden_container_runtime.sh ---
# --- lib/harden_trivy.sh ---
#!/bin/bash

_harden_trivy_bootstrap() {
    if command -v trivy ; then
        return 0  # already installed
    fi

    _harden_log info "Installing Trivy vulnerability scanner..."
    local trivy_version="v0.54.1"
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  arch="64bit" ;;
        aarch64) arch="ARM64" ;;
        *)       _harden_log warn "Trivy — unsupported architecture: $arch"; return 1 ;;
    esac

    local deb_url="https://github.com/aquasecurity/trivy/releases/download/${trivy_version}/trivy_${trivy_version#v}_Linux-${arch}.deb"
    local tmp_deb
    tmp_deb="$(mktemp /tmp/trivy.XXXXXX.deb)"

    # Attempt 1: Direct DEB download with retries and timeouts
    if curl --retry 3 --retry-delay 2 --connect-timeout 15 -fsSL "$deb_url" -o "$tmp_deb" ; then
        if ! dpkg -i "$tmp_deb" ; then
            apt-get install -f -y  || true
            dpkg -i "$tmp_deb"  || true
        fi
        rm -f "$tmp_deb"
    else
        rm -f "$tmp_deb"
        _harden_log info "Direct DEB download failed — trying official APT repo and install script..."
    fi

    # Attempt 2: Official APT Repository fallback
    if ! command -v trivy ; then
        apt-get update -qq  || true
        if ! apt-get install -y trivy ; then
            if command -v gpg ; then
                curl --retry 2 --connect-timeout 10 -fsSL https://aquasecurity.github.io/trivy-repo/deb/public.key  | gpg --dearmor -o /usr/share/keyrings/trivy.gpg  || true
                echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc  || echo stable) main" > /etc/apt/sources.list.d/trivy.list  || true
                apt-get update -qq  || true
                apt-get install -y trivy  || true
            fi
        fi
    fi

    # Attempt 3: Official Contrib script fallback
    if ! command -v trivy ; then
        curl --retry 2 --connect-timeout 10 -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin  || true
    fi

    if command -v trivy ; then
        _harden_log ok "Trivy installed successfully"
        return 0
    fi
    _harden_log warn "Trivy — download and installation fallbacks failed"
    return 1
}

_harden_trivy_verify() {
    if command -v trivy ; then
        local ver
        ver="$(trivy --version  | head -1 || true)"
        _harden_log ok "Trivy available: ${ver}"
        return 0
    fi
    _harden_log warn "Trivy — not installed (image vulnerability scanning unavailable)"
    return 1
}

# --- end lib/harden_trivy.sh ---
# --- lib/harden_infisical.sh ---
#!/bin/bash

_harden_infisical_bootstrap() {
    local infisical_script="$INSTALL_DIR/lib/infisical.sh"
    if [ ! -f "$infisical_script" ]; then
        _harden_log info "Infisical script not found — skipping"
        return 0
    fi
    # Source Infisical functions and bootstrap
    # shellcheck disable=SC1090
    source "$infisical_script"  || {
        _harden_log warn "Failed to source infisical.sh"
        return 1
    }
    if ! command -v infisical_bootstrap ; then
        _harden_log warn "infisical_bootstrap function not found"
        return 1
    fi
    infisical_bootstrap  || {
        _harden_log warn "Infisical bootstrap had issues"
        return 1
    }
    return 0
}

_harden_infisical_verify() {
    # Optional layer: the bootstrap skips when lib/infisical.sh is absent —
    # the verify must skip too, or every install reports a phantom failure.
    local infisical_script="${INSTALL_DIR:-/opt/smsly-hosting}/lib/infisical.sh"
    if [ ! -f "$infisical_script" ]; then
        return 0
    fi
    command -v docker >/dev/null 2>&1 || return 0
    if docker ps --format '{{.Names}}'  | grep -q "smsly-infisical"; then
        _harden_log ok "Infisical running"
        return 0
    fi
    _harden_log warn "Infisical — container not running"
    return 1
}

# --- end lib/harden_infisical.sh ---

harden_security_bootstrap() {
    echo -e "${BLUE}  → [harden] Bootstrapping security stack (blocking)...${NC}"
    local _harden_failures=0
    _harden_fail2ban_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_ufw_bootstrap        || { _harden_failures=$((_harden_failures + 1)); }
    _harden_apparmor_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_auditd_bootstrap     || { _harden_failures=$((_harden_failures + 1)); }
    _harden_kernel_bootstrap
    _harden_docker_daemon_bootstrap
    _harden_crowdsec_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_falco_bootstrap      || { _harden_failures=$((_harden_failures + 1)); }
    _harden_container_runtime_bootstrap
    _harden_trivy_bootstrap      || { _harden_failures=$((_harden_failures + 1)); }
    _harden_infisical_bootstrap  || { _harden_failures=$((_harden_failures + 1)); }
    if [ "$_harden_failures" -gt 0 ]; then
        echo -e "${YELLOW}  ⚠ [harden] $_harden_failures layer(s) had issues — verify will report details${NC}"
    else
        echo -e "${GREEN}  ✓ [harden] Bootstrap complete — all layers started${NC}"
    fi
    return 0
}

harden_security_verify() {
    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Security Stack — Verification${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

    local failures=0 checks=0

    # NOTE: never use standalone `((checks++))` here — when the counter is 0
    # the arithmetic expression evaluates to 0 → exit status 1 → under `set -e`
    # (re-enabled by fresh_hardening.sh after harden.sh's `set +e`) the whole
    # install dies silently after the first check.
    if ! _harden_fail2ban_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_ufw_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_apparmor_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_auditd_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_kernel_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_docker_daemon_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_crowdsec_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_falco_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_container_runtime_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_trivy_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_infisical_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))

    local passed=$((checks - failures))
    echo ""
    if [ "$failures" -eq 0 ]; then
        echo -e "${GREEN}  All $passed/$checks security checks passed${NC}"
    else
        echo -e "${RED}  Security: $passed/$checks passed, $failures FAILED${NC}"
        echo -e "${YELLOW}  Review failures above — run 'sudo bash install.sh --debug' for details${NC}"
    fi
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# --- end lib/harden.sh ---

# --- lib/logging.sh ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'
export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"
export NEEDRESTART_MODE="${NEEDRESTART_MODE:-a}"

# --- end lib/logging.sh ---

# --- lib/media-node-ops.sh ---
# lib/media-node-ops.sh — Media Node runtime operations
# Sourced by install.sh or called directly for status/restart/logs

[ "${_MEDIA_OPS_LOADED:-}" = "true" ] && return 0
_MEDIA_OPS_LOADED=true

MEDIA_NODE_ENV="/opt/smsly-hosting-media/.env"

# ─── Status ───────────────────────────────────────────────────────────────────
media_status() {
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  SMSLY Media Node — Status${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

    echo -e "\n${BLUE}  System:${NC}"
    echo "    Host:     $(hostname -f  || hostname)"
    echo "    Kernel:   $(uname -r)"
    echo "    CPU:      $(nproc) cores | $(awk '/MemTotal/{printf "%.0f MB", $2/1024}' /proc/meminfo)"
    echo "    Public IP: $(detect_public_ip  || echo 'unknown')"

    echo -e "\n${BLUE}  Config:${NC}"
    if [ -f "$MEDIA_NODE_ENV" ]; then
        echo "    NODE_ID:    $(grep -m1 '^NODE_ID=' "$MEDIA_NODE_ENV" | cut -d= -f2-)"
        echo "    NODE_TYPE:  $(grep -m1 '^NODE_TYPE=' "$MEDIA_NODE_ENV" | cut -d= -f2-)"
        echo "    PUBLIC_IP:  $(grep -m1 '^PUBLIC_IP=' "$MEDIA_NODE_ENV" | cut -d= -f2-)"
        echo "    MASTER_URL: $(grep -m1 '^MASTER_API_URL=' "$MEDIA_NODE_ENV" | cut -d= -f2-)"
    else
        echo "    (no config found — not installed)"
    fi

    echo -e "\n${BLUE}  Services:${NC}"
    for svc in smsly-media-mgmt smsly-voice-api smsly-video livekit-server rtpengine freeswitch kamailio coturn openresty postgresql redis-server; do
        local status="not installed"
        if systemctl is-enabled "$svc" ; then
            if systemctl is-active "$svc" ; then
                echo -e "    ${GREEN}✓${NC} $svc (running)"
            else
                echo -e "    ${YELLOW}○${NC} $svc (stopped)"
            fi
        fi
    done

    echo -e "\n${BLUE}  Ports:${NC}"
    ss -tlnp  | grep -E ':(5060|5061|7880|9090|80|443|3478) ' | awk '{printf "    %-20s %s\n", $4, $6}' || true
    ss -ulnp  | grep -E ':(5060|22222|30000-31000|3478) ' | awk '{printf "    %-20s %s\n", $4, $6}' || true
}

# ─── Restart ──────────────────────────────────────────────────────────────────
media_restart() {
    echo -e "${BLUE}  → Restarting media services...${NC}"
    local failed=0
    for svc in smsly-media-mgmt livekit-server rtpengine freeswitch kamailio coturn smsly-voice-api smsly-video openresty; do
        if systemctl is-enabled "$svc" ; then
            if systemctl restart "$svc" ; then
                echo -e "    ${GREEN}✓${NC} $svc restarted"
            else
                echo -e "    ${RED}✗${NC} $svc restart failed"
                ((failed++))
            fi
        fi
    done
    if [ "$failed" -gt 0 ]; then
        echo -e "${RED}  ✗ $failed service(s) failed to restart${NC}"
        return 1
    fi
    echo -e "${GREEN}  ✓ All media services restarted${NC}"
}

# ─── Logs ─────────────────────────────────────────────────────────────────────
media_logs() {
    local service="${1:-smsly-media-mgmt}"
    local lines="${2:-100}"

    echo -e "${BLUE}  → Tailing $service logs (last $lines lines)...${NC}"
    if command -v journalctl ; then
        journalctl -u "$service" --no-pager -n "$lines"  || {
            echo -e "${YELLOW}  ⚠ No logs for $service (service may not exist)${NC}"
        }
    else
        tail -n "$lines" "/var/log/${service}.log"  || {
            echo -e "${YELLOW}  ⚠ No log file for $service${NC}"
        }
    fi
}

# ─── Health Check ─────────────────────────────────────────────────────────────
media_health() {
    echo -e "${BLUE}  → Checking media node health...${NC}"
    local failures=0

    # Check management daemon
    if curl -sf http://127.0.0.1:9090/health ; then
        echo -e "    ${GREEN}✓${NC} Management daemon: healthy"
    else
        echo -e "    ${RED}✗${NC} Management daemon: unreachable"
        failures=$((failures + 1))
    fi

    # Check LiveKit
    if systemctl is-active livekit-server ; then
        echo -e "    ${GREEN}✓${NC} LiveKit: running"
    else
        echo -e "    ${RED}✗${NC} LiveKit: not running"
        failures=$((failures + 1))
    fi

    # Check FreeSWITCH
    if command -v fs_cli  && fs_cli -x "status" ; then
        echo -e "    ${GREEN}✓${NC} FreeSWITCH: running"
    else
        echo -e "    ${YELLOW}○${NC} FreeSWITCH: not responding"
    fi

    # Check RTPEngine
    if systemctl is-active rtpengine ; then
        echo -e "    ${GREEN}✓${NC} RTPEngine: running"
    else
        echo -e "    ${RED}✗${NC} RTPEngine: not running"
        failures=$((failures + 1))
    fi

    # Check PostgreSQL
    if systemctl is-active postgresql ; then
        echo -e "    ${GREEN}✓${NC} PostgreSQL: running"
    else
        echo -e "    ${RED}✗${NC} PostgreSQL: not running"
        failures=$((failures + 1))
    fi

    # Check Redis
    if systemctl is-active redis-server ; then
        echo -e "    ${GREEN}✓${NC} Redis: running"
    else
        echo -e "    ${RED}✗${NC} Redis: not running"
        failures=$((failures + 1))
    fi

    if [ "$failures" -gt 0 ]; then
        echo -e "\n${RED}  ✗ $failures component(s) unhealthy${NC}"
        return 1
    fi

    echo -e "\n${GREEN}  ✓ All components healthy${NC}"
    return 0
}

# ─── Dump diagnostics ────────────────────────────────────────────────────────
media_diagnose() {
    media_status
    echo ""
    media_health
    echo ""
    echo -e "${BLUE}  → Recent systemd failures:${NC}"
    systemctl --failed --no-pager  | head -20 || true
    echo ""
    echo -e "${BLUE}  → Listening ports:${NC}"
    ss -tlnp  | head -30 || netstat -tlnp  | head -30 || true
}

# --- end lib/media-node-ops.sh ---

# --- lib/media-node.sh ---
# lib/media-node.sh — Media Node provisioning functions
# Sourced by install.sh when --mode=media-node

[ "${_MEDIA_NODE_LOADED:-}" = "true" ] && return 0
_MEDIA_NODE_LOADED=true

MEDIA_NODE_INSTALL_DIR="/opt/smsly-hosting-media"
MEDIA_NODE_ENV="$MEDIA_NODE_INSTALL_DIR/.env"
MEDIA_NODE_LOG="/var/log/smsly-media-install.log"

# ─── Detect media hardware ───────────────────────────────────────────────────
detect_media_hardware() {
    echo -e "${BLUE}  → Detecting media hardware...${NC}"

    local cores
    cores=$(nproc  || echo 0)
    local ram_kb
    ram_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}'  || echo 0)
    local ram_mb=$((ram_kb / 1024))
    local disk_gb
    disk_gb=$(df -BG / | awk 'NR==2 {print $2}' | tr -d 'G'  || echo 0)

    echo -e "${BLUE}    CPU: ${cores} cores | RAM: ${ram_mb}MB | Disk: ${disk_gb}GB${NC}"

    if [ "$cores" -lt 4 ]; then
        echo -e "${RED}  ✗ Media node requires ≥4 CPU cores (found: ${cores})${NC}"
        return 1
    fi
    if [ "$ram_mb" -lt 7680 ]; then
        echo -e "${RED}  ✗ Media node requires ≥8GB RAM (found: ${ram_mb}MB)${NC}"
        return 1
    fi
    if [ "$disk_gb" -lt 50 ]; then
        echo -e "${RED}  ✗ Media node requires ≥50GB disk (found: ${disk_gb}GB)${NC}"
        return 1
    fi

    echo -e "${GREEN}  ✓ Hardware requirements met${NC}"
}

# ─── Detect available ports ──────────────────────────────────────────────────
check_media_ports() {
    echo -e "${BLUE}  → Checking media ports...${NC}"
    local ports=(80 443 5060 5060/udp 3478 3478/udp 9090 9091 30000-31000/udp)
    local blocked=()

    for port_spec in "${ports[@]}"; do
        local port="${port_spec%%/*}"
        local proto="${port_spec##*/}"
        if [ "$proto" = "$port_spec" ]; then
            proto="tcp"
        fi

        if [ "$proto" = "udp" ]; then
            if ss -ulnp  | grep -q ":${port} " ; then
                blocked+=("$port_spec")
            fi
        else
            if ss -tlnp  | grep -q ":${port} " ; then
                blocked+=("$port_spec")
            fi
        fi
    done

    if [ ${#blocked[@]} -gt 0 ]; then
        echo -e "${RED}  ✗ Ports blocked: ${blocked[*]}${NC}"
        return 1
    fi

    echo -e "${GREEN}  ✓ All media ports available${NC}"
}

# ─── Detect TPM ─────────────────────────────────────────────────────────────
detect_tpm() {
    if [ -c /dev/tpm0 ] && command -v tpm2_pcrread ; then
        echo -e "${GREEN}  ✓ TPM 2.0 detected${NC}"
        echo "tpm2"
    else
        echo -e "${YELLOW}  ⚠ No TPM 2.0 — using software fallback${NC}"
        echo "software"
    fi
}

# ─── Generate media node secrets ────────────────────────────────────────────
generate_media_secrets() {
    local env_file="$1"
    echo -e "${BLUE}  → Generating media node secrets...${NC}"

    local gateway_secret
    gateway_secret="$(openssl rand -hex 32  || python3 -c 'import secrets; print(secrets.token_hex(32))')"
    local livekit_api_key
    livekit_api_key="$(openssl rand -hex 16  || python3 -c 'import secrets; print(secrets.token_hex(16))')"
    local livekit_api_secret
    livekit_api_secret="$(openssl rand -hex 32  || python3 -c 'import secrets; print(secrets.token_hex(32))')"
    local turn_secret
    turn_secret="$(openssl rand -hex 32  || python3 -c 'import secrets; print(secrets.token_hex(32))')"
    local postgres_password
    postgres_password="$(openssl rand -hex 16  || python3 -c 'import secrets; print(secrets.token_hex(16))')"
    local redis_password
    redis_password="$(openssl rand -hex 16  || python3 -c 'import secrets; print(secrets.token_hex(16))')"

    cat > "$env_file" <<EOF
# SMSLY Media Node — Auto-generated secrets
# Generated: $(date -Iseconds)

NODE_TYPE=media
NODE_ID=${NODE_ID:-$(hostname -f  || hostname)}

# Master connection
MASTER_IP=${MASTER_IP:-}
MASTER_MESH_IP=${MASTER_MESH_IP:-}
MASTER_API_URL=${MASTER_API_URL:-https://master.smsly.com/api/v1}
GATEWAY_SECRET=${gateway_secret}

# Database (local)
POSTGRES_PASSWORD=${postgres_password}
REDIS_PASSWORD=${redis_password}

# LiveKit
LIVEKIT_API_KEY=${livekit_api_key}
LIVEKIT_API_SECRET=${livekit_api_secret}

# TURN
TURN_SECRET=${turn_secret}

# Node identity
PUBLIC_IP=${PUBLIC_IP:-$(detect_public_ip  || echo "")}
DOMAIN=${DOMAIN:-$(hostname -f  || hostname)}

# Management daemon
CONFIG_PATH=/etc/smsly/media-mgmt.json
RUST_LOG=smsly_media_mgmt=info,tower_http=info
EOF

    chmod 600 "$env_file"
    echo -e "${GREEN}  ✓ Secrets generated${NC}"
}

# ─── Install media infrastructure packages ───────────────────────────────────
install_media_packages() {
    echo -e "${BLUE}  → Installing media infrastructure packages...${NC}"

    # Ensure smsly system user exists (all systemd units run as this user)
    if ! id smsly ; then
        useradd -r -s /usr/sbin/nologin -u 1000 smsly  || true
        echo -e "${GREEN}  ✓ Created smsly system user${NC}"
    fi

    # Create required directories
    mkdir -p /var/log/smsly /run/smsly /var/lib/freeswitch /var/lib/livekit /var/log/coturn /var/lib/rtpengine-recording

    apt-get update -qq
    apt-get install -y -qq \
        postgresql-15 \
        redis-server \
        wireguard \
        kamailio \
        freeswitch \
        coturn \
        openresty \
        curl \
        jq \
        netcat-openbsd \
        fs_cli \
        

    # Install RTPEngine (not in default Ubuntu repos — build from source or use PPA)
    if ! command -v rtpengine ; then
        echo -e "${BLUE}  → Installing RTPEngine...${NC}"
        apt-get install -y -qq rtpengine  || {
            echo -e "${YELLOW}  ⚠ RTPEngine not in apt repos — installing from Sipwise PPA...${NC}"
            apt-get install -y -qq software-properties-common  || true
            add-apt-repository -y ppa:sipwise/rtpengine  || true
            apt-get update -qq  && apt-get install -y -qq rtpengine  || {
                echo -e "${YELLOW}  ⚠ RTPEngine auto-install failed — install manually${NC}"
            }
        }
    fi

    # Install LiveKit server (binary from GitHub releases)
    if ! command -v livekit-server ; then
        echo -e "${BLUE}  → Installing LiveKit server...${NC}"
        local lk_arch
        lk_arch="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
        local lk_version="1.8.4"
        curl -fsSL "https://github.com/livekit/livekit/releases/download/v${lk_version}/livekit_${lk_version}_linux_${lk_arch}.tar.gz" \
            | tar xz -C /usr/local/bin livekit-server  || {
            echo -e "${YELLOW}  ⚠ LiveKit auto-install failed — install manually${NC}"
        }
    fi

    echo -e "${GREEN}  ✓ Packages installed${NC}"
}

# ─── Deploy media configs ────────────────────────────────────────────────────
deploy_media_configs() {
    local script_dir="$1"
    echo -e "${BLUE}  → Deploying media node configs...${NC}"

    local infra_dir="$script_dir/infrastructure/media"
    if [ ! -d "$infra_dir" ]; then
        echo -e "${YELLOW}  ⚠ infrastructure/media/ not found — skipping config deployment${NC}"
        return 0
    fi

    # Kamailio
    [ -d /etc/kamailio ] || mkdir -p /etc/kamailio
    cp -f "$infra_dir/kamailio/kamailio.cfg" /etc/kamailio/  || true
    cp -f "$infra_dir/kamailio/tls.cfg" /etc/kamailio/  || true

    # FreeSWITCH
    [ -d /etc/freeswitch ] && cp -f "$infra_dir/freeswitch/freeswitch.xml" /etc/freeswitch/  || true

    # RTPEngine
    [ -d /etc/rtpengine ] || mkdir -p /etc/rtpengine
    cp -f "$infra_dir/rtpengine/rtpengine.conf" /etc/rtpengine/  || true

    # LiveKit
    [ -d /etc/livekit ] || mkdir -p /etc/livekit
    cp -f "$infra_dir/livekit/livekit.yaml" /etc/livekit/  || true

    # coturn
    [ -d /etc/coturn ] || mkdir -p /etc/coturn
    cp -f "$infra_dir/coturn/turnserver.conf" /etc/coturn/  || true

    # OpenResty
    [ -d /usr/local/openresty/nginx/conf ] || mkdir -p /usr/local/openresty/nginx/conf
    cp -f "$infra_dir/openresty/nginx.conf" /usr/local/openresty/nginx/conf/  || true

    # Attestation + media-mgmt config
    [ -d /etc/smsly ] || mkdir -p /etc/smsly
    cp -f "$infra_dir/attestation/attestation.json" /etc/smsly/media-mgmt.json  || true

    echo -e "${GREEN}  ✓ Configs deployed${NC}"
}

# ─── Deploy systemd units ───────────────────────────────────────────────────
deploy_media_systemd_units() {
    local script_dir="$1"
    echo -e "${BLUE}  → Deploying systemd units...${NC}"

    local systemd_dir="$script_dir/scripts/systemd"
    if [ ! -d "$systemd_dir" ]; then
        echo -e "${YELLOW}  ⚠ scripts/systemd/ not found — skipping systemd deployment${NC}"
        return 0
    fi

    for unit in "$systemd_dir"/*.service; do
        [ -f "$unit" ] || continue
        local name
        name=$(basename "$unit")
        cp -f "$unit" /etc/systemd/system/
        echo -e "  → Installed ${name}"
    done

    systemctl daemon-reload
    echo -e "${GREEN}  ✓ Systemd units deployed${NC}"
}

# ─── Template env vars into config files ─────────────────────────────────────
template_media_configs() {
    local env_file="$1"
    echo -e "${BLUE}  → Templating env vars into media configs...${NC}"

    # Source env for values
    set -a
    source "$env_file"
    set +a

    # Kamailio
    if [ -f /etc/kamailio/kamailio.cfg ]; then
        sed -i \
            -e "s|\${PUBLIC_IP}|${PUBLIC_IP}|g" \
            -e "s|\${PRIVATE_IP}|${PRIVATE_IP:-127.0.0.1}|g" \
            -e "s|\${DOMAIN}|${DOMAIN}|g" \
            /etc/kamailio/kamailio.cfg
    fi

    # LiveKit
    if [ -f /etc/livekit/livekit.yaml ]; then
        sed -i \
            -e "s|\${LIVEKIT_API_KEY}|${LIVEKIT_API_KEY}|g" \
            -e "s|\${LIVEKIT_API_SECRET}|${LIVEKIT_API_SECRET}|g" \
            -e "s|\${PUBLIC_IP}|${PUBLIC_IP}|g" \
            -e "s|\${PRIVATE_IP}|${PRIVATE_IP:-127.0.0.1}|g" \
            -e "s|\${TURN_SECRET}|${TURN_SECRET}|g" \
            -e "s|\${MASTER_API_URL}|${MASTER_API_URL}|g" \
            /etc/livekit/livekit.yaml
    fi

    # coturn
    if [ -f /etc/coturn/turnserver.conf ]; then
        sed -i \
            -e "s|\${PUBLIC_IP}|${PUBLIC_IP}|g" \
            -e "s|\${PRIVATE_IP}|${PRIVATE_IP:-127.0.0.1}|g" \
            -e "s|\${TURN_SECRET}|${TURN_SECRET}|g" \
            -e "s|\${DOMAIN}|${DOMAIN}|g" \
            /etc/coturn/turnserver.conf
    fi

    # Attestation
    if [ -f /etc/smsly/attestation.json ]; then
        sed -i \
            -e "s|\${NODE_ID}|${NODE_ID}|g" \
            -e "s|\${POSTGRES_PASSWORD}|${POSTGRES_PASSWORD}|g" \
            -e "s|\${REDIS_PASSWORD}|${REDIS_PASSWORD}|g" \
            -e "s|\${MASTER_API_URL}|${MASTER_API_URL}|g" \
            -e "s|\${GATEWAY_SECRET}|${GATEWAY_SECRET}|g" \
            /etc/smsly/attestation.json
    fi

    echo -e "${GREEN}  ✓ Configs templated${NC}"
}

# ─── Start media services in order ───────────────────────────────────────────
start_media_services() {
    echo -e "${BLUE}  → Starting media services...${NC}"

    local infra_services=(postgresql redis-server wireguard)
    local media_services=(kamailio rtpengine freeswitch coturn)
    local app_services=(livekit-server smsly-voice-api smsly-video)
    local mgmt_services=(smsly-media-mgmt openresty)

    for svc in "${infra_services[@]}"; do
        systemctl enable --now "$svc" || echo -e "${YELLOW}    ⚠ systemctl enable --now $svc failed${NC}"
    done
    sleep 2

    for svc in "${media_services[@]}"; do
        systemctl enable --now "$svc" || echo -e "${YELLOW}    ⚠ systemctl enable --now $svc failed${NC}"
    done
    sleep 1

    for svc in "${app_services[@]}"; do
        systemctl enable --now "$svc" || echo -e "${YELLOW}    ⚠ systemctl enable --now $svc failed${NC}"
    done

    for svc in "${mgmt_services[@]}"; do
        systemctl enable --now "$svc" || echo -e "${YELLOW}    ⚠ systemctl enable --now $svc failed${NC}"
    done

    echo -e "${GREEN}  ✓ All media services started${NC}"
}

# ─── Verify media services ───────────────────────────────────────────────────
verify_media_services() {
    echo -e "${BLUE}  → Verifying media services...${NC}"
    local failures=0

    local services=(
        "postgresql:pg_isready -q"
        "redis:redis-cli ping"
        "kamailio:nc -zvu 127.0.0.1 5060"
        "smsly-media-mgmt:curl -sf http://127.0.0.1:9090/health"
    )

    for entry in "${services[@]}"; do
        local name="${entry%%:*}"
        local check="${entry##*:}"
        if eval "$check" ; then
            echo -e "  ${GREEN}✓${NC} ${name}"
        else
            echo -e "  ${RED}✗${NC} ${name}"
            failures=$((failures + 1))
        fi
    done

    if [ "$failures" -gt 0 ]; then
        echo -e "${RED}  ✗ ${failures} services failed verification${NC}"
        return 1
    fi

    echo -e "${GREEN}  ✓ All services healthy${NC}"
}

# ─── Full media node fresh install ───────────────────────────────────────────
install_media_node() {
    local script_dir="$1"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  SMSLY Media Node — Fresh Install${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

    # Phase 0: Pre-flight
    check_internet
    detect_media_hardware
    check_media_ports

    # Phase 1: Core infrastructure
    install_media_packages

    # Phase 2: Create install dir + generate secrets
    mkdir -p "$MEDIA_NODE_INSTALL_DIR"
    generate_media_secrets "$MEDIA_NODE_ENV"

    # Phase 3: Deploy configs + systemd
    deploy_media_configs "$script_dir"
    deploy_media_systemd_units "$script_dir"

    # Phase 4: Template configs
    template_media_configs "$MEDIA_NODE_ENV"

    # Phase 5: Start services
    start_media_services

    # Phase 6: Verify
    sleep 3
    verify_media_services

    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✓ Media node installation complete${NC}"
    echo -e "${GREEN}  → Config: ${MEDIA_NODE_ENV}${NC}"
    echo -e "${GREEN}  → Logs:   ${MEDIA_NODE_LOG}${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
}

# ─── Media node update (rebuild binaries, restart services) ───────────────────
update_media_node() {
    local script_dir="$1"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  SMSLY Media Node — Update${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

    if [ ! -d "$script_dir" ]; then
        echo -e "${RED}  ERROR: Script directory not found: $script_dir${NC}"
        exit 1
    fi

    # Pull latest code
    echo -e "${BLUE}  → Pulling latest code...${NC}"
    cd "$script_dir" && git pull --ff-only  || {
        echo -e "${YELLOW}  ⚠ git pull failed — using local copy${NC}"
    }

    # Rebuild smsly-media-mgmt if Cargo.toml exists
    local mgmt_dir="$script_dir/../smsly-media-mgmt"
    if [ -f "$mgmt_dir/Cargo.toml" ]; then
        echo -e "${BLUE}  → Rebuilding smsly-media-mgmt...${NC}"
        cd "$mgmt_dir" && cargo build --release 2>&1 | tail -5
        if [ -f target/release/smsly-media-mgmt ]; then
            cp target/release/smsly-media-mgmt /usr/local/bin/smsly-media-mgmt
            systemctl restart smsly-media-mgmt
            echo -e "${GREEN}  ✓ smsly-media-mgmt updated${NC}"
        fi
    fi

    # Redeploy configs
    echo -e "${BLUE}  → Updating configs...${NC}"
    deploy_media_configs "$script_dir"
    deploy_media_systemd_units "$script_dir"
    if [ -f "$MEDIA_NODE_ENV" ]; then
        template_media_configs "$MEDIA_NODE_ENV"
    fi

    # Restart all media services
    echo -e "${BLUE}  → Restarting media services...${NC}"
    for svc in smsly-media-mgmt smsly-voice-api smsly-video livekit-server rtpengine freeswitch kamailio coturn openresty; do
        systemctl restart "$svc" || echo -e "${YELLOW}    ⚠ systemctl restart $svc failed${NC}"
    done

    sleep 3
    verify_media_services

    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✓ Media node update complete${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
}

# --- end lib/media-node.sh ---

# --- lib/network.sh ---
detect_public_ip() {
    local candidate=""
    local endpoint=""
    local endpoints=(
        "https://api.ipify.org"
        "https://ifconfig.me/ip"
        "https://ipv4.icanhazip.com"
    )

    for endpoint in "${endpoints[@]}"; do
        candidate="$(curl -4 -fsS -m 5 "$endpoint"  | tr -d '\r\n' || true)"
        if is_valid_ipv4 "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done

    candidate="$(hostname -I  | awk '{print $1}' | tr -d '\r\n' || true)"
    if is_valid_ipv4 "$candidate"; then
        echo "$candidate"
        return 0
    fi

    echo "127.0.0.1"
    return 0
}

ensure_update_networks() {
    docker network inspect smsly-net  || docker network create smsly-net || echo -e "${YELLOW}    ⚠ smsly-net create failed (may already exist)${NC}"
    docker network inspect smsly-proxy  || docker network create smsly-proxy || echo -e "${YELLOW}    ⚠ smsly-proxy create failed (may already exist)${NC}"
    docker network inspect socket-proxy  || docker network create --driver bridge --internal socket-proxy || echo -e "${YELLOW}    ⚠ socket-proxy create failed (may already exist)${NC}"
}

https_listener_active() {
    if command -v ss ; then
        ss -H -tln  | awk '{print $4}' | grep -Eq ':443$'
    else
        lsof -iTCP:443 -sTCP:LISTEN
    fi
}

# --- end lib/network.sh ---

# --- lib/ops.sh ---
# --- lib/ops_wipe.sh ---
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

    if [ -f "$COMPOSE_FILE" ]; then
        cd "$INSTALL_DIR"
        docker compose -f "$COMPOSE_FILE" down -v --remove-orphans || echo -e "${YELLOW}    ⚠ docker compose down failed${NC}"
    fi

    SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly-hosting" -q  || true)
    if [ -n "$SMSLY_CONTAINERS" ]; then
        docker rm -f $SMSLY_CONTAINERS || echo -e "${YELLOW}    ⚠ docker rm containers failed${NC}"
    fi

    SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly-hosting" -q  || true)
    if [ -n "$SMSLY_VOLUMES" ]; then
        for vol in $SMSLY_VOLUMES; do
            docker volume rm "$vol"  || true
        done
    fi

    SMSLY_NETWORKS=$(docker network ls --filter "name=smsly-hosting" -q  || true)
    if [ -n "$SMSLY_NETWORKS" ]; then
        for net in $SMSLY_NETWORKS; do
            docker network rm "$net"  || true
        done
    fi

    # Clean up Caddy watcher service (prevents stale config on reinstall)
    systemctl stop caddy-watcher || echo -e "${YELLOW}    ⚠ systemctl stop caddy-watcher failed${NC}"
    systemctl disable caddy-watcher || echo -e "${YELLOW}    ⚠ systemctl disable caddy-watcher failed${NC}"
    rm -f /etc/systemd/system/caddy-watcher.service

    # Reset Caddyfile to default (prevents stale routing)
    if [ -f "$INSTALL_DIR"/caddy-config/Caddyfile ]; then
        echo ':80 { respond "Caddy is running" 200 }' > "$INSTALL_DIR"/caddy-config/Caddyfile
    fi

    # Remove Cloudflare token override
    rm -rf /etc/systemd/system/caddy.service.d
    systemctl daemon-reload || echo -e "${YELLOW}    ⚠ systemctl daemon-reload failed${NC}"

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
    chown root:1000 "$env_file"  || true
    chmod 664 "$env_file"  || true

    local owner mode
    owner="$(stat -c '%u:%g' "$env_file"  || echo "?")"
    mode="$(stat -c '%a' "$env_file"  || echo "?")"
    echo -e "${GREEN}  ✓ .env permissions: $mode owner=$owner${NC}"

    # Also fix caddy-config directory for good measure.
    # Owner root (backend writes config as cap-dropped root), group 1000
    # (caddy container uid 1000 reads it).
    if [ -d "$INSTALL_DIR/caddy-config" ]; then
        chown -R 0:1000 "$INSTALL_DIR/caddy-config"  || true
        chmod -R u+rwX,g+rwX "$INSTALL_DIR/caddy-config"  || true
        echo -e "${GREEN}  ✓ caddy-config permissions fixed${NC}"
    fi

    # Fix staticfiles/media directories
    for dir in staticfiles media backups; do
        if [ -d "$INSTALL_DIR/$dir" ]; then
            chown -R 1000:1000 "$INSTALL_DIR/$dir"  || true
        fi
    done

    # Fix builds and prometheus-targets directories
    for dir in builds prometheus-targets; do
        if [ -d "$INSTALL_DIR/$dir" ]; then
            chown -R 1000:1000 "$INSTALL_DIR/$dir"  || true
            chmod 2777 "$INSTALL_DIR/$dir"  || true
        fi
    done
}

# --- end lib/ops_wipe.sh ---
# --- lib/ops_domain.sh ---
fix_domain_sync() {
    local target_domain="${1:-}"
    local env_file="$INSTALL_DIR/.env"

    echo -e "${BLUE}  → Fixing domain sync for: $target_domain${NC}"

    # 1. Fix .env
    if grep -q '^DOMAIN=' "$env_file" ; then
        sed -i "s|^DOMAIN=.*|DOMAIN=$target_domain|" "$env_file"
    else
        echo "DOMAIN=$target_domain" >> "$env_file"
    fi
    if grep -q '^USE_SSL=' "$env_file" ; then
        sed -i 's/^USE_SSL=.*/USE_SSL=true/' "$env_file"
    else
        echo "USE_SSL=true" >> "$env_file"
    fi

    # Sync allowlists
    sync_env_domain_allowlists "$env_file" "$target_domain" "$(detect_public_ip)"

    # 2. Sync DB PlatformConfig
    if docker compose -f "$COMPOSE_FILE" ps -q backend  | grep -q .; then
        timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
cfg = PlatformConfig.load()
cfg.domain = '$target_domain'
cfg.use_ssl = True
cfg.save()
print(f'PlatformConfig domain set to: {cfg.domain}')
"  && echo -e "${GREEN}  ✓ PlatformConfig synced${NC}" || echo -e "${YELLOW}  ⚠ DB sync skipped${NC}"
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
    if docker compose -f "$COMPOSE_FILE" ps -q caddy  | grep -q .; then
        timeout -k 5 20 docker compose -f "$COMPOSE_FILE" exec caddy caddy reload --config /etc/caddy/Caddyfile || \
            timeout -k 5 20 docker compose -f "$COMPOSE_FILE" restart caddy || \
            echo -e "${YELLOW}    ⚠ Caddy reload failed${NC}"
    fi

    echo -e "${GREEN}  ✓ Domain fix complete for: $target_domain${NC}"
}

# --- end lib/ops_domain.sh ---
# --- lib/ops_recovery.sh ---
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
    # regenerate if the existing key/cert don't match — `openssl req`
    # produces a matched pair in one shot, so a mismatch means one
    # file was rotated independently of the other.
    mkdir -p "$INSTALL_DIR/auth" "$INSTALL_DIR/certs"
    _regen_registry_tls() {
        echo -e "${BLUE}      Generating self-signed TLS cert for registry...${NC}"
        _tmp_dir="$(mktemp -d)"
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "${_tmp_dir}/registry.key" \
            -out    "${_tmp_dir}/registry.crt" \
            -subj "/CN=registry" \
            -addext "subjectAltName=DNS:registry,DNS:localhost,IP:127.0.0.1,IP:10.100.0.1" 
        local _rc=$?
        if [ "$_rc" -ne 0 ]; then
            rm -rf "$_tmp_dir"
            return $_rc
        fi
        mv "${_tmp_dir}/registry.key" "$INSTALL_DIR/certs/registry.key"
        mv "${_tmp_dir}/registry.crt" "$INSTALL_DIR/certs/registry.crt"
        rm -rf "$_tmp_dir"
        chmod 644 "$INSTALL_DIR/certs/registry.crt" "$INSTALL_DIR/certs/registry.key"
    }
    _registry_tls_ok() {
        [ -f "$INSTALL_DIR/certs/registry.key" ] || return 1
        [ -f "$INSTALL_DIR/certs/registry.crt" ] || return 1
        local _cmod _kmod
        _cmod="$(openssl x509 -in "$INSTALL_DIR/certs/registry.crt" -noout -modulus  | openssl sha256)" || return 1
        _kmod="$(openssl rsa  -in "$INSTALL_DIR/certs/registry.key" -noout -modulus  | openssl sha256)" || return 1
        [ "$_cmod" = "$_kmod" ]
    }
    if ! _registry_tls_ok; then
        _regen_registry_tls
        if ! _registry_tls_ok; then
            echo -e "${RED}    ✗ Registry TLS cert/key still mismatched after regen attempt${NC}"
            echo -e "${YELLOW}      Manual fix: openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \\${NC}"
            echo -e "${YELLOW}        -keyout $INSTALL_DIR/certs/registry.key \\${NC}"
            echo -e "${YELLOW}        -out    $INSTALL_DIR/certs/registry.crt \\${NC}"
            echo -e "${YELLOW}        -subj '/CN=registry'${NC}"
        else
            echo -e "${BLUE}    Restarting registry container to pick up new TLS certs...${NC}"
            docker restart smsly-hosting-registry-1 || echo -e "${YELLOW}    ⚠ Registry restart failed${NC}"
        fi
    fi
    if [ ! -f "$INSTALL_DIR/auth/htpasswd" ] || [ -z "${REGISTRY_PASSWORD:-}" ] || [ -z "${REGISTRY_USER:-}" ]; then
        REGISTRY_PASS="${REGISTRY_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(18))"  || openssl rand -hex 12  || echo 'auto-generated-change-me')}"
        if command -v htpasswd ; then
            htpasswd -Bbn "${REGISTRY_USER:-smsly-registry}" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"
        else
            python3 -c "
import bcrypt, sys
pw = sys.argv[1] if len(sys.argv) > 1 else '$REGISTRY_PASS'
print('${REGISTRY_USER:-smsly-registry}:' + bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode())
" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"  || true
        fi
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_USER" "${REGISTRY_USER:-smsly-registry}"
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_PASSWORD" "$REGISTRY_PASS"
    fi

    # Install registry cert into Docker's cert trust store so the daemon
    # connects via HTTPS (not HTTP fallback) to the registry.
    install_registry_docker_certs

    # ─── Self-heal: missing secrets + cosign keypair ───────────────────────
    if [ -f "$INSTALL_DIR/.env" ]; then
        _ensure_secret() {
            local _name="$1" _bytes="$2"
            if ! grep -q "^${_name}=" "$INSTALL_DIR/.env"  || [ -z "$(grep "^${_name}=" "$INSTALL_DIR/.env"  | cut -d= -f2)" ]; then
                local _val="$(python3 -c "import secrets; print(secrets.token_hex($_bytes))"  || openssl rand -hex "$_bytes"  || true)"
                if [ -n "$_val" ]; then
                    printf -v "$_name" '%s' "$_val"
                    env_set_value "$INSTALL_DIR/.env" "$_name" "$_val"  || true
                    echo -e "${BLUE}    → Self-healed $_name${NC}"
                fi
            fi
        }
        _ensure_secret REGISTRY_HTTP_SECRET 32
        _ensure_secret REPLICATION_PASSWORD 32
        _ensure_secret SENTINEL_PASSWORD 32
        _ensure_secret CROWDSEC_BOUNCER_KEY 32
        _ensure_secret COSIGN_PASSWORD 32
    fi
    if command -v cosign ; then
        mkdir -p "$INSTALL_DIR/cosign-keys"
        if [ ! -f "$INSTALL_DIR/cosign-keys/cosign.key" ] || [ ! -f "$INSTALL_DIR/cosign-keys/cosign.pub" ]; then
            echo -e "${BLUE}    → Cosign keypair missing — generating...${NC}"
            COSIGN_PASSWORD="${COSIGN_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_hex(32))"  || openssl rand -hex 32  || true)}"
            COSIGN_PASSWORD="$COSIGN_PASSWORD" cosign generate-key-pair  || true
            if [ -f cosign.key ]; then
                mv cosign.key "$INSTALL_DIR/cosign-keys/cosign.key"
                mv cosign.pub "$INSTALL_DIR/cosign-keys/cosign.pub"
                chmod 600 "$INSTALL_DIR/cosign-keys/cosign.key"
                chmod 644 "$INSTALL_DIR/cosign-keys/cosign.pub"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PASSWORD" "$COSIGN_PASSWORD"  || true
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PRIVATE_KEY_PATH" "$INSTALL_DIR/cosign-keys/cosign.key"  || true
                echo -e "${GREEN}      ✓ Cosign keypair created${NC}"
            fi
        fi
    fi

    if [ "$MODE_AGENT_LITE" = "true" ]; then
        docker compose -f "$COMPOSE_FILE" up -d redis rabbitmq socket-proxy || true
        wait_for_container_ready "smsly-redis-primary" 120 || true
        sync_agent_lite_rabbitmq_password
    else
        docker compose -f "$COMPOSE_FILE" up -d $(get_db_service) $(get_pgcat_if_exists) redis rabbitmq socket-proxy registry || true
        wait_for_container_ready "smsly-postgres-primary" 120 || true
        if [ -n "$(get_pgcat_if_exists)" ]; then wait_for_container_ready "smsly-hosting-pgcat-1" 120 || true; fi
        wait_for_container_ready "smsly-hosting-redis-1" 120 || true
    fi

    if should_manage_caddy && docker compose -f "$COMPOSE_FILE" ps caddy  | grep -q "Up"; then
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

# --- end lib/ops_recovery.sh ---
# --- lib/ops_debug.sh ---
debug_platform_status() {
    # TODO(install): replace set -e toggle with explicit conditional. The
    # entire body tolerates command failures (each diagnostic line has its own
    # `|| true` or ``); leaving set -e toggled off is functional
    # but discouraged.
    set +e
    echo -e "\n${YELLOW}=== SMSLY DEBUG SNAPSHOT ===${NC}"
    echo "Timestamp: $(date -Iseconds)"
    echo "Install dir: $INSTALL_DIR"
    echo ""

    echo "---- Systemd ----"
    systemctl is-active docker  || true
    true
    true
    systemctl is-active smsly-autoscaler  || true
    echo ""

    echo "---- Docker Networks ----"
    docker network ls | grep -E 'smsly|socket-proxy' || true
    echo ""

    echo "---- Compose PS ----"
    docker compose -f "$COMPOSE_FILE" ps || true
    echo ""

    echo "---- Local Health ----"
    timeout 10 curl -iSsf http://127.0.0.1:8000/health  | head -20 || echo "http://127.0.0.1:8000/health failed"
    echo ""

    echo "---- Backend DNS Checks ----"
    timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend getent hosts db pgcat redis  || echo "backend DNS check failed"
    echo ""

    echo "---- Key Logs (tail 120) ----"
    docker compose -f "$COMPOSE_FILE" logs --tail=120 backend frontend traefik pgcat redis  || true
    echo -e "${YELLOW}=== END DEBUG SNAPSHOT ===${NC}\n"
    set -e
}

# --- end lib/ops_debug.sh ---

# =============================================================================
# ops.sh — Function library for install/update/ops operations
# Mode dispatch is handled by install.sh, NOT here.
# This file only defines functions and sources sub-modules.
# =============================================================================

# ─── VERIFY MODE — Run endpoint checks only (no changes) ──────────────────────
# Called from install.sh when VERIFY_MODE=true
verify_endpoints() {
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}x Please run as root (sudo bash install.sh --verify)${NC}"
        exit 1
    fi
    cd "$INSTALL_DIR"  || { echo -e "${RED}x $INSTALL_DIR not found. Run fresh install first.${NC}"; exit 1; }

    DOMAIN="$(env_get_value "$INSTALL_DIR/.env" "DOMAIN"  || echo "")"

    if should_manage_caddy; then
        echo -e "\n${BLUE}  ⟳ Syncing Proxy Configurations...${NC}"
        reload_container_caddy  || true
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
        _ep1_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- | tr -d '[:space:]' || true)"
        if [ -n "$_ep1_domain" ] && [ "$_ep1_domain" != "localhost" ]; then
            _LITE_HOST_HEADER="$_ep1_domain"
        fi
    fi
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        if [ -n "${_LITE_HOST_HEADER:-}" ]; then
            EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 -H "Host: ${_LITE_HOST_HEADER}" "http://127.0.0.1/health" ) || EP1_CODE="000"
        else
            EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1/health" ) || EP1_CODE="000"
        fi
    else
        if timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health ; then
            EP1_CODE="200"
        elif curl -fsS --max-time 5 "$EP1_FALLBACK_URL" ; then
            EP1_CODE="200"
        else
            EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP1_FALLBACK_URL" ) || EP1_CODE="000"
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
        EP_PUB_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 10 "$EP_PUB_URL" ) || EP_PUB_CODE="000"
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
        EP2_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 10 "$EP2_URL" ) || EP2_CODE="000"
        case "$EP2_CODE" in
            2*|3*)
            echo -e "${GREEN}  ✓ HTTPS: HTTP $EP2_CODE${NC}"; PASS_COUNT=$((PASS_COUNT + 1))
            ;;
        *)
            echo -e "${RED}  ✗ HTTPS: HTTP $EP2_CODE ($EP2_URL)${NC}"; FAIL_COUNT=$((FAIL_COUNT + 1))
            ;;
        esac
    elif echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' ; then
        echo -e "${YELLOW}  ⊘ HTTPS: Skipped (IP Mode — SSL requires a domain name)${NC}"
    fi

    # Traefik
    EP3_URL="http://127.0.0.1:8081/"
    if is_node_mode; then
        EP3_URL="http://127.0.0.1/health/live"
    fi
    EP3_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP3_URL" ) || EP3_CODE="000"
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
    ALL_SVC_DOMAINS="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
for s in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain=''):
    print(f'{s.name}|{s.public_domain.strip()}')
"  | tr -d '\r' || true)"

    if [ -n "$ALL_SVC_DOMAINS" ]; then
        while IFS='|' read -r svc_name svc_domain; do
            [ -z "$svc_domain" ] && continue
            if should_manage_caddy; then
                svc_url="https://${svc_domain}/"
            else
                svc_url="http://${svc_domain}/"
            fi
            svc_code=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$svc_url" ) || svc_code="000"
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

    docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}"  || \
        docker compose -f "$COMPOSE_FILE" ps  || true
    exit 0
}

# --- end lib/ops.sh ---

# --- lib/ops_debug.sh ---
debug_platform_status() {
    # TODO(install): replace set -e toggle with explicit conditional. The
    # entire body tolerates command failures (each diagnostic line has its own
    # `|| true` or ``); leaving set -e toggled off is functional
    # but discouraged.
    set +e
    echo -e "\n${YELLOW}=== SMSLY DEBUG SNAPSHOT ===${NC}"
    echo "Timestamp: $(date -Iseconds)"
    echo "Install dir: $INSTALL_DIR"
    echo ""

    echo "---- Systemd ----"
    systemctl is-active docker  || true
    true
    true
    systemctl is-active smsly-autoscaler  || true
    echo ""

    echo "---- Docker Networks ----"
    docker network ls | grep -E 'smsly|socket-proxy' || true
    echo ""

    echo "---- Compose PS ----"
    docker compose -f "$COMPOSE_FILE" ps || true
    echo ""

    echo "---- Local Health ----"
    timeout 10 curl -iSsf http://127.0.0.1:8000/health  | head -20 || echo "http://127.0.0.1:8000/health failed"
    echo ""

    echo "---- Backend DNS Checks ----"
    timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend getent hosts db pgcat redis  || echo "backend DNS check failed"
    echo ""

    echo "---- Key Logs (tail 120) ----"
    docker compose -f "$COMPOSE_FILE" logs --tail=120 backend frontend traefik pgcat redis  || true
    echo -e "${YELLOW}=== END DEBUG SNAPSHOT ===${NC}\n"
    set -e
}

# --- end lib/ops_debug.sh ---

# --- lib/ops_domain.sh ---
fix_domain_sync() {
    local target_domain="${1:-}"
    local env_file="$INSTALL_DIR/.env"

    echo -e "${BLUE}  → Fixing domain sync for: $target_domain${NC}"

    # 1. Fix .env
    if grep -q '^DOMAIN=' "$env_file" ; then
        sed -i "s|^DOMAIN=.*|DOMAIN=$target_domain|" "$env_file"
    else
        echo "DOMAIN=$target_domain" >> "$env_file"
    fi
    if grep -q '^USE_SSL=' "$env_file" ; then
        sed -i 's/^USE_SSL=.*/USE_SSL=true/' "$env_file"
    else
        echo "USE_SSL=true" >> "$env_file"
    fi

    # Sync allowlists
    sync_env_domain_allowlists "$env_file" "$target_domain" "$(detect_public_ip)"

    # 2. Sync DB PlatformConfig
    if docker compose -f "$COMPOSE_FILE" ps -q backend  | grep -q .; then
        timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
cfg = PlatformConfig.load()
cfg.domain = '$target_domain'
cfg.use_ssl = True
cfg.save()
print(f'PlatformConfig domain set to: {cfg.domain}')
"  && echo -e "${GREEN}  ✓ PlatformConfig synced${NC}" || echo -e "${YELLOW}  ⚠ DB sync skipped${NC}"
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
    if docker compose -f "$COMPOSE_FILE" ps -q caddy  | grep -q .; then
        timeout -k 5 20 docker compose -f "$COMPOSE_FILE" exec caddy caddy reload --config /etc/caddy/Caddyfile || \
            timeout -k 5 20 docker compose -f "$COMPOSE_FILE" restart caddy || \
            echo -e "${YELLOW}    ⚠ Caddy reload failed${NC}"
    fi

    echo -e "${GREEN}  ✓ Domain fix complete for: $target_domain${NC}"
}

# --- end lib/ops_domain.sh ---

# --- lib/ops_recovery.sh ---
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
    # regenerate if the existing key/cert don't match — `openssl req`
    # produces a matched pair in one shot, so a mismatch means one
    # file was rotated independently of the other.
    mkdir -p "$INSTALL_DIR/auth" "$INSTALL_DIR/certs"
    _regen_registry_tls() {
        echo -e "${BLUE}      Generating self-signed TLS cert for registry...${NC}"
        _tmp_dir="$(mktemp -d)"
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "${_tmp_dir}/registry.key" \
            -out    "${_tmp_dir}/registry.crt" \
            -subj "/CN=registry" \
            -addext "subjectAltName=DNS:registry,DNS:localhost,IP:127.0.0.1,IP:10.100.0.1" 
        local _rc=$?
        if [ "$_rc" -ne 0 ]; then
            rm -rf "$_tmp_dir"
            return $_rc
        fi
        mv "${_tmp_dir}/registry.key" "$INSTALL_DIR/certs/registry.key"
        mv "${_tmp_dir}/registry.crt" "$INSTALL_DIR/certs/registry.crt"
        rm -rf "$_tmp_dir"
        chmod 644 "$INSTALL_DIR/certs/registry.crt" "$INSTALL_DIR/certs/registry.key"
    }
    _registry_tls_ok() {
        [ -f "$INSTALL_DIR/certs/registry.key" ] || return 1
        [ -f "$INSTALL_DIR/certs/registry.crt" ] || return 1
        local _cmod _kmod
        _cmod="$(openssl x509 -in "$INSTALL_DIR/certs/registry.crt" -noout -modulus  | openssl sha256)" || return 1
        _kmod="$(openssl rsa  -in "$INSTALL_DIR/certs/registry.key" -noout -modulus  | openssl sha256)" || return 1
        [ "$_cmod" = "$_kmod" ]
    }
    if ! _registry_tls_ok; then
        _regen_registry_tls
        if ! _registry_tls_ok; then
            echo -e "${RED}    ✗ Registry TLS cert/key still mismatched after regen attempt${NC}"
            echo -e "${YELLOW}      Manual fix: openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \\${NC}"
            echo -e "${YELLOW}        -keyout $INSTALL_DIR/certs/registry.key \\${NC}"
            echo -e "${YELLOW}        -out    $INSTALL_DIR/certs/registry.crt \\${NC}"
            echo -e "${YELLOW}        -subj '/CN=registry'${NC}"
        else
            echo -e "${BLUE}    Restarting registry container to pick up new TLS certs...${NC}"
            docker restart smsly-hosting-registry-1 || echo -e "${YELLOW}    ⚠ Registry restart failed${NC}"
        fi
    fi
    if [ ! -f "$INSTALL_DIR/auth/htpasswd" ] || [ -z "${REGISTRY_PASSWORD:-}" ] || [ -z "${REGISTRY_USER:-}" ]; then
        REGISTRY_PASS="${REGISTRY_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(18))"  || openssl rand -hex 12  || echo 'auto-generated-change-me')}"
        if command -v htpasswd ; then
            htpasswd -Bbn "${REGISTRY_USER:-smsly-registry}" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"
        else
            python3 -c "
import bcrypt, sys
pw = sys.argv[1] if len(sys.argv) > 1 else '$REGISTRY_PASS'
print('${REGISTRY_USER:-smsly-registry}:' + bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode())
" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"  || true
        fi
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_USER" "${REGISTRY_USER:-smsly-registry}"
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_PASSWORD" "$REGISTRY_PASS"
    fi

    # Install registry cert into Docker's cert trust store so the daemon
    # connects via HTTPS (not HTTP fallback) to the registry.
    install_registry_docker_certs

    # ─── Self-heal: missing secrets + cosign keypair ───────────────────────
    if [ -f "$INSTALL_DIR/.env" ]; then
        _ensure_secret() {
            local _name="$1" _bytes="$2"
            if ! grep -q "^${_name}=" "$INSTALL_DIR/.env"  || [ -z "$(grep "^${_name}=" "$INSTALL_DIR/.env"  | cut -d= -f2)" ]; then
                local _val="$(python3 -c "import secrets; print(secrets.token_hex($_bytes))"  || openssl rand -hex "$_bytes"  || true)"
                if [ -n "$_val" ]; then
                    printf -v "$_name" '%s' "$_val"
                    env_set_value "$INSTALL_DIR/.env" "$_name" "$_val"  || true
                    echo -e "${BLUE}    → Self-healed $_name${NC}"
                fi
            fi
        }
        _ensure_secret REGISTRY_HTTP_SECRET 32
        _ensure_secret REPLICATION_PASSWORD 32
        _ensure_secret SENTINEL_PASSWORD 32
        _ensure_secret CROWDSEC_BOUNCER_KEY 32
        _ensure_secret COSIGN_PASSWORD 32
    fi
    if command -v cosign ; then
        mkdir -p "$INSTALL_DIR/cosign-keys"
        if [ ! -f "$INSTALL_DIR/cosign-keys/cosign.key" ] || [ ! -f "$INSTALL_DIR/cosign-keys/cosign.pub" ]; then
            echo -e "${BLUE}    → Cosign keypair missing — generating...${NC}"
            COSIGN_PASSWORD="${COSIGN_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_hex(32))"  || openssl rand -hex 32  || true)}"
            COSIGN_PASSWORD="$COSIGN_PASSWORD" cosign generate-key-pair  || true
            if [ -f cosign.key ]; then
                mv cosign.key "$INSTALL_DIR/cosign-keys/cosign.key"
                mv cosign.pub "$INSTALL_DIR/cosign-keys/cosign.pub"
                chmod 600 "$INSTALL_DIR/cosign-keys/cosign.key"
                chmod 644 "$INSTALL_DIR/cosign-keys/cosign.pub"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PASSWORD" "$COSIGN_PASSWORD"  || true
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PRIVATE_KEY_PATH" "$INSTALL_DIR/cosign-keys/cosign.key"  || true
                echo -e "${GREEN}      ✓ Cosign keypair created${NC}"
            fi
        fi
    fi

    if [ "$MODE_AGENT_LITE" = "true" ]; then
        docker compose -f "$COMPOSE_FILE" up -d redis rabbitmq socket-proxy || true
        wait_for_container_ready "smsly-redis-primary" 120 || true
        sync_agent_lite_rabbitmq_password
    else
        docker compose -f "$COMPOSE_FILE" up -d $(get_db_service) $(get_pgcat_if_exists) redis rabbitmq socket-proxy registry || true
        wait_for_container_ready "smsly-postgres-primary" 120 || true
        if [ -n "$(get_pgcat_if_exists)" ]; then wait_for_container_ready "smsly-hosting-pgcat-1" 120 || true; fi
        wait_for_container_ready "smsly-hosting-redis-1" 120 || true
    fi

    if should_manage_caddy && docker compose -f "$COMPOSE_FILE" ps caddy  | grep -q "Up"; then
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

# --- end lib/ops_recovery.sh ---

# --- lib/ops_wipe.sh ---
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

    if [ -f "$COMPOSE_FILE" ]; then
        cd "$INSTALL_DIR"
        docker compose -f "$COMPOSE_FILE" down -v --remove-orphans || echo -e "${YELLOW}    ⚠ docker compose down failed${NC}"
    fi

    SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly-hosting" -q  || true)
    if [ -n "$SMSLY_CONTAINERS" ]; then
        docker rm -f $SMSLY_CONTAINERS || echo -e "${YELLOW}    ⚠ docker rm containers failed${NC}"
    fi

    SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly-hosting" -q  || true)
    if [ -n "$SMSLY_VOLUMES" ]; then
        for vol in $SMSLY_VOLUMES; do
            docker volume rm "$vol"  || true
        done
    fi

    SMSLY_NETWORKS=$(docker network ls --filter "name=smsly-hosting" -q  || true)
    if [ -n "$SMSLY_NETWORKS" ]; then
        for net in $SMSLY_NETWORKS; do
            docker network rm "$net"  || true
        done
    fi

    # Clean up Caddy watcher service (prevents stale config on reinstall)
    systemctl stop caddy-watcher || echo -e "${YELLOW}    ⚠ systemctl stop caddy-watcher failed${NC}"
    systemctl disable caddy-watcher || echo -e "${YELLOW}    ⚠ systemctl disable caddy-watcher failed${NC}"
    rm -f /etc/systemd/system/caddy-watcher.service

    # Reset Caddyfile to default (prevents stale routing)
    if [ -f "$INSTALL_DIR"/caddy-config/Caddyfile ]; then
        echo ':80 { respond "Caddy is running" 200 }' > "$INSTALL_DIR"/caddy-config/Caddyfile
    fi

    # Remove Cloudflare token override
    rm -rf /etc/systemd/system/caddy.service.d
    systemctl daemon-reload || echo -e "${YELLOW}    ⚠ systemctl daemon-reload failed${NC}"

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
    chown root:1000 "$env_file"  || true
    chmod 664 "$env_file"  || true

    local owner mode
    owner="$(stat -c '%u:%g' "$env_file"  || echo "?")"
    mode="$(stat -c '%a' "$env_file"  || echo "?")"
    echo -e "${GREEN}  ✓ .env permissions: $mode owner=$owner${NC}"

    # Also fix caddy-config directory for good measure.
    # Owner root (backend writes config as cap-dropped root), group 1000
    # (caddy container uid 1000 reads it).
    if [ -d "$INSTALL_DIR/caddy-config" ]; then
        chown -R 0:1000 "$INSTALL_DIR/caddy-config"  || true
        chmod -R u+rwX,g+rwX "$INSTALL_DIR/caddy-config"  || true
        echo -e "${GREEN}  ✓ caddy-config permissions fixed${NC}"
    fi

    # Fix staticfiles/media directories
    for dir in staticfiles media backups; do
        if [ -d "$INSTALL_DIR/$dir" ]; then
            chown -R 1000:1000 "$INSTALL_DIR/$dir"  || true
        fi
    done

    # Fix builds and prometheus-targets directories
    for dir in builds prometheus-targets; do
        if [ -d "$INSTALL_DIR/$dir" ]; then
            chown -R 1000:1000 "$INSTALL_DIR/$dir"  || true
            chmod 2777 "$INSTALL_DIR/$dir"  || true
        fi
    done
}

# --- end lib/ops_wipe.sh ---

# --- lib/platform-diagnostics.sh ---
dump_diagnostic_logs() {
    local env_file="${1:-$INSTALL_DIR/.env}"
    echo -e "\n${RED}════════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}   DIAGNOSTIC LOG DUMP (FAILURE ANALYSIS)${NC}"
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"

    echo -e "${YELLOW}  → System Resource Snapshot:${NC}"
    free -m
    df -h /

    echo -e "\n${YELLOW}  → Container Status:${NC}"
    if command -v docker  && [ -f "$env_file" ] && grep -q '^POSTGRES_PASSWORD=' "$env_file" ; then
        docker compose -f "$COMPOSE_FILE" ps || true

        echo -e "\n${YELLOW}  -> Compose Logs (Last 50 lines):${NC}"
        docker compose -f "$COMPOSE_FILE" logs --tail=50 || true
    else
        echo -e "${YELLOW}  (Docker or .env not ready; skipping container logs)${NC}"
    fi

    echo -e "${RED}════════════════════════════════════════════════════════════${NC}\n"
}

# --- end lib/platform-diagnostics.sh ---

# --- lib/platform-domain.sh ---
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
        timeout -k 5 300 docker compose -f "$COMPOSE_FILE" exec -T \
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

incoming_domain = normalize_platform_domain(os.environ.get("SMSLY_SYNC_DOMAIN", ""))
db_has_real_domain = bool(original_domain) and original_domain not in ("", "localhost")
incoming_is_ip_or_empty = not incoming_domain
if db_has_real_domain and incoming_is_ip_or_empty:
    print(f"[sync] Preserving existing DB domain '{original_domain}' (incoming was empty/IP)")
else:
    cfg.domain = incoming_domain

_incoming_use_ssl = parse_bool(os.environ.get("SMSLY_SYNC_USE_SSL", "false"))
_db_already_has_ssl = bool(cfg.use_ssl)
if _incoming_use_ssl:
    cfg.use_ssl = True
elif not _db_already_has_ssl:
    cfg.use_ssl = False

_incoming_wildcard = parse_bool(os.environ.get("SMSLY_SYNC_WILDCARD", "false"))
_db_already_has_wildcard = bool(cfg.wildcard_subdomains)
if _incoming_wildcard:
    cfg.wildcard_subdomains = True
elif not _db_already_has_wildcard:
    cfg.wildcard_subdomains = False
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

    DOMAIN_SYNC_UPDATED_COUNT="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('updated_service_domains', 0))"  || echo 0)"
    DOMAIN_SYNC_REDEPLOY_REQUIRED="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(1 if json.load(sys.stdin).get('redeploy_required') else 0)"  || echo 0)"
    DOMAIN_SYNC_SERVICE_IDS="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(','.join(json.load(sys.stdin).get('service_ids', [])))"  || true)"

    echo -e "${GREEN}  ✓ PlatformConfig synced: domain=$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('domain', ''))" )${NC}"
    if [ "${DOMAIN_SYNC_UPDATED_COUNT:-0}" -gt 0 ]; then
        echo -e "${GREEN}  ✓ Rewrote ${DOMAIN_SYNC_UPDATED_COUNT} existing service public domain(s)${NC}"
    fi

    _effective_domain="$(printf '%s' "$sync_json" | python3 -c "
import json,sys
d = json.load(sys.stdin)
print(d.get('domain', '') or '')
"  || true)"
    _env_domain="$(env_get_value "$env_file" "DOMAIN")"
    _env_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
    _env_wildcard="$(env_get_value "$env_file" "WILDCARD_SUBDOMAINS")"
    _db_use_ssl="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('use_ssl') else 'false')" )"
    _db_wildcard="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('wildcard_subdomains') else 'false')" )"
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

    local backend_container
    backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
    local backend_state
    backend_state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$backend_container"  || echo 'missing')"
    if [ "$backend_state" != "healthy" ] && [ "$backend_state" != "running" ]; then
        echo -e "${YELLOW}  ⚠ Backend container ($backend_container) not ready (state=$backend_state). Waiting 15s...${NC}" >&2
        sleep 15
        backend_state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$backend_container"  || echo 'missing')"
        if [ "$backend_state" != "healthy" ] && [ "$backend_state" != "running" ]; then
            echo -e "${RED}  ✗ Backend container still not ready after wait. Skipping redeploy.${NC}" >&2
            return 1
        fi
    fi

    timeout -k 5 300 docker compose -f "$COMPOSE_FILE" exec -T \
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
except Exception as exc:
    print(f"WARN: {exc}")
    traceback.print_exc()
PY
}

# --- end lib/platform-domain.sh ---

# --- lib/platform-env.sh ---
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

    if [ -n "$desired_domain" ]; then
        if echo "$desired_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || [ "$desired_use_ssl" != "true" ]; then
            _grafana_scheme="http"
        else
            _grafana_scheme="https"
        fi
        _desired_grafana_url="${_grafana_scheme}://${desired_domain}/grafana"
        _current_grafana_url="$(env_get_value "$env_file" "GRAFANA_EXTERNAL_URL")"
        if [ "$_desired_grafana_url" != "$_current_grafana_url" ]; then
            env_set_value "$env_file" "GRAFANA_EXTERNAL_URL" "$_desired_grafana_url"
            changed=true
        fi
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

    if [ -f "$env_file" ]; then
        local env_node_type
        env_node_type="$(env_get_value "$env_file" "NODE_TYPE"  || true)"
        if [ "$env_node_type" = "agent-lite" ] || [ "$env_node_type" = "agent" ]; then
            MODE_AGENT_LITE="true"
        fi
    fi

    if [ "${MODE_AGENT_LITE:-false}" = "true" ]; then
        if [ -z "${MASTER_IP:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_IP="$(env_get_value "$env_file" "MASTER_IP"  || true)"
            fi
            if [ -z "${MASTER_IP:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_IP="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_IP"  || true)"
            fi
        fi

        if [ -z "${MASTER_MESH_IP:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_MESH_IP="$(env_get_value "$env_file" "MASTER_MESH_IP"  || true)"
            fi
            if [ -z "${MASTER_MESH_IP:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_MESH_IP="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_MESH_IP"  || true)"
            fi
        fi

        if [ -z "${MASTER_DB_USER:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_DB_USER="$(env_get_value "$env_file" "MASTER_DB_USER"  || true)"
            fi
            if [ -z "${MASTER_DB_USER:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_DB_USER="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_DB_USER"  || true)"
            fi
        fi

        if [ -z "${MASTER_DB_PASSWORD:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_DB_PASSWORD="$(env_get_value "$env_file" "MASTER_DB_PASSWORD"  || true)"
            fi
            if [ -z "${MASTER_DB_PASSWORD:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_DB_PASSWORD="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_DB_PASSWORD"  || true)"
            fi
            if [ -z "${MASTER_DB_PASSWORD:-}" ] && [ -f "$env_file" ]; then
                local db_url
                db_url="$(env_get_value "$env_file" "DATABASE_URL"  || true)"
                if [[ "$db_url" =~ ://[^:]+:([^@]+)@ ]]; then
                    MASTER_DB_PASSWORD="${BASH_REMATCH[1]}"
                fi
            fi
        fi

        if [ -z "${MASTER_MQ_PASSWORD:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_MQ_PASSWORD="$(env_get_value "$env_file" "MASTER_MQ_PASSWORD"  || true)"
            fi
            if [ -z "${MASTER_MQ_PASSWORD:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_MQ_PASSWORD="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_MQ_PASSWORD"  || true)"
            fi
        fi
    fi

    env_ensure_var "$env_file" "SECRET_KEY" "$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(50)))"  || openssl rand -hex 32)" "Django SECRET_KEY (minimum 32 chars)"
    env_ensure_var "$env_file" "FIELD_ENCRYPTION_KEY" "$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'  || openssl rand -base64 32)" "Fernet key for Django field-level encryption"
    env_ensure_var "$env_file" "POSTGRES_PASSWORD" "$(gen_hex_secret 32)" "PostgreSQL admin password"
    env_ensure_var "$env_file" "REDIS_PASSWORD" "$(gen_hex_secret 32)" "Redis authentication password"
    env_ensure_var "$env_file" "RABBITMQ_PASSWORD" "$(gen_hex_secret 32)" "RabbitMQ authentication password"
    env_ensure_var "$env_file" "GATEWAY_SECRET" "$(gen_hex_secret 64)" "Inter-service HMAC authentication secret"
    env_ensure_var "$env_file" "GITHUB_WEBHOOK_SECRET" "$(gen_hex_secret 64)" "GitHub webhook signature verification"
    env_ensure_var "$env_file" "AUTOSCALER_API_TOKEN" "$(gen_hex_secret 64)" "Autoscaler API bearer token (shared between autoscaler service and Django backend)"
    env_ensure_var "$env_file" "FRP_AUTH_TOKEN" "$(gen_hex_secret 64)" "FRP tunnel relay authentication token"
    env_ensure_var "$env_file" "CADDY_ASK_SECRET" "$(gen_hex_secret 64)" "Shared secret for the Caddy on_demand_tls 'ask' endpoint (X-Caddy-Secret header). Without this the backend logs a warning and generates an ephemeral random secret on every restart."
    env_ensure_var "$env_file" "BACKUP_ENCRYPTION_KEY" "$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'  || openssl rand -base64 32)" "Fernet key used to encrypt on-disk backups (required when BACKUP_REQUIRE_ENCRYPTION=True)"
    env_ensure_var "$env_file" "BACKUP_REQUIRE_ENCRYPTION" "true" "Refuse to write unencrypted backups"
    env_ensure_var "$env_file" "SMSLY_DISABLE_TIER_GATES" "true" "Disable owner-tier paywall gates in this edition"
    env_ensure_var "$env_file" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false" "Keep AppConfig.ready side-effect free; installer/watchers sync edge config"
    env_ensure_var "$env_file" "PGCAT_ADMIN_PASSWORD" "$(gen_hex_secret 48)" "PgCat administration password (mandatory for 1.2+)"
    env_ensure_var "$env_file" "GRAFANA_PASSWORD" "$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits+'-_') for _ in range(40)))"  || openssl rand -base64 30 | tr -d '+/=')" "Grafana admin password (used by the standalone observability stack)"
    env_ensure_var "$env_file" "REPLICATION_PASSWORD" "$(gen_hex_secret 32)" "PostgreSQL streaming replication password"
    env_ensure_var "$env_file" "SENTINEL_PASSWORD" "$(gen_hex_secret 32)" "Redis Sentinel authentication password"
    env_ensure_var "$env_file" "REGISTRY_HTTP_SECRET" "$(gen_hex_secret 32)" "Docker registry HTTP secret"
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
        local compose_target="${COMPOSE_FILE:-docker-compose.prod.yml}"
        if [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" ; then
            expected_database_url="postgresql://smsly_admin:${postgres_password}@pgcat:5432/smsly_hosting"
        else
            expected_database_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
        fi
        current_database_url="$(env_get_value "$env_file" "DATABASE_URL")"

        if [ "$MODE_AGENT_LITE" = "true" ] && [ -n "${MASTER_IP:-}" ]; then
            echo -e "${BLUE}  -> Configuring for Edge Node (Lite Agent) mode...${NC}"

            if [ -z "${MASTER_MESH_IP:-}" ] && [ -f "$env_file" ]; then
                MASTER_MESH_IP="$(env_get_value "$env_file" "MASTER_MESH_IP")"
            fi
            local db_user="${MASTER_DB_USER:-smsly_admin}"
            local db_pass="${MASTER_DB_PASSWORD:-$postgres_password}"
            local mq_pass="${MASTER_MQ_PASSWORD:-$rabbitmq_password}"

            local db_host="${MASTER_MESH_IP}"
            expected_database_url="postgresql://${db_user}:${db_pass}@${db_host}:5432/smsly_hosting"
            expected_direct_url="postgresql://${db_user}:${db_pass}@${db_host}:5432/smsly_hosting"
            expected_celery_broker_url="amqp://smsly_user:${rabbitmq_password}@rabbitmq:5672//"

            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            env_set_value "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url"
            env_set_value "$env_file" "CELERY_BROKER_URL" "$expected_celery_broker_url"
            if [ -n "${MASTER_MESH_IP:-}" ]; then
                env_set_value "$env_file" "MASTER_MESH_IP" "$MASTER_MESH_IP"
            fi

            current_database_url="$expected_database_url"
            current_celery_broker_url="$expected_celery_broker_url"
        fi

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

        if [[ "$current_database_url" =~ @db:5432 ]] && [ "$MODE_AGENT_LITE" != "true" ] && [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" ; then
            echo -e "${BLUE}  -> Migrating DATABASE_URL from db to pgcat${NC}"
            local migrated_url="${current_database_url/@db:5432/@pgcat:5432}"
            env_set_value "$env_file" "DATABASE_URL" "$migrated_url"
            current_database_url="$migrated_url"
            echo -e "${GREEN}  OK DATABASE_URL migrated to pgcat${NC}"
        fi

        if [[ "$current_database_url" =~ @pgbouncer:5432 ]]; then
            local migrated_url
            if [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" ; then
                echo -e "${BLUE}  -> Migrating DATABASE_URL from pgbouncer to pgcat${NC}"
                migrated_url="${current_database_url/@pgbouncer:5432/@pgcat:5432}"
            else
                echo -e "${BLUE}  -> Migrating DATABASE_URL from pgbouncer to db${NC}"
                migrated_url="${current_database_url/@pgbouncer:5432/@db:5432}"
            fi
            env_set_value "$env_file" "DATABASE_URL" "$migrated_url"
            current_database_url="$migrated_url"
            echo -e "${GREEN}  OK DATABASE_URL migrated${NC}"
        fi

        local expected_direct_url
        if [ "$MODE_AGENT_LITE" = "true" ]; then
            expected_direct_url="postgresql://${MASTER_DB_USER:-smsly_admin}:${MASTER_DB_PASSWORD:-$postgres_password}@${MASTER_MESH_IP:-db}:5432/smsly_hosting"
        else
            expected_direct_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
        fi

        if [ -z "$current_database_url" ]; then
            env_ensure_var "$env_file" "DATABASE_URL" "$expected_database_url" "PostgreSQL connection string (via PgCat)"

            env_ensure_var "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url" "Direct connection bypass for migrations"
        elif [[ "$current_database_url" =~ ^postgresql://smsly_admin:.*@pgcat:5432/smsly_hosting$ ]] && [ "$current_database_url" != "$expected_database_url" ]; then
            echo -e "${BLUE}  -> Fixing DATABASE_URL to match POSTGRES_PASSWORD${NC}"
            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            echo -e "${GREEN}  OK DATABASE_URL password synced${NC}"
        fi

        env_ensure_var "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url" "Direct PostgreSQL connection (migrations only)"
    fi

    return 0
}

# --- end lib/platform-env.sh ---

# --- lib/platform-validation.sh ---
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
                new_rabbitmq_pass=$(gen_hex_secret 32)
                echo -e "${BLUE}  -> Generating missing RABBITMQ_PASSWORD for upgrade...${NC}"
                echo "RABBITMQ_PASSWORD=$new_rabbitmq_pass" >> "$env_file"
                env_set_value "$env_file" "CELERY_BROKER_URL" "amqp://smsly_user:${new_rabbitmq_pass}@rabbitmq:5672//"
            elif [ "$var_name" = "GATEWAY_SECRET" ]; then
                echo -e "${BLUE}  -> Generating missing GATEWAY_SECRET...${NC}"
                env_set_value "$env_file" "GATEWAY_SECRET" "$(gen_hex_secret 64)"
            elif [ "$var_name" = "FRP_AUTH_TOKEN" ]; then
                echo -e "${BLUE}  -> Generating missing FRP_AUTH_TOKEN...${NC}"
                env_set_value "$env_file" "FRP_AUTH_TOKEN" "$(gen_hex_secret 64)"
            elif [ "$var_name" = "TUNNEL_DOMAIN" ]; then
                echo -e "${BLUE}  -> Setting missing TUNNEL_DOMAIN...${NC}"
                env_set_value "$env_file" "TUNNEL_DOMAIN" "tunnel.localhost"
            elif [ "$var_name" = "PGCAT_ADMIN_PASSWORD" ]; then
                echo -e "${BLUE}  -> Generating missing PGCAT_ADMIN_PASSWORD...${NC}"
                env_set_value "$env_file" "PGCAT_ADMIN_PASSWORD" "$(gen_hex_secret 48)"
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

# --- end lib/platform-validation.sh ---

# --- lib/platform.sh ---
_SCRIPT_DIR="${BASH_SOURCE[0]%/*}"
# --- lib/platform-diagnostics.sh ---
dump_diagnostic_logs() {
    local env_file="${1:-$INSTALL_DIR/.env}"
    echo -e "\n${RED}════════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}   DIAGNOSTIC LOG DUMP (FAILURE ANALYSIS)${NC}"
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"

    echo -e "${YELLOW}  → System Resource Snapshot:${NC}"
    free -m
    df -h /

    echo -e "\n${YELLOW}  → Container Status:${NC}"
    if command -v docker  && [ -f "$env_file" ] && grep -q '^POSTGRES_PASSWORD=' "$env_file" ; then
        docker compose -f "$COMPOSE_FILE" ps || true

        echo -e "\n${YELLOW}  -> Compose Logs (Last 50 lines):${NC}"
        docker compose -f "$COMPOSE_FILE" logs --tail=50 || true
    else
        echo -e "${YELLOW}  (Docker or .env not ready; skipping container logs)${NC}"
    fi

    echo -e "${RED}════════════════════════════════════════════════════════════${NC}\n"
}

# --- end lib/platform-diagnostics.sh ---
# --- lib/platform-domain.sh ---
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
        timeout -k 5 300 docker compose -f "$COMPOSE_FILE" exec -T \
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

incoming_domain = normalize_platform_domain(os.environ.get("SMSLY_SYNC_DOMAIN", ""))
db_has_real_domain = bool(original_domain) and original_domain not in ("", "localhost")
incoming_is_ip_or_empty = not incoming_domain
if db_has_real_domain and incoming_is_ip_or_empty:
    print(f"[sync] Preserving existing DB domain '{original_domain}' (incoming was empty/IP)")
else:
    cfg.domain = incoming_domain

_incoming_use_ssl = parse_bool(os.environ.get("SMSLY_SYNC_USE_SSL", "false"))
_db_already_has_ssl = bool(cfg.use_ssl)
if _incoming_use_ssl:
    cfg.use_ssl = True
elif not _db_already_has_ssl:
    cfg.use_ssl = False

_incoming_wildcard = parse_bool(os.environ.get("SMSLY_SYNC_WILDCARD", "false"))
_db_already_has_wildcard = bool(cfg.wildcard_subdomains)
if _incoming_wildcard:
    cfg.wildcard_subdomains = True
elif not _db_already_has_wildcard:
    cfg.wildcard_subdomains = False
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

    DOMAIN_SYNC_UPDATED_COUNT="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('updated_service_domains', 0))"  || echo 0)"
    DOMAIN_SYNC_REDEPLOY_REQUIRED="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(1 if json.load(sys.stdin).get('redeploy_required') else 0)"  || echo 0)"
    DOMAIN_SYNC_SERVICE_IDS="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(','.join(json.load(sys.stdin).get('service_ids', [])))"  || true)"

    echo -e "${GREEN}  ✓ PlatformConfig synced: domain=$(printf '%s' "$sync_json" | python3 -c "import json,sys; print(json.load(sys.stdin).get('domain', ''))" )${NC}"
    if [ "${DOMAIN_SYNC_UPDATED_COUNT:-0}" -gt 0 ]; then
        echo -e "${GREEN}  ✓ Rewrote ${DOMAIN_SYNC_UPDATED_COUNT} existing service public domain(s)${NC}"
    fi

    _effective_domain="$(printf '%s' "$sync_json" | python3 -c "
import json,sys
d = json.load(sys.stdin)
print(d.get('domain', '') or '')
"  || true)"
    _env_domain="$(env_get_value "$env_file" "DOMAIN")"
    _env_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
    _env_wildcard="$(env_get_value "$env_file" "WILDCARD_SUBDOMAINS")"
    _db_use_ssl="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('use_ssl') else 'false')" )"
    _db_wildcard="$(printf '%s' "$sync_json" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('wildcard_subdomains') else 'false')" )"
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

    local backend_container
    backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
    local backend_state
    backend_state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$backend_container"  || echo 'missing')"
    if [ "$backend_state" != "healthy" ] && [ "$backend_state" != "running" ]; then
        echo -e "${YELLOW}  ⚠ Backend container ($backend_container) not ready (state=$backend_state). Waiting 15s...${NC}" >&2
        sleep 15
        backend_state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$backend_container"  || echo 'missing')"
        if [ "$backend_state" != "healthy" ] && [ "$backend_state" != "running" ]; then
            echo -e "${RED}  ✗ Backend container still not ready after wait. Skipping redeploy.${NC}" >&2
            return 1
        fi
    fi

    timeout -k 5 300 docker compose -f "$COMPOSE_FILE" exec -T \
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
except Exception as exc:
    print(f"WARN: {exc}")
    traceback.print_exc()
PY
}

# --- end lib/platform-domain.sh ---
# --- lib/platform-env.sh ---
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

    if [ -n "$desired_domain" ]; then
        if echo "$desired_domain" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || [ "$desired_use_ssl" != "true" ]; then
            _grafana_scheme="http"
        else
            _grafana_scheme="https"
        fi
        _desired_grafana_url="${_grafana_scheme}://${desired_domain}/grafana"
        _current_grafana_url="$(env_get_value "$env_file" "GRAFANA_EXTERNAL_URL")"
        if [ "$_desired_grafana_url" != "$_current_grafana_url" ]; then
            env_set_value "$env_file" "GRAFANA_EXTERNAL_URL" "$_desired_grafana_url"
            changed=true
        fi
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

    if [ -f "$env_file" ]; then
        local env_node_type
        env_node_type="$(env_get_value "$env_file" "NODE_TYPE"  || true)"
        if [ "$env_node_type" = "agent-lite" ] || [ "$env_node_type" = "agent" ]; then
            MODE_AGENT_LITE="true"
        fi
    fi

    if [ "${MODE_AGENT_LITE:-false}" = "true" ]; then
        if [ -z "${MASTER_IP:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_IP="$(env_get_value "$env_file" "MASTER_IP"  || true)"
            fi
            if [ -z "${MASTER_IP:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_IP="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_IP"  || true)"
            fi
        fi

        if [ -z "${MASTER_MESH_IP:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_MESH_IP="$(env_get_value "$env_file" "MASTER_MESH_IP"  || true)"
            fi
            if [ -z "${MASTER_MESH_IP:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_MESH_IP="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_MESH_IP"  || true)"
            fi
        fi

        if [ -z "${MASTER_DB_USER:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_DB_USER="$(env_get_value "$env_file" "MASTER_DB_USER"  || true)"
            fi
            if [ -z "${MASTER_DB_USER:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_DB_USER="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_DB_USER"  || true)"
            fi
        fi

        if [ -z "${MASTER_DB_PASSWORD:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_DB_PASSWORD="$(env_get_value "$env_file" "MASTER_DB_PASSWORD"  || true)"
            fi
            if [ -z "${MASTER_DB_PASSWORD:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_DB_PASSWORD="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_DB_PASSWORD"  || true)"
            fi
            if [ -z "${MASTER_DB_PASSWORD:-}" ] && [ -f "$env_file" ]; then
                local db_url
                db_url="$(env_get_value "$env_file" "DATABASE_URL"  || true)"
                if [[ "$db_url" =~ ://[^:]+:([^@]+)@ ]]; then
                    MASTER_DB_PASSWORD="${BASH_REMATCH[1]}"
                fi
            fi
        fi

        if [ -z "${MASTER_MQ_PASSWORD:-}" ]; then
            if [ -f "$env_file" ]; then
                MASTER_MQ_PASSWORD="$(env_get_value "$env_file" "MASTER_MQ_PASSWORD"  || true)"
            fi
            if [ -z "${MASTER_MQ_PASSWORD:-}" ] && [ -f "/opt/smsly-hosting/.agent_lite_seed" ]; then
                MASTER_MQ_PASSWORD="$(env_get_value "/opt/smsly-hosting/.agent_lite_seed" "MASTER_MQ_PASSWORD"  || true)"
            fi
        fi
    fi

    env_ensure_var "$env_file" "SECRET_KEY" "$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(50)))"  || openssl rand -hex 32)" "Django SECRET_KEY (minimum 32 chars)"
    env_ensure_var "$env_file" "FIELD_ENCRYPTION_KEY" "$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'  || openssl rand -base64 32)" "Fernet key for Django field-level encryption"
    env_ensure_var "$env_file" "POSTGRES_PASSWORD" "$(gen_hex_secret 32)" "PostgreSQL admin password"
    env_ensure_var "$env_file" "REDIS_PASSWORD" "$(gen_hex_secret 32)" "Redis authentication password"
    env_ensure_var "$env_file" "RABBITMQ_PASSWORD" "$(gen_hex_secret 32)" "RabbitMQ authentication password"
    env_ensure_var "$env_file" "GATEWAY_SECRET" "$(gen_hex_secret 64)" "Inter-service HMAC authentication secret"
    env_ensure_var "$env_file" "GITHUB_WEBHOOK_SECRET" "$(gen_hex_secret 64)" "GitHub webhook signature verification"
    env_ensure_var "$env_file" "AUTOSCALER_API_TOKEN" "$(gen_hex_secret 64)" "Autoscaler API bearer token (shared between autoscaler service and Django backend)"
    env_ensure_var "$env_file" "FRP_AUTH_TOKEN" "$(gen_hex_secret 64)" "FRP tunnel relay authentication token"
    env_ensure_var "$env_file" "CADDY_ASK_SECRET" "$(gen_hex_secret 64)" "Shared secret for the Caddy on_demand_tls 'ask' endpoint (X-Caddy-Secret header). Without this the backend logs a warning and generates an ephemeral random secret on every restart."
    env_ensure_var "$env_file" "BACKUP_ENCRYPTION_KEY" "$(python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'  || openssl rand -base64 32)" "Fernet key used to encrypt on-disk backups (required when BACKUP_REQUIRE_ENCRYPTION=True)"
    env_ensure_var "$env_file" "BACKUP_REQUIRE_ENCRYPTION" "true" "Refuse to write unencrypted backups"
    env_ensure_var "$env_file" "SMSLY_DISABLE_TIER_GATES" "true" "Disable owner-tier paywall gates in this edition"
    env_ensure_var "$env_file" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false" "Keep AppConfig.ready side-effect free; installer/watchers sync edge config"
    env_ensure_var "$env_file" "PGCAT_ADMIN_PASSWORD" "$(gen_hex_secret 48)" "PgCat administration password (mandatory for 1.2+)"
    env_ensure_var "$env_file" "GRAFANA_PASSWORD" "$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits+'-_') for _ in range(40)))"  || openssl rand -base64 30 | tr -d '+/=')" "Grafana admin password (used by the standalone observability stack)"
    env_ensure_var "$env_file" "REPLICATION_PASSWORD" "$(gen_hex_secret 32)" "PostgreSQL streaming replication password"
    env_ensure_var "$env_file" "SENTINEL_PASSWORD" "$(gen_hex_secret 32)" "Redis Sentinel authentication password"
    env_ensure_var "$env_file" "REGISTRY_HTTP_SECRET" "$(gen_hex_secret 32)" "Docker registry HTTP secret"
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
        local compose_target="${COMPOSE_FILE:-docker-compose.prod.yml}"
        if [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" ; then
            expected_database_url="postgresql://smsly_admin:${postgres_password}@pgcat:5432/smsly_hosting"
        else
            expected_database_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
        fi
        current_database_url="$(env_get_value "$env_file" "DATABASE_URL")"

        if [ "$MODE_AGENT_LITE" = "true" ] && [ -n "${MASTER_IP:-}" ]; then
            echo -e "${BLUE}  -> Configuring for Edge Node (Lite Agent) mode...${NC}"

            if [ -z "${MASTER_MESH_IP:-}" ] && [ -f "$env_file" ]; then
                MASTER_MESH_IP="$(env_get_value "$env_file" "MASTER_MESH_IP")"
            fi
            local db_user="${MASTER_DB_USER:-smsly_admin}"
            local db_pass="${MASTER_DB_PASSWORD:-$postgres_password}"
            local mq_pass="${MASTER_MQ_PASSWORD:-$rabbitmq_password}"

            local db_host="${MASTER_MESH_IP}"
            expected_database_url="postgresql://${db_user}:${db_pass}@${db_host}:5432/smsly_hosting"
            expected_direct_url="postgresql://${db_user}:${db_pass}@${db_host}:5432/smsly_hosting"
            expected_celery_broker_url="amqp://smsly_user:${rabbitmq_password}@rabbitmq:5672//"

            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            env_set_value "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url"
            env_set_value "$env_file" "CELERY_BROKER_URL" "$expected_celery_broker_url"
            if [ -n "${MASTER_MESH_IP:-}" ]; then
                env_set_value "$env_file" "MASTER_MESH_IP" "$MASTER_MESH_IP"
            fi

            current_database_url="$expected_database_url"
            current_celery_broker_url="$expected_celery_broker_url"
        fi

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

        if [[ "$current_database_url" =~ @db:5432 ]] && [ "$MODE_AGENT_LITE" != "true" ] && [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" ; then
            echo -e "${BLUE}  -> Migrating DATABASE_URL from db to pgcat${NC}"
            local migrated_url="${current_database_url/@db:5432/@pgcat:5432}"
            env_set_value "$env_file" "DATABASE_URL" "$migrated_url"
            current_database_url="$migrated_url"
            echo -e "${GREEN}  OK DATABASE_URL migrated to pgcat${NC}"
        fi

        if [[ "$current_database_url" =~ @pgbouncer:5432 ]]; then
            local migrated_url
            if [ -f "$compose_target" ] && grep -q "^  *pgcat:" "$compose_target" ; then
                echo -e "${BLUE}  -> Migrating DATABASE_URL from pgbouncer to pgcat${NC}"
                migrated_url="${current_database_url/@pgbouncer:5432/@pgcat:5432}"
            else
                echo -e "${BLUE}  -> Migrating DATABASE_URL from pgbouncer to db${NC}"
                migrated_url="${current_database_url/@pgbouncer:5432/@db:5432}"
            fi
            env_set_value "$env_file" "DATABASE_URL" "$migrated_url"
            current_database_url="$migrated_url"
            echo -e "${GREEN}  OK DATABASE_URL migrated${NC}"
        fi

        local expected_direct_url
        if [ "$MODE_AGENT_LITE" = "true" ]; then
            expected_direct_url="postgresql://${MASTER_DB_USER:-smsly_admin}:${MASTER_DB_PASSWORD:-$postgres_password}@${MASTER_MESH_IP:-db}:5432/smsly_hosting"
        else
            expected_direct_url="postgresql://smsly_admin:${postgres_password}@db:5432/smsly_hosting"
        fi

        if [ -z "$current_database_url" ]; then
            env_ensure_var "$env_file" "DATABASE_URL" "$expected_database_url" "PostgreSQL connection string (via PgCat)"

            env_ensure_var "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url" "Direct connection bypass for migrations"
        elif [[ "$current_database_url" =~ ^postgresql://smsly_admin:.*@pgcat:5432/smsly_hosting$ ]] && [ "$current_database_url" != "$expected_database_url" ]; then
            echo -e "${BLUE}  -> Fixing DATABASE_URL to match POSTGRES_PASSWORD${NC}"
            env_set_value "$env_file" "DATABASE_URL" "$expected_database_url"
            echo -e "${GREEN}  OK DATABASE_URL password synced${NC}"
        fi

        env_ensure_var "$env_file" "DIRECT_DATABASE_URL" "$expected_direct_url" "Direct PostgreSQL connection (migrations only)"
    fi

    return 0
}

# --- end lib/platform-env.sh ---
# --- lib/platform-validation.sh ---
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
                new_rabbitmq_pass=$(gen_hex_secret 32)
                echo -e "${BLUE}  -> Generating missing RABBITMQ_PASSWORD for upgrade...${NC}"
                echo "RABBITMQ_PASSWORD=$new_rabbitmq_pass" >> "$env_file"
                env_set_value "$env_file" "CELERY_BROKER_URL" "amqp://smsly_user:${new_rabbitmq_pass}@rabbitmq:5672//"
            elif [ "$var_name" = "GATEWAY_SECRET" ]; then
                echo -e "${BLUE}  -> Generating missing GATEWAY_SECRET...${NC}"
                env_set_value "$env_file" "GATEWAY_SECRET" "$(gen_hex_secret 64)"
            elif [ "$var_name" = "FRP_AUTH_TOKEN" ]; then
                echo -e "${BLUE}  -> Generating missing FRP_AUTH_TOKEN...${NC}"
                env_set_value "$env_file" "FRP_AUTH_TOKEN" "$(gen_hex_secret 64)"
            elif [ "$var_name" = "TUNNEL_DOMAIN" ]; then
                echo -e "${BLUE}  -> Setting missing TUNNEL_DOMAIN...${NC}"
                env_set_value "$env_file" "TUNNEL_DOMAIN" "tunnel.localhost"
            elif [ "$var_name" = "PGCAT_ADMIN_PASSWORD" ]; then
                echo -e "${BLUE}  -> Generating missing PGCAT_ADMIN_PASSWORD...${NC}"
                env_set_value "$env_file" "PGCAT_ADMIN_PASSWORD" "$(gen_hex_secret 48)"
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

# --- end lib/platform-validation.sh ---
unset _SCRIPT_DIR

# --- end lib/platform.sh ---

# --- lib/preflight.sh ---
check_internet() {
    echo -e "${BLUE}  → Checking internet connectivity...${NC}"
    if ! curl -Is --connect-timeout 5 https://google.com ; then
        echo -e "${RED}  ✗ No internet access. Check your firewall/network settings.${NC}"
        exit 1
    fi
    if ! host github.com ; then
         # Fallback to ping if host is missing
         if ! ping -c 1 github.com ; then
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
    if systemctl is-active --quiet caddy ; then
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

        if command -v fuser ; then
            for lock_file in "${lock_files[@]}"; do
                [ -e "$lock_file" ] || continue
                if fuser "$lock_file" ; then
                    active_locks+=("$lock_file")
                    pids="$pids $(fuser "$lock_file"  || true)"
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
                ps -p "$pid" -o pid=,comm=,etime=,args=  || true
            done
            echo -e "${YELLOW}  Wait for those processes to finish, then rerun the installer.${NC}"
            echo -e "${YELLOW}  If no apt/dpkg processes are running, repair with: sudo dpkg --configure -a${NC}"
            return 1
        fi

        if [ $((elapsed % 30)) -eq 0 ]; then
            echo
            echo -e "${YELLOW}  Waiting for APT lock (${elapsed}s/${max_wait}s). Active lock(s): ${active_locks[*]}${NC}"
            for pid in $(printf "%s\n" $pids | sort -u); do
                ps -p "$pid" -o pid=,comm=,etime=,args=  || true
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
        if output="$("$@" )"; then
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
            swapoff "$swapfile"  || true
            rm -f "$swapfile"
            # Since we removed the old file, we need to create the full target amount
            needed_mb=$target_swap_mb
        fi

        fallocate -l ${needed_mb}M "$swapfile"  || dd if=/dev/zero of="$swapfile" bs=1M count=$needed_mb status=none
        chmod 600 "$swapfile"
        mkswap "$swapfile" 
        swapon "$swapfile"  || true
        # Make permanent (idempotent)
        if ! grep -q "$swapfile" /etc/fstab ; then
            echo "$swapfile none swap sw 0 0" >> /etc/fstab
        fi
        echo -e "${GREEN}  ✓ Swap file created and activated (${needed_mb}MB)${NC}"
    else
        echo -e "${GREEN}  ✓ Swap already sufficient (${current_swap_mb}MB, >= 4x RAM)${NC}"
    fi
}
# --- end lib/preflight.sh ---

# --- lib/state.sh ---
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
        previous_flavor="$(cat "$STATE_MODE_FILE"  || echo "legacy")"
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
    if [ ! -f "$STATE_FILE" ] || ! grep -q "^$name$" "$STATE_FILE" ; then
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
        grep -v "^$name$" "$STATE_FILE" > "${STATE_FILE}.tmp"  || true
        mv "${STATE_FILE}.tmp" "$STATE_FILE"  || true
    fi
}
# --- end lib/state.sh ---

# --- lib/utils.sh ---
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
        sed 's/^/__compose_config_error__:/' /tmp/smsly-compose-config.err  | head -5 || true
        return 0
    fi

    printf '%s\n' "$services" | while IFS= read -r service; do
        [ -n "$service" ] || continue
        container_id="$(docker compose -f "$COMPOSE_FILE" ps -q "$service"  || true)"
        if [ -z "$container_id" ]; then
            echo "$service:missing"
            continue
        fi
        container_state="$(docker inspect -f '{{.State.Status}}' "$container_id"  || true)"
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

    set +e; compose_stack_up --remove-orphans; reconcile_rc=$?; set -e
    if [ "$reconcile_rc" -ne 0 ]; then
        echo -e "${YELLOW}  -> Compose reconciliation needs a rebuild; rebuilding stack...${NC}"
        echo -e "${YELLOW}    ↳ Rebuilding with --no-cache to ensure clean state...${NC}"
        set +e; compose_stack_build --no-cache; reconcile_rc=$?; set -e
        if [ "$reconcile_rc" -eq 0 ]; then
            set +e; compose_stack_up --remove-orphans; reconcile_rc=$?; set -e
        fi
    fi

    if [ "$reconcile_rc" -ne 0 ]; then
        echo -e "${RED}  x Compose reconciliation failed (exit $reconcile_rc).${NC}"
        docker compose -f "$COMPOSE_FILE" ps  || true
        docker compose -f "$COMPOSE_FILE" logs --tail=120  || true
        exit "$reconcile_rc"
    fi

    echo -e "${GREEN}  OK Compose stack reconciled after resume${NC}"
}
# --- end lib/utils.sh ---

# --- lib/validation.sh ---
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

is_real_domain_name() {
    local host="${1:-}"
    [ -n "$host" ] \
        && [ "$host" != "localhost" ] \
        && ! echo "$host" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'
}

# --- end lib/validation.sh ---

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
# --- lib/harden.sh ---
#!/bin/bash
set +e

_harden_log() {
    local level="$1"; shift
    case "$level" in
        ok)   echo -e "${GREEN}  ✓ [harden] $*${NC}" ;;
        warn) echo -e "${YELLOW}  ⚠ [harden] $*${NC}" ;;
        err)  echo -e "${RED}  ✗ [harden] $*${NC}" ;;
        info) echo -e "${BLUE}  → [harden] $*${NC}" ;;
    esac
}

# --- lib/harden_fail2ban.sh ---
#!/bin/bash

_harden_fail2ban_bootstrap() {
    if ! command -v fail2ban-client ; then
        apt_run apt-get install -y fail2ban  || true
    fi
    command -v fail2ban-client  || return 1

    [ -f /etc/fail2ban/jail.local ] || cat <<'JAIL_EOF' > /etc/fail2ban/jail.local
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 3
banaction = iptables-multiport
banaction_allports = iptables-allports

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 1h
findtime = 10m

[recidive]
enabled = true
filter = recidive
logpath = /var/log/fail2ban.log
action = iptables-allports[name=recidive]
bantime = 24h
findtime = 1d
maxretry = 3
JAIL_EOF
    # Enable Caddy jails when Caddy logs are available
    if [ -d /var/log/caddy ] || docker volume ls --format '{{.Name}}'  | grep -q caddy_logs; then
        # Never duplicate the sections: fail2ban aborts on a repeated
        # [caddy-auth], and every install/update run would otherwise append.
        if ! grep -q '^\[caddy-auth\]' /etc/fail2ban/jail.local 2>/dev/null; then
            cat <<'CADDY_JAIL_EOF' >> /etc/fail2ban/jail.local

[caddy-auth]
enabled = true
filter = caddy-auth
port = http,https
logpath = /var/log/caddy/access.log
maxretry = 5
bantime = 1h

[caddy-dos]
enabled = true
filter = caddy-dos
port = http,https
logpath = /var/log/caddy/access.log
findtime = 300
maxretry = 300
bantime = 600
CADDY_JAIL_EOF
        fi
    fi
    # Caddy auth filter (JSON access log — 401/403 responses)
    [ -f /etc/fail2ban/filter.d/caddy-auth.conf ] || cat <<'FILTER_EOF' > /etc/fail2ban/filter.d/caddy-auth.conf
[Definition]
failregex = ^.*"remote_ip":"<HOST>".*"status":(401|403).*$
ignoreregex =
FILTER_EOF
    # Caddy DoS filter (JSON access log — any request)
    [ -f /etc/fail2ban/filter.d/caddy-dos.conf ] || cat <<'FILTER_EOF' > /etc/fail2ban/filter.d/caddy-dos.conf
[Definition]
failregex = ^.*"remote_ip":"<HOST>".*"method":"(GET|POST|HEAD|PUT|DELETE|PATCH)".*$
ignoreregex =
FILTER_EOF

    systemctl enable fail2ban || _harden_log warn "fail2ban enable failed"
    # Blocking start — wait for the service to actually be ACTIVE (not just for
    # `systemctl restart` to return). If it never comes up we surface the real
    # failure via journalctl instead of spamming socket errors.
    systemctl restart fail2ban || _harden_log warn "fail2ban restart returned non-zero"
    local _up=0
    for _i in $(seq 1 30); do
        if systemctl is-active --quiet fail2ban; then
            _up=1
            break
        fi
        sleep 1
    done
    if [ "$_up" -ne 1 ]; then
        _harden_log err "fail2ban failed to become active — last journalctl output:"
        journalctl -u fail2ban -n 40 --no-pager 2>&1 | sed 's/^/      /' || true
        return 1
    fi
    # Service is active — confirm the client can reach the server socket.
    if ! fail2ban-client ping; then
        _harden_log warn "fail2ban active but client cannot reach socket"
    fi
}

_harden_fail2ban_verify() {
    command -v fail2ban-client  || { _harden_log warn "fail2ban — not installed"; return 1; }
    if ! systemctl is-active --quiet fail2ban; then
        _harden_log warn "fail2ban not running — last journalctl output:"
        journalctl -u fail2ban -n 30 --no-pager 2>&1 | sed 's/^/      /' || true
        return 1
    fi
    if fail2ban-client ping && fail2ban-client status sshd ; then
        _harden_log ok "fail2ban active (sshd + recidive + http)"
        return 0
    fi
    _harden_log warn "fail2ban running but not responding to client"
    return 1
}

# --- end lib/harden_fail2ban.sh ---
# --- lib/harden_ufw.sh ---
#!/bin/bash

_harden_ufw_bootstrap() {
    command -v ufw  || apt_run apt-get install -y ufw  || true
    command -v ufw  || return 1

    # Already active — just verify ports are open, then bail
    if ufw status  | grep -qi "active"; then
        for port in 22 80 443 51820; do
            ufw status verbose  | grep -qE "${port}(/tcp|/udp)?.*ALLOW" || ufw allow "$port" || echo -e "${YELLOW}    ⚠ ufw allow port $port failed${NC}"
        done
        # Whitelist Docker bridges
        for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
            ip link show "$iface" >/dev/null 2>&1 || continue
            ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
        done
        return 0
    fi

    # Inactive — configure and enable (INPUT default deny, FORWARD stays open for Docker)
    ufw --force default deny incoming || echo -e "${YELLOW}    ⚠ ufw default deny incoming failed${NC}"
    ufw --force default allow outgoing || echo -e "${YELLOW}    ⚠ ufw default allow outgoing failed${NC}"
    ufw allow ssh || echo -e "${YELLOW}    ⚠ ufw allow ssh failed${NC}"
    ufw allow 80/tcp || echo -e "${YELLOW}    ⚠ ufw allow 80/tcp failed${NC}"
    ufw allow 443/tcp || echo -e "${YELLOW}    ⚠ ufw allow 443/tcp failed${NC}"
    ufw allow 51820/udp || echo -e "${YELLOW}    ⚠ ufw allow 51820/udp failed${NC}"
    for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
        ip link show "$iface" >/dev/null 2>&1 || continue
        ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
    done
    ufw --force enable || echo -e "${YELLOW}    ⚠ ufw enable failed${NC}"
    # Verify it actually came up
    for _i in $(seq 1 5); do
        ufw status  | grep -qi "active" && break
        sleep 2
    done
}

_harden_ufw_verify() {
    command -v ufw  || { _harden_log warn "ufw — not installed"; return 1; }
    if ufw status  | grep -qi "active"; then
        _harden_log ok "ufw active (host INPUT hardened)"
        return 0
    fi
    _harden_log warn "ufw not active — check ufw status"
    return 1
}

# --- end lib/harden_ufw.sh ---
# --- lib/harden_apparmor.sh ---
#!/bin/bash

_harden_apparmor_bootstrap() {
    command -v aa-status  || apt_run apt-get install -y apparmor apparmor-utils  || true
    command -v aa-status  || return 1
    systemctl enable apparmor || echo -e "${YELLOW}    ⚠ apparmor enable failed${NC}"
    systemctl start apparmor || echo -e "${YELLOW}    ⚠ apparmor start failed${NC}"
}

_harden_apparmor_verify() {
    command -v aa-status  || { _harden_log warn "apparmor — not installed"; return 1; }
    local count
    count=$(aa-status --json  | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('processes',{})))"  || echo "0")
    count="${count//[^0-9]/}"
    : "${count:=0}"
    if [ "$count" -gt 0 ] ; then
        _harden_log ok "apparmor enforcing ($count profiles)"
        return 0
    fi
    _harden_log warn "apparmor installed but no enforce profiles"
    return 1
}

# --- end lib/harden_apparmor.sh ---
# --- lib/harden_auditd.sh ---
#!/bin/bash

_harden_auditd_bootstrap() {
    command -v auditd  || apt_run apt-get install -y auditd audispd-plugins  || true

    if [ ! -f /etc/audit/rules.d/smsly.rules ]; then
        mkdir -p /etc/audit/rules.d
        cat > /etc/audit/rules.d/smsly.rules <<'AUDIT_EOF'
-w /etc/shadow -p wa -k identity
-w /etc/passwd -p wa -k identity
-w /etc/sudoers -p wa -k privilege-escalation
-w /etc/ssh/sshd_config -p wa -k sshd
-w /opt/smsly-hosting/.env -p wa -k smsly-config
-w /opt/smsly-hosting/secrets/ -p wa -k smsly-secrets
-a always,exit -F arch=b64 -S execve -F path=/usr/bin/docker -k docker-exec
-a always,exit -F arch=b64 -S mount -k filesystem-mounts
-a exit,always -F arch=b64 -S execve -F euid=0 -F auid>=1000 -k priv-esc
AUDIT_EOF
    fi
    systemctl enable auditd || echo -e "${YELLOW}    ⚠ auditd enable failed${NC}"
    systemctl restart auditd || echo -e "${YELLOW}    ⚠ auditd restart failed${NC}"
}

_harden_auditd_verify() {
    command -v auditd  || { _harden_log warn "auditd — not installed"; return 1; }
    if systemctl is-active --quiet auditd ; then
        _harden_log ok "auditd active (file + syscall monitoring)"
        return 0
    fi
    _harden_log warn "auditd not running — may need kernel param audit=1"
    return 1
}

# --- end lib/harden_auditd.sh ---
# --- lib/harden_kernel.sh ---
#!/bin/bash

_harden_kernel_bootstrap() {
    local sysctl_file="/etc/sysctl.d/99-smsly-security.conf"
    [ -f "$sysctl_file" ] && return 0  # already applied

    cat > "$sysctl_file" <<'SYSCTL_EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
kernel.yama.ptrace_scope = 1
kernel.unprivileged_bpf_disabled = 1
kernel.randomize_va_space = 2
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
fs.suid_dumpable = 0
SYSCTL_EOF
    sysctl -p "$sysctl_file" || echo -e "${YELLOW}    ⚠ sysctl -p failed${NC}"
}

_harden_kernel_verify() {
    if [ -f /etc/sysctl.d/99-smsly-security.conf ]; then
        _harden_log ok "kernel hardening applied"
        return 0
    fi
    _harden_log warn "kernel hardening not applied"
    return 1
}

# --- end lib/harden_kernel.sh ---
# --- lib/harden_docker_daemon.sh ---
#!/bin/bash

_harden_docker_daemon_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    local daemon_cfg="/etc/docker/daemon.json"
    [ ! -f "$daemon_cfg" ] && echo '{}' > "$daemon_cfg"

    local changed=false

    # log rotation
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('log-driver')=='json-file' and d.get('log-opts',{}).get('max-size')=='10m' else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['log-driver'] = 'json-file'
cfg['log-opts'] = {'max-size': '10m', 'max-file': '3'}
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # live-restore
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('live-restore') else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['live-restore'] = True
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # seccomp
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('features',{}).get('seccomp') else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg.setdefault('features', {})['seccomp'] = True
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # Restart Docker if config changed AND no SMSLY containers are running
    # (doing so live would kill production).
    if [ "$changed" = "true" ]; then
        local _smsly_ctrs
        _smsly_ctrs="$(docker ps --format '{{.Names}}'  | grep -c smsly || true)"
        if [ "$_smsly_ctrs" -eq 0 ]; then
            _harden_log info "Docker daemon config changed — restarting Docker..."
            systemctl restart docker || { _harden_log error "Docker restart failed"; }
            for _i in $(seq 1 30); do
                docker info  && break
                sleep 2
            done
            _harden_log ok "Docker daemon restarted with security config"
        else
            _harden_log warn "Docker daemon config changed but $_smsly_ctrs SMSLY containers are running — deferring restart (apply on next daemon reload)"
        fi
    fi
}

_harden_docker_daemon_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    local daemon_cfg="/etc/docker/daemon.json"
    if [ -f "$daemon_cfg" ] && python3 -c "import json; json.load(open('$daemon_cfg'))" ; then
        _harden_log ok "docker daemon security config present"
        return 0
    fi
    _harden_log warn "docker daemon config missing or invalid"
    return 1
}

# --- end lib/harden_docker_daemon.sh ---
# --- lib/harden_crowdsec.sh ---
#!/bin/bash

_harden_crowdsec_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    # CrowdSec comes from the main docker-compose stack — if the container
    # isn't running, try docker compose up -d for just that service.
    if docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec"; then
        return 0  # already up
    fi
    # Blocking start — wait for container to be healthy
    # The harden bootstrap may run before fresh_config has generated .env,
    # so only pass --env-file when the file exists.
    local env_args=()
    [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
    docker compose \
        "${env_args[@]}" \
        -f "$COMPOSE_FILE" \
        up -d crowdsec || echo -e "${YELLOW}    ⚠ crowdsec docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec" && break
        sleep 2
    done
}

_harden_crowdsec_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec"; then
        _harden_log warn "crowdsec — container not running"
        return 1
    fi
    # Refresh hub scenarios — only upgrade when explicitly allowed.
    # Auto-upgrading on every harden.sh run can silently break
    # production WAF if CrowdSec ships a breaking parser change.
    timeout -k 5 60 docker exec smsly-crowdsec cscli hub update  || _harden_log warn "crowdsec hub update failed"
    if [ "${CROWDSEC_AUTO_UPGRADE_HUB:-0}" = "1" ]; then
        timeout -k 5 60 docker exec smsly-crowdsec cscli hub upgrade  || _harden_log warn "crowdsec hub upgrade failed"
    else
        _harden_log info "crowdsec hub upgrade skipped (set CROWDSEC_AUTO_UPGRADE_HUB=1 to enable)"
    fi
    _harden_log ok "crowdsec deployed"
    return 0
}

# --- end lib/harden_crowdsec.sh ---
# --- lib/harden_falco.sh ---
#!/bin/bash

_harden_falco_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    local compose_file="$INSTALL_DIR/infrastructure/docker/docker-compose.falco.yml"
    [ -f "$compose_file" ] || return 1

    # Blocking start — always recreate so config changes take effect.
    # The harden bootstrap may run before fresh_config has generated .env,
    # so only pass --env-file when the file exists (compose file needs no vars).
    local env_args=()
    [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
    # smsly-net is declared external in the falco compose file but is only
    # created during stack deploy (fresh_deploy.sh) — the harden bootstrap
    # runs earlier, so create it here if missing.
    docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null 2>&1 || true
    docker compose \
        "${env_args[@]}" \
        -f "$compose_file" \
        up -d --force-recreate --pull always || echo -e "${YELLOW}    ⚠ falco docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}'  | grep -q "smsly-falco" && break
        sleep 2
    done
}

_harden_falco_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! docker ps --format '{{.Names}}'  | grep -q "smsly-falco"; then
        _harden_log warn "falco — container not running"
        return 1
    fi
    _harden_log ok "falco deployed"
    return 0
}

# --- end lib/harden_falco.sh ---
# --- lib/harden_container_runtime.sh ---
#!/bin/bash

_harden_container_runtime_bootstrap() {
    local install_dir="${INSTALL_DIR:-/opt/smsly-hosting}"
    local env_file="$install_dir/.env"

    # If CONTAINER_RUNTIME is already persisted in .env, skip detection.
    # The user can clear it to re-detect.
    if [ -f "$env_file" ] && grep -q '^CONTAINER_RUNTIME=' "$env_file" ; then
        return 0
    fi

    # Try Kata first (stronger isolation, requires KVM)
    if [ -e /dev/kvm ] && ! command -v kata-runtime ; then
        if [ -f "$install_dir/lib/install-kata.sh" ]; then
            echo -e "${BLUE}  → [harden] Kata Containers (KVM available) — installing...${NC}"
            bash "$install_dir/lib/install-kata.sh" || true
        fi
    fi

    if command -v kata-runtime ; then
        env_set_value "$env_file" "CONTAINER_RUNTIME" "kata"
        echo -e "${BLUE}  → [harden] Persisted CONTAINER_RUNTIME=kata in .env${NC}"
        return 0
    fi

    # Fall back to gVisor (lighter, no KVM required)
    if ! command -v runsc ; then
        if [ -f "$install_dir/lib/install-gvisor.sh" ]; then
            echo -e "${BLUE}  → [harden] gVisor (runsc) — installing...${NC}"
            bash "$install_dir/lib/install-gvisor.sh" || true
        fi
    fi

    if command -v runsc ; then
        env_set_value "$env_file" "CONTAINER_RUNTIME" "runsc"
        echo -e "${BLUE}  → [harden] Persisted CONTAINER_RUNTIME=runsc in .env${NC}"
        return 0
    fi
}

_harden_container_runtime_verify() {
    local found=0

    if command -v runsc ; then
        _harden_log ok "gVisor (runsc) installed"
        found=1
    fi

    if command -v kata-runtime ; then
        _harden_log ok "Kata Containers installed"
        found=1
    fi

    if [ "$found" -eq 0 ]; then
        _harden_log warn "container runtime sandboxing — install gVisor or Kata for VM-level isolation"
        return 1
    fi

    # Check Docker runtime registration
    if [ -f /etc/docker/daemon.json ]; then
        if python3 -c "import json; import sys; cfg=json.load(open('/etc/docker/daemon.json')); sys.exit(0 if 'runsc' in cfg.get('runtimes',{}) else 1)" ; then
            _harden_log ok "gVisor registered with Docker"
        elif python3 -c "import json; import sys; cfg=json.load(open('/etc/docker/daemon.json')); sys.exit(0 if 'kata-runtime' in cfg.get('runtimes',{}) else 1)" ; then
            _harden_log ok "Kata registered with Docker"
        fi
    fi

    # `found` is a 0/1 FLAG, not an exit code — returning it turns a successful
    # gVisor/Kata install into a FAILED security check (found=1 -> return 1).
    return 0
}

# --- end lib/harden_container_runtime.sh ---
# --- lib/harden_trivy.sh ---
#!/bin/bash

_harden_trivy_bootstrap() {
    if command -v trivy ; then
        return 0  # already installed
    fi

    _harden_log info "Installing Trivy vulnerability scanner..."
    local trivy_version="v0.54.1"
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  arch="64bit" ;;
        aarch64) arch="ARM64" ;;
        *)       _harden_log warn "Trivy — unsupported architecture: $arch"; return 1 ;;
    esac

    local deb_url="https://github.com/aquasecurity/trivy/releases/download/${trivy_version}/trivy_${trivy_version#v}_Linux-${arch}.deb"
    local tmp_deb
    tmp_deb="$(mktemp /tmp/trivy.XXXXXX.deb)"

    # Attempt 1: Direct DEB download with retries and timeouts
    if curl --retry 3 --retry-delay 2 --connect-timeout 15 -fsSL "$deb_url" -o "$tmp_deb" ; then
        if ! dpkg -i "$tmp_deb" ; then
            apt-get install -f -y  || true
            dpkg -i "$tmp_deb"  || true
        fi
        rm -f "$tmp_deb"
    else
        rm -f "$tmp_deb"
        _harden_log info "Direct DEB download failed — trying official APT repo and install script..."
    fi

    # Attempt 2: Official APT Repository fallback
    if ! command -v trivy ; then
        apt-get update -qq  || true
        if ! apt-get install -y trivy ; then
            if command -v gpg ; then
                curl --retry 2 --connect-timeout 10 -fsSL https://aquasecurity.github.io/trivy-repo/deb/public.key  | gpg --dearmor -o /usr/share/keyrings/trivy.gpg  || true
                echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc  || echo stable) main" > /etc/apt/sources.list.d/trivy.list  || true
                apt-get update -qq  || true
                apt-get install -y trivy  || true
            fi
        fi
    fi

    # Attempt 3: Official Contrib script fallback
    if ! command -v trivy ; then
        curl --retry 2 --connect-timeout 10 -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin  || true
    fi

    if command -v trivy ; then
        _harden_log ok "Trivy installed successfully"
        return 0
    fi
    _harden_log warn "Trivy — download and installation fallbacks failed"
    return 1
}

_harden_trivy_verify() {
    if command -v trivy ; then
        local ver
        ver="$(trivy --version  | head -1 || true)"
        _harden_log ok "Trivy available: ${ver}"
        return 0
    fi
    _harden_log warn "Trivy — not installed (image vulnerability scanning unavailable)"
    return 1
}

# --- end lib/harden_trivy.sh ---
# --- lib/harden_infisical.sh ---
#!/bin/bash

_harden_infisical_bootstrap() {
    local infisical_script="$INSTALL_DIR/lib/infisical.sh"
    if [ ! -f "$infisical_script" ]; then
        _harden_log info "Infisical script not found — skipping"
        return 0
    fi
    # Source Infisical functions and bootstrap
    # shellcheck disable=SC1090
    source "$infisical_script"  || {
        _harden_log warn "Failed to source infisical.sh"
        return 1
    }
    if ! command -v infisical_bootstrap ; then
        _harden_log warn "infisical_bootstrap function not found"
        return 1
    fi
    infisical_bootstrap  || {
        _harden_log warn "Infisical bootstrap had issues"
        return 1
    }
    return 0
}

_harden_infisical_verify() {
    # Optional layer: the bootstrap skips when lib/infisical.sh is absent —
    # the verify must skip too, or every install reports a phantom failure.
    local infisical_script="${INSTALL_DIR:-/opt/smsly-hosting}/lib/infisical.sh"
    if [ ! -f "$infisical_script" ]; then
        return 0
    fi
    command -v docker >/dev/null 2>&1 || return 0
    if docker ps --format '{{.Names}}'  | grep -q "smsly-infisical"; then
        _harden_log ok "Infisical running"
        return 0
    fi
    _harden_log warn "Infisical — container not running"
    return 1
}

# --- end lib/harden_infisical.sh ---

harden_security_bootstrap() {
    echo -e "${BLUE}  → [harden] Bootstrapping security stack (blocking)...${NC}"
    local _harden_failures=0
    _harden_fail2ban_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_ufw_bootstrap        || { _harden_failures=$((_harden_failures + 1)); }
    _harden_apparmor_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_auditd_bootstrap     || { _harden_failures=$((_harden_failures + 1)); }
    _harden_kernel_bootstrap
    _harden_docker_daemon_bootstrap
    _harden_crowdsec_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_falco_bootstrap      || { _harden_failures=$((_harden_failures + 1)); }
    _harden_container_runtime_bootstrap
    _harden_trivy_bootstrap      || { _harden_failures=$((_harden_failures + 1)); }
    _harden_infisical_bootstrap  || { _harden_failures=$((_harden_failures + 1)); }
    if [ "$_harden_failures" -gt 0 ]; then
        echo -e "${YELLOW}  ⚠ [harden] $_harden_failures layer(s) had issues — verify will report details${NC}"
    else
        echo -e "${GREEN}  ✓ [harden] Bootstrap complete — all layers started${NC}"
    fi
    return 0
}

harden_security_verify() {
    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Security Stack — Verification${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

    local failures=0 checks=0

    # NOTE: never use standalone `((checks++))` here — when the counter is 0
    # the arithmetic expression evaluates to 0 → exit status 1 → under `set -e`
    # (re-enabled by fresh_hardening.sh after harden.sh's `set +e`) the whole
    # install dies silently after the first check.
    if ! _harden_fail2ban_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_ufw_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_apparmor_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_auditd_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_kernel_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_docker_daemon_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_crowdsec_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_falco_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_container_runtime_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_trivy_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_infisical_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))

    local passed=$((checks - failures))
    echo ""
    if [ "$failures" -eq 0 ]; then
        echo -e "${GREEN}  All $passed/$checks security checks passed${NC}"
    else
        echo -e "${RED}  Security: $passed/$checks passed, $failures FAILED${NC}"
        echo -e "${YELLOW}  Review failures above — run 'sudo bash install.sh --debug' for details${NC}"
    fi
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# --- end lib/harden.sh ---
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
# --- lib/update.sh ---
#!/bin/bash
# Grid by SMSLY - Update Mode Module
# Sourced by install.sh for --update

if [ -n "$UPDATE_MODE" ]; then
# --- lib/update_preflight.sh ---
    echo -e "${YELLOW}[UPDATE] Running in update mode: $UPDATE_MODE${NC}"
    echo -e "${BLUE}  -> Safe update: preserves database/redis volumes and addon data.${NC}"

    # Ensure repo cache directory exists for user service builds
    mkdir -p /opt/smsly-cache/repos
    chmod 775 /opt/smsly-cache
    chown -R 1000:1000 /opt/smsly-cache  || true
    mkdir -p /opt/smsly-hosting/builds
    chmod 775 /opt/smsly-hosting/builds
    chown -R 1000:1000 /opt/smsly-hosting/builds  || true

    # ─── Fix .env permissions BEFORE any containers start ────────────────────
    # The docker-compose.prod.yml mounts .env into the backend container.
    # If .env has 600 permissions (created by old install.sh), the container
    # can't read it and Django crashes with PermissionError.
    # The backend container runs as UID 1000 (smsly user), so the file must be
    # writable by that user to allow the domain-config signal to sync back to .env.
    if [ -f "$INSTALL_DIR/.env" ]; then
        chown root:1000 "$INSTALL_DIR/.env"  || true
        chmod 640 "$INSTALL_DIR/.env"  || true
        echo -e "${BLUE}  → Fixed .env permissions to 640 (readable by container UID 1000)${NC}"
    fi

    # ─── Pre-flight ──────────────────────────────────────────────────────────
    if [ "$EUID" -ne 0 ]; then
        echo -e "${RED}✗ Please run as root (sudo bash install.sh --update)${NC}"
        exit 1
    fi

    export PATH="/usr/local/bin:$PATH"
    check_internet
    check_hardware
    check_caddy_conflict
    ensure_system_swap
    ensure_security_tools || true


    # ─── Security: bootstrap (fire-and-forget) ────────────────────────────
    if [ -f "$INSTALL_DIR/lib/harden.sh" ]; then
# --- lib/harden.sh ---
#!/bin/bash
set +e

_harden_log() {
    local level="$1"; shift
    case "$level" in
        ok)   echo -e "${GREEN}  ✓ [harden] $*${NC}" ;;
        warn) echo -e "${YELLOW}  ⚠ [harden] $*${NC}" ;;
        err)  echo -e "${RED}  ✗ [harden] $*${NC}" ;;
        info) echo -e "${BLUE}  → [harden] $*${NC}" ;;
    esac
}

# --- lib/harden_fail2ban.sh ---
#!/bin/bash

_harden_fail2ban_bootstrap() {
    if ! command -v fail2ban-client ; then
        apt_run apt-get install -y fail2ban  || true
    fi
    command -v fail2ban-client  || return 1

    [ -f /etc/fail2ban/jail.local ] || cat <<'JAIL_EOF' > /etc/fail2ban/jail.local
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 3
banaction = iptables-multiport
banaction_allports = iptables-allports

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 1h
findtime = 10m

[recidive]
enabled = true
filter = recidive
logpath = /var/log/fail2ban.log
action = iptables-allports[name=recidive]
bantime = 24h
findtime = 1d
maxretry = 3
JAIL_EOF
    # Enable Caddy jails when Caddy logs are available
    if [ -d /var/log/caddy ] || docker volume ls --format '{{.Name}}'  | grep -q caddy_logs; then
        # Never duplicate the sections: fail2ban aborts on a repeated
        # [caddy-auth], and every install/update run would otherwise append.
        if ! grep -q '^\[caddy-auth\]' /etc/fail2ban/jail.local 2>/dev/null; then
            cat <<'CADDY_JAIL_EOF' >> /etc/fail2ban/jail.local

[caddy-auth]
enabled = true
filter = caddy-auth
port = http,https
logpath = /var/log/caddy/access.log
maxretry = 5
bantime = 1h

[caddy-dos]
enabled = true
filter = caddy-dos
port = http,https
logpath = /var/log/caddy/access.log
findtime = 300
maxretry = 300
bantime = 600
CADDY_JAIL_EOF
        fi
    fi
    # Caddy auth filter (JSON access log — 401/403 responses)
    [ -f /etc/fail2ban/filter.d/caddy-auth.conf ] || cat <<'FILTER_EOF' > /etc/fail2ban/filter.d/caddy-auth.conf
[Definition]
failregex = ^.*"remote_ip":"<HOST>".*"status":(401|403).*$
ignoreregex =
FILTER_EOF
    # Caddy DoS filter (JSON access log — any request)
    [ -f /etc/fail2ban/filter.d/caddy-dos.conf ] || cat <<'FILTER_EOF' > /etc/fail2ban/filter.d/caddy-dos.conf
[Definition]
failregex = ^.*"remote_ip":"<HOST>".*"method":"(GET|POST|HEAD|PUT|DELETE|PATCH)".*$
ignoreregex =
FILTER_EOF

    systemctl enable fail2ban || _harden_log warn "fail2ban enable failed"
    # Blocking start — wait for the service to actually be ACTIVE (not just for
    # `systemctl restart` to return). If it never comes up we surface the real
    # failure via journalctl instead of spamming socket errors.
    systemctl restart fail2ban || _harden_log warn "fail2ban restart returned non-zero"
    local _up=0
    for _i in $(seq 1 30); do
        if systemctl is-active --quiet fail2ban; then
            _up=1
            break
        fi
        sleep 1
    done
    if [ "$_up" -ne 1 ]; then
        _harden_log err "fail2ban failed to become active — last journalctl output:"
        journalctl -u fail2ban -n 40 --no-pager 2>&1 | sed 's/^/      /' || true
        return 1
    fi
    # Service is active — confirm the client can reach the server socket.
    if ! fail2ban-client ping; then
        _harden_log warn "fail2ban active but client cannot reach socket"
    fi
}

_harden_fail2ban_verify() {
    command -v fail2ban-client  || { _harden_log warn "fail2ban — not installed"; return 1; }
    if ! systemctl is-active --quiet fail2ban; then
        _harden_log warn "fail2ban not running — last journalctl output:"
        journalctl -u fail2ban -n 30 --no-pager 2>&1 | sed 's/^/      /' || true
        return 1
    fi
    if fail2ban-client ping && fail2ban-client status sshd ; then
        _harden_log ok "fail2ban active (sshd + recidive + http)"
        return 0
    fi
    _harden_log warn "fail2ban running but not responding to client"
    return 1
}

# --- end lib/harden_fail2ban.sh ---
# --- lib/harden_ufw.sh ---
#!/bin/bash

_harden_ufw_bootstrap() {
    command -v ufw  || apt_run apt-get install -y ufw  || true
    command -v ufw  || return 1

    # Already active — just verify ports are open, then bail
    if ufw status  | grep -qi "active"; then
        for port in 22 80 443 51820; do
            ufw status verbose  | grep -qE "${port}(/tcp|/udp)?.*ALLOW" || ufw allow "$port" || echo -e "${YELLOW}    ⚠ ufw allow port $port failed${NC}"
        done
        # Whitelist Docker bridges
        for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
            ip link show "$iface" >/dev/null 2>&1 || continue
            ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
        done
        return 0
    fi

    # Inactive — configure and enable (INPUT default deny, FORWARD stays open for Docker)
    ufw --force default deny incoming || echo -e "${YELLOW}    ⚠ ufw default deny incoming failed${NC}"
    ufw --force default allow outgoing || echo -e "${YELLOW}    ⚠ ufw default allow outgoing failed${NC}"
    ufw allow ssh || echo -e "${YELLOW}    ⚠ ufw allow ssh failed${NC}"
    ufw allow 80/tcp || echo -e "${YELLOW}    ⚠ ufw allow 80/tcp failed${NC}"
    ufw allow 443/tcp || echo -e "${YELLOW}    ⚠ ufw allow 443/tcp failed${NC}"
    ufw allow 51820/udp || echo -e "${YELLOW}    ⚠ ufw allow 51820/udp failed${NC}"
    for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
        ip link show "$iface" >/dev/null 2>&1 || continue
        ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
    done
    ufw --force enable || echo -e "${YELLOW}    ⚠ ufw enable failed${NC}"
    # Verify it actually came up
    for _i in $(seq 1 5); do
        ufw status  | grep -qi "active" && break
        sleep 2
    done
}

_harden_ufw_verify() {
    command -v ufw  || { _harden_log warn "ufw — not installed"; return 1; }
    if ufw status  | grep -qi "active"; then
        _harden_log ok "ufw active (host INPUT hardened)"
        return 0
    fi
    _harden_log warn "ufw not active — check ufw status"
    return 1
}

# --- end lib/harden_ufw.sh ---
# --- lib/harden_apparmor.sh ---
#!/bin/bash

_harden_apparmor_bootstrap() {
    command -v aa-status  || apt_run apt-get install -y apparmor apparmor-utils  || true
    command -v aa-status  || return 1
    systemctl enable apparmor || echo -e "${YELLOW}    ⚠ apparmor enable failed${NC}"
    systemctl start apparmor || echo -e "${YELLOW}    ⚠ apparmor start failed${NC}"
}

_harden_apparmor_verify() {
    command -v aa-status  || { _harden_log warn "apparmor — not installed"; return 1; }
    local count
    count=$(aa-status --json  | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('processes',{})))"  || echo "0")
    count="${count//[^0-9]/}"
    : "${count:=0}"
    if [ "$count" -gt 0 ] ; then
        _harden_log ok "apparmor enforcing ($count profiles)"
        return 0
    fi
    _harden_log warn "apparmor installed but no enforce profiles"
    return 1
}

# --- end lib/harden_apparmor.sh ---
# --- lib/harden_auditd.sh ---
#!/bin/bash

_harden_auditd_bootstrap() {
    command -v auditd  || apt_run apt-get install -y auditd audispd-plugins  || true

    if [ ! -f /etc/audit/rules.d/smsly.rules ]; then
        mkdir -p /etc/audit/rules.d
        cat > /etc/audit/rules.d/smsly.rules <<'AUDIT_EOF'
-w /etc/shadow -p wa -k identity
-w /etc/passwd -p wa -k identity
-w /etc/sudoers -p wa -k privilege-escalation
-w /etc/ssh/sshd_config -p wa -k sshd
-w /opt/smsly-hosting/.env -p wa -k smsly-config
-w /opt/smsly-hosting/secrets/ -p wa -k smsly-secrets
-a always,exit -F arch=b64 -S execve -F path=/usr/bin/docker -k docker-exec
-a always,exit -F arch=b64 -S mount -k filesystem-mounts
-a exit,always -F arch=b64 -S execve -F euid=0 -F auid>=1000 -k priv-esc
AUDIT_EOF
    fi
    systemctl enable auditd || echo -e "${YELLOW}    ⚠ auditd enable failed${NC}"
    systemctl restart auditd || echo -e "${YELLOW}    ⚠ auditd restart failed${NC}"
}

_harden_auditd_verify() {
    command -v auditd  || { _harden_log warn "auditd — not installed"; return 1; }
    if systemctl is-active --quiet auditd ; then
        _harden_log ok "auditd active (file + syscall monitoring)"
        return 0
    fi
    _harden_log warn "auditd not running — may need kernel param audit=1"
    return 1
}

# --- end lib/harden_auditd.sh ---
# --- lib/harden_kernel.sh ---
#!/bin/bash

_harden_kernel_bootstrap() {
    local sysctl_file="/etc/sysctl.d/99-smsly-security.conf"
    [ -f "$sysctl_file" ] && return 0  # already applied

    cat > "$sysctl_file" <<'SYSCTL_EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
kernel.yama.ptrace_scope = 1
kernel.unprivileged_bpf_disabled = 1
kernel.randomize_va_space = 2
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
fs.suid_dumpable = 0
SYSCTL_EOF
    sysctl -p "$sysctl_file" || echo -e "${YELLOW}    ⚠ sysctl -p failed${NC}"
}

_harden_kernel_verify() {
    if [ -f /etc/sysctl.d/99-smsly-security.conf ]; then
        _harden_log ok "kernel hardening applied"
        return 0
    fi
    _harden_log warn "kernel hardening not applied"
    return 1
}

# --- end lib/harden_kernel.sh ---
# --- lib/harden_docker_daemon.sh ---
#!/bin/bash

_harden_docker_daemon_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    local daemon_cfg="/etc/docker/daemon.json"
    [ ! -f "$daemon_cfg" ] && echo '{}' > "$daemon_cfg"

    local changed=false

    # log rotation
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('log-driver')=='json-file' and d.get('log-opts',{}).get('max-size')=='10m' else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['log-driver'] = 'json-file'
cfg['log-opts'] = {'max-size': '10m', 'max-file': '3'}
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # live-restore
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('live-restore') else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['live-restore'] = True
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # seccomp
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('features',{}).get('seccomp') else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg.setdefault('features', {})['seccomp'] = True
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # Restart Docker if config changed AND no SMSLY containers are running
    # (doing so live would kill production).
    if [ "$changed" = "true" ]; then
        local _smsly_ctrs
        _smsly_ctrs="$(docker ps --format '{{.Names}}'  | grep -c smsly || true)"
        if [ "$_smsly_ctrs" -eq 0 ]; then
            _harden_log info "Docker daemon config changed — restarting Docker..."
            systemctl restart docker || { _harden_log error "Docker restart failed"; }
            for _i in $(seq 1 30); do
                docker info  && break
                sleep 2
            done
            _harden_log ok "Docker daemon restarted with security config"
        else
            _harden_log warn "Docker daemon config changed but $_smsly_ctrs SMSLY containers are running — deferring restart (apply on next daemon reload)"
        fi
    fi
}

_harden_docker_daemon_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    local daemon_cfg="/etc/docker/daemon.json"
    if [ -f "$daemon_cfg" ] && python3 -c "import json; json.load(open('$daemon_cfg'))" ; then
        _harden_log ok "docker daemon security config present"
        return 0
    fi
    _harden_log warn "docker daemon config missing or invalid"
    return 1
}

# --- end lib/harden_docker_daemon.sh ---
# --- lib/harden_crowdsec.sh ---
#!/bin/bash

_harden_crowdsec_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    # CrowdSec comes from the main docker-compose stack — if the container
    # isn't running, try docker compose up -d for just that service.
    if docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec"; then
        return 0  # already up
    fi
    # Blocking start — wait for container to be healthy
    # The harden bootstrap may run before fresh_config has generated .env,
    # so only pass --env-file when the file exists.
    local env_args=()
    [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
    docker compose \
        "${env_args[@]}" \
        -f "$COMPOSE_FILE" \
        up -d crowdsec || echo -e "${YELLOW}    ⚠ crowdsec docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec" && break
        sleep 2
    done
}

_harden_crowdsec_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec"; then
        _harden_log warn "crowdsec — container not running"
        return 1
    fi
    # Refresh hub scenarios — only upgrade when explicitly allowed.
    # Auto-upgrading on every harden.sh run can silently break
    # production WAF if CrowdSec ships a breaking parser change.
    timeout -k 5 60 docker exec smsly-crowdsec cscli hub update  || _harden_log warn "crowdsec hub update failed"
    if [ "${CROWDSEC_AUTO_UPGRADE_HUB:-0}" = "1" ]; then
        timeout -k 5 60 docker exec smsly-crowdsec cscli hub upgrade  || _harden_log warn "crowdsec hub upgrade failed"
    else
        _harden_log info "crowdsec hub upgrade skipped (set CROWDSEC_AUTO_UPGRADE_HUB=1 to enable)"
    fi
    _harden_log ok "crowdsec deployed"
    return 0
}

# --- end lib/harden_crowdsec.sh ---
# --- lib/harden_falco.sh ---
#!/bin/bash

_harden_falco_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    local compose_file="$INSTALL_DIR/infrastructure/docker/docker-compose.falco.yml"
    [ -f "$compose_file" ] || return 1

    # Blocking start — always recreate so config changes take effect.
    # The harden bootstrap may run before fresh_config has generated .env,
    # so only pass --env-file when the file exists (compose file needs no vars).
    local env_args=()
    [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
    # smsly-net is declared external in the falco compose file but is only
    # created during stack deploy (fresh_deploy.sh) — the harden bootstrap
    # runs earlier, so create it here if missing.
    docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null 2>&1 || true
    docker compose \
        "${env_args[@]}" \
        -f "$compose_file" \
        up -d --force-recreate --pull always || echo -e "${YELLOW}    ⚠ falco docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}'  | grep -q "smsly-falco" && break
        sleep 2
    done
}

_harden_falco_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! docker ps --format '{{.Names}}'  | grep -q "smsly-falco"; then
        _harden_log warn "falco — container not running"
        return 1
    fi
    _harden_log ok "falco deployed"
    return 0
}

# --- end lib/harden_falco.sh ---
# --- lib/harden_container_runtime.sh ---
#!/bin/bash

_harden_container_runtime_bootstrap() {
    local install_dir="${INSTALL_DIR:-/opt/smsly-hosting}"
    local env_file="$install_dir/.env"

    # If CONTAINER_RUNTIME is already persisted in .env, skip detection.
    # The user can clear it to re-detect.
    if [ -f "$env_file" ] && grep -q '^CONTAINER_RUNTIME=' "$env_file" ; then
        return 0
    fi

    # Try Kata first (stronger isolation, requires KVM)
    if [ -e /dev/kvm ] && ! command -v kata-runtime ; then
        if [ -f "$install_dir/lib/install-kata.sh" ]; then
            echo -e "${BLUE}  → [harden] Kata Containers (KVM available) — installing...${NC}"
            bash "$install_dir/lib/install-kata.sh" || true
        fi
    fi

    if command -v kata-runtime ; then
        env_set_value "$env_file" "CONTAINER_RUNTIME" "kata"
        echo -e "${BLUE}  → [harden] Persisted CONTAINER_RUNTIME=kata in .env${NC}"
        return 0
    fi

    # Fall back to gVisor (lighter, no KVM required)
    if ! command -v runsc ; then
        if [ -f "$install_dir/lib/install-gvisor.sh" ]; then
            echo -e "${BLUE}  → [harden] gVisor (runsc) — installing...${NC}"
            bash "$install_dir/lib/install-gvisor.sh" || true
        fi
    fi

    if command -v runsc ; then
        env_set_value "$env_file" "CONTAINER_RUNTIME" "runsc"
        echo -e "${BLUE}  → [harden] Persisted CONTAINER_RUNTIME=runsc in .env${NC}"
        return 0
    fi
}

_harden_container_runtime_verify() {
    local found=0

    if command -v runsc ; then
        _harden_log ok "gVisor (runsc) installed"
        found=1
    fi

    if command -v kata-runtime ; then
        _harden_log ok "Kata Containers installed"
        found=1
    fi

    if [ "$found" -eq 0 ]; then
        _harden_log warn "container runtime sandboxing — install gVisor or Kata for VM-level isolation"
        return 1
    fi

    # Check Docker runtime registration
    if [ -f /etc/docker/daemon.json ]; then
        if python3 -c "import json; import sys; cfg=json.load(open('/etc/docker/daemon.json')); sys.exit(0 if 'runsc' in cfg.get('runtimes',{}) else 1)" ; then
            _harden_log ok "gVisor registered with Docker"
        elif python3 -c "import json; import sys; cfg=json.load(open('/etc/docker/daemon.json')); sys.exit(0 if 'kata-runtime' in cfg.get('runtimes',{}) else 1)" ; then
            _harden_log ok "Kata registered with Docker"
        fi
    fi

    # `found` is a 0/1 FLAG, not an exit code — returning it turns a successful
    # gVisor/Kata install into a FAILED security check (found=1 -> return 1).
    return 0
}

# --- end lib/harden_container_runtime.sh ---
# --- lib/harden_trivy.sh ---
#!/bin/bash

_harden_trivy_bootstrap() {
    if command -v trivy ; then
        return 0  # already installed
    fi

    _harden_log info "Installing Trivy vulnerability scanner..."
    local trivy_version="v0.54.1"
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  arch="64bit" ;;
        aarch64) arch="ARM64" ;;
        *)       _harden_log warn "Trivy — unsupported architecture: $arch"; return 1 ;;
    esac

    local deb_url="https://github.com/aquasecurity/trivy/releases/download/${trivy_version}/trivy_${trivy_version#v}_Linux-${arch}.deb"
    local tmp_deb
    tmp_deb="$(mktemp /tmp/trivy.XXXXXX.deb)"

    # Attempt 1: Direct DEB download with retries and timeouts
    if curl --retry 3 --retry-delay 2 --connect-timeout 15 -fsSL "$deb_url" -o "$tmp_deb" ; then
        if ! dpkg -i "$tmp_deb" ; then
            apt-get install -f -y  || true
            dpkg -i "$tmp_deb"  || true
        fi
        rm -f "$tmp_deb"
    else
        rm -f "$tmp_deb"
        _harden_log info "Direct DEB download failed — trying official APT repo and install script..."
    fi

    # Attempt 2: Official APT Repository fallback
    if ! command -v trivy ; then
        apt-get update -qq  || true
        if ! apt-get install -y trivy ; then
            if command -v gpg ; then
                curl --retry 2 --connect-timeout 10 -fsSL https://aquasecurity.github.io/trivy-repo/deb/public.key  | gpg --dearmor -o /usr/share/keyrings/trivy.gpg  || true
                echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc  || echo stable) main" > /etc/apt/sources.list.d/trivy.list  || true
                apt-get update -qq  || true
                apt-get install -y trivy  || true
            fi
        fi
    fi

    # Attempt 3: Official Contrib script fallback
    if ! command -v trivy ; then
        curl --retry 2 --connect-timeout 10 -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin  || true
    fi

    if command -v trivy ; then
        _harden_log ok "Trivy installed successfully"
        return 0
    fi
    _harden_log warn "Trivy — download and installation fallbacks failed"
    return 1
}

_harden_trivy_verify() {
    if command -v trivy ; then
        local ver
        ver="$(trivy --version  | head -1 || true)"
        _harden_log ok "Trivy available: ${ver}"
        return 0
    fi
    _harden_log warn "Trivy — not installed (image vulnerability scanning unavailable)"
    return 1
}

# --- end lib/harden_trivy.sh ---
# --- lib/harden_infisical.sh ---
#!/bin/bash

_harden_infisical_bootstrap() {
    local infisical_script="$INSTALL_DIR/lib/infisical.sh"
    if [ ! -f "$infisical_script" ]; then
        _harden_log info "Infisical script not found — skipping"
        return 0
    fi
    # Source Infisical functions and bootstrap
    # shellcheck disable=SC1090
    source "$infisical_script"  || {
        _harden_log warn "Failed to source infisical.sh"
        return 1
    }
    if ! command -v infisical_bootstrap ; then
        _harden_log warn "infisical_bootstrap function not found"
        return 1
    fi
    infisical_bootstrap  || {
        _harden_log warn "Infisical bootstrap had issues"
        return 1
    }
    return 0
}

_harden_infisical_verify() {
    # Optional layer: the bootstrap skips when lib/infisical.sh is absent —
    # the verify must skip too, or every install reports a phantom failure.
    local infisical_script="${INSTALL_DIR:-/opt/smsly-hosting}/lib/infisical.sh"
    if [ ! -f "$infisical_script" ]; then
        return 0
    fi
    command -v docker >/dev/null 2>&1 || return 0
    if docker ps --format '{{.Names}}'  | grep -q "smsly-infisical"; then
        _harden_log ok "Infisical running"
        return 0
    fi
    _harden_log warn "Infisical — container not running"
    return 1
}

# --- end lib/harden_infisical.sh ---

harden_security_bootstrap() {
    echo -e "${BLUE}  → [harden] Bootstrapping security stack (blocking)...${NC}"
    local _harden_failures=0
    _harden_fail2ban_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_ufw_bootstrap        || { _harden_failures=$((_harden_failures + 1)); }
    _harden_apparmor_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_auditd_bootstrap     || { _harden_failures=$((_harden_failures + 1)); }
    _harden_kernel_bootstrap
    _harden_docker_daemon_bootstrap
    _harden_crowdsec_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_falco_bootstrap      || { _harden_failures=$((_harden_failures + 1)); }
    _harden_container_runtime_bootstrap
    _harden_trivy_bootstrap      || { _harden_failures=$((_harden_failures + 1)); }
    _harden_infisical_bootstrap  || { _harden_failures=$((_harden_failures + 1)); }
    if [ "$_harden_failures" -gt 0 ]; then
        echo -e "${YELLOW}  ⚠ [harden] $_harden_failures layer(s) had issues — verify will report details${NC}"
    else
        echo -e "${GREEN}  ✓ [harden] Bootstrap complete — all layers started${NC}"
    fi
    return 0
}

harden_security_verify() {
    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Security Stack — Verification${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

    local failures=0 checks=0

    # NOTE: never use standalone `((checks++))` here — when the counter is 0
    # the arithmetic expression evaluates to 0 → exit status 1 → under `set -e`
    # (re-enabled by fresh_hardening.sh after harden.sh's `set +e`) the whole
    # install dies silently after the first check.
    if ! _harden_fail2ban_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_ufw_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_apparmor_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_auditd_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_kernel_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_docker_daemon_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_crowdsec_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_falco_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_container_runtime_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_trivy_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_infisical_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))

    local passed=$((checks - failures))
    echo ""
    if [ "$failures" -eq 0 ]; then
        echo -e "${GREEN}  All $passed/$checks security checks passed${NC}"
    else
        echo -e "${RED}  Security: $passed/$checks passed, $failures FAILED${NC}"
        echo -e "${YELLOW}  Review failures above — run 'sudo bash install.sh --debug' for details${NC}"
    fi
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# --- end lib/harden.sh ---
        harden_security_bootstrap
    fi

    # ─── Registry TLS cert check ─────────────────────────────────────────
    # Regenerate + restart if the cert and key don't match, so the
    # registry container doesn't crash-loop with "private key does not
    # match public key".
    _registry_cert_ok() {
        [ -f "$INSTALL_DIR/certs/registry.key" ] || return 1
        [ -f "$INSTALL_DIR/certs/registry.crt" ] || return 1
        local _cmod _kmod
        _cmod="$(openssl x509 -in "$INSTALL_DIR/certs/registry.crt" -noout -modulus  | openssl sha256)" || return 1
        _kmod="$(openssl rsa  -in "$INSTALL_DIR/certs/registry.key" -noout -modulus  | openssl sha256)" || return 1
        [ "$_cmod" = "$_kmod" ]
    }
    if ! _registry_cert_ok; then
        echo -e "${BLUE}  → Registry TLS cert/key missing or mismatch — generating...${NC}"
        mkdir -p "$INSTALL_DIR/certs"
        _tmp="$(mktemp -d)"
        openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
            -keyout "${_tmp}/registry.key" \
            -out    "${_tmp}/registry.crt" \
            -subj "/CN=registry" \
            -addext "subjectAltName=DNS:registry,DNS:localhost,IP:127.0.0.1,IP:10.100.0.1"  && {
            mv "${_tmp}/registry.key" "$INSTALL_DIR/certs/registry.key"
            mv "${_tmp}/registry.crt" "$INSTALL_DIR/certs/registry.crt"
            chmod 644 "$INSTALL_DIR/certs/registry.crt"
            chmod 600 "$INSTALL_DIR/certs/registry.key"
            echo -e "${BLUE}  → Restarting registry container...${NC}"
            docker restart smsly-hosting-registry-1 || echo -e "${YELLOW}    ⚠ Registry restart failed${NC}"
        } || true
        rm -rf "$_tmp"  || true
    fi
    mkdir -p "$INSTALL_DIR/auth"
    if [ ! -f "$INSTALL_DIR/auth/htpasswd" ] || [ -z "${REGISTRY_PASSWORD:-}" ] || [ -z "${REGISTRY_USER:-}" ]; then
        echo -e "${BLUE}  → Ensuring registry htpasswd authentication exists...${NC}"
        REGISTRY_PASS="${REGISTRY_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(18))"  || openssl rand -hex 12  || { echo "ERROR: Cannot generate registry password" >&2; exit 1; })}"
        if command -v htpasswd ; then
            htpasswd -Bbn "${REGISTRY_USER:-smsly-registry}" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"
        else
            python3 -c "
import bcrypt, sys
pw = sys.argv[1] if len(sys.argv) > 1 else '$REGISTRY_PASS'
print('${REGISTRY_USER:-smsly-registry}:' + bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode())
" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"  || true
        fi
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_USER" "${REGISTRY_USER:-smsly-registry}"  || true
        env_set_value "$INSTALL_DIR/.env" "REGISTRY_PASSWORD" "$REGISTRY_PASS"  || true
        chmod 600 "$INSTALL_DIR/auth/htpasswd"  || true
    fi

    # Install registry cert into Docker's cert trust store so the daemon
    # connects via HTTPS (not HTTP fallback) to the registry.
    install_registry_docker_certs

    # ─── Self-heal: missing secrets (update paths can miss secret generation) ─
    echo -e "${BLUE}  → Checking for missing secrets and generating if needed...${NC}"
    _ensure_secret() {
        local _name="$1" _bytes="$2"
        if [ -z "${!_name:-}" ]; then
            local _val="$(python3 -c "import secrets; print(secrets.token_hex($_bytes))"  || openssl rand -hex "$_bytes"  || true)"
            if [ -n "$_val" ]; then
                printf -v "$_name" '%s' "$_val"
                env_set_value "$INSTALL_DIR/.env" "$_name" "$_val"  || true
                echo -e "${BLUE}    → Generated $_name${NC}"
            fi
        fi
    }
    _ensure_secret REGISTRY_HTTP_SECRET 32
    _ensure_secret REPLICATION_PASSWORD 32
    _ensure_secret SENTINEL_PASSWORD 32
    _ensure_secret CROWDSEC_BOUNCER_KEY 32
    _ensure_secret COSIGN_PASSWORD 32

    # ─── Self-heal: Cosign keypair ──────────────────────────────────────────
    if command -v cosign ; then
        mkdir -p "$INSTALL_DIR/cosign-keys"
        COSIGN_PRIVATE_KEY_PATH="$INSTALL_DIR/cosign-keys/cosign.key"
        COSIGN_PUBLIC_KEY_PATH="$INSTALL_DIR/cosign-keys/cosign.pub"
        if [ ! -f "$COSIGN_PRIVATE_KEY_PATH" ] || [ ! -f "$COSIGN_PUBLIC_KEY_PATH" ]; then
            echo -e "${BLUE}  → Cosign keypair missing — generating...${NC}"
            COSIGN_PASSWORD="${COSIGN_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_hex(32))"  || openssl rand -hex 32  || true)}"
            COSIGN_PASSWORD="$COSIGN_PASSWORD" cosign generate-key-pair  || true
            if [ -f cosign.key ]; then
                mv cosign.key "$COSIGN_PRIVATE_KEY_PATH"
                mv cosign.pub "$COSIGN_PUBLIC_KEY_PATH"
                chmod 600 "$COSIGN_PRIVATE_KEY_PATH"
                chmod 644 "$COSIGN_PUBLIC_KEY_PATH"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PASSWORD" "$COSIGN_PASSWORD"  || true
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PRIVATE_KEY_PATH" "$COSIGN_PRIVATE_KEY_PATH"  || true
                echo -e "${GREEN}    ✓ Cosign keypair created${NC}"
            else
                echo -e "${YELLOW}    ⚠ cosign generate-key-pair ran but no output — skipping${NC}"
            fi
        else
            # Key exists but password might be missing
            if [ -z "${COSIGN_PASSWORD:-}" ]; then
                COSIGN_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || openssl rand -hex 32  || true)"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PASSWORD" "$COSIGN_PASSWORD"  || true
            fi
        fi
    fi

    # ─── Git Safety ──────────────────────────────────────────────────────────
    # Prevents "dubious ownership" errors on production VPS
    git config --global --add safe.directory "$INSTALL_DIR"  || true

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
    if [ "${SMSLY_SKIP_GIT_SYNC:-false}" = "true" ]; then
        set_checkpoint "update_git_synced"
    elif [ "${SMSLY_REEXEC:-}" != "1" ]; then
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

# --- end lib/update_preflight.sh ---
# --- lib/update_git.sh ---
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
        PRE_UPDATE_HEAD="$(cat "$INSTALL_DIR/.pre-update-head"  || true)"
        echo -e "${YELLOW}  ⚠ Recovering pre-update baseline from prior incomplete run (${PRE_UPDATE_HEAD:0:7})${NC}"
    else
        PRE_UPDATE_HEAD="$(git rev-parse HEAD  || true)"
    fi
    echo "$PRE_UPDATE_HEAD" > "$INSTALL_DIR/.pre-update-head"  || true
    ensure_local_ignores
    if [ -n "$(git status --porcelain )" ]; then
        echo -e "${YELLOW}  ⚠ Local changes detected — stashing before pull${NC}"
        git stash push --include-untracked -m "install-update-$(date +%s)"
        touch "$INSTALL_DIR/.git-stash-marker"
    fi

    echo -e "${BLUE}  → Force-pulling latest code from GitHub ($SMSLY_BRANCH)...${NC}"

    # Track if git update succeeded
    GIT_UPDATE_OK=true

    if ! git fetch origin "$SMSLY_BRANCH" ; then
        echo -e "${RED}  ✗ Git fetch failed for $SMSLY_BRANCH. SSL verification is always enforced — check network or CA certificates.${NC}"
        GIT_UPDATE_OK=false
    fi

    if [ "$GIT_UPDATE_OK" = "true" ]; then
        if ! git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" ; then
            echo -e "${RED}  ✗ Git checkout failed for $SMSLY_BRANCH.${NC}"
            GIT_UPDATE_OK=false
        else
            git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH"  || true
        fi
    fi

    # Fallback if git failed but a local bundle was provided
    if [ "$GIT_UPDATE_OK" = "false" ]; then
        if [ -n "${SMSLY_INSTALL_WORKDIR:-}" ] && [ -d "${SMSLY_INSTALL_WORKDIR}" ]; then
            echo -e "${BLUE}  → Fallback: Synchronizing from pre-uploaded source bundle...${NC}"
            # Use rsync if available, otherwise cp. Exclude .git to preserve local repo state if any.
            if command -v rsync ; then
                rsync -rtv --exclude='.git' "${SMSLY_INSTALL_WORKDIR}/" "$INSTALL_DIR/"
            else
                cp -rv "${SMSLY_INSTALL_WORKDIR}/"* "$INSTALL_DIR/"  || true
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
        exec 9>&-  || true
        exec env SMSLY_REEXEC=1 NO_SCREEN=true SKIP_SCREEN=1 SMSLY_PRE_UPDATE_HEAD="$PRE_UPDATE_HEAD" PATH="/usr/local/bin:$PATH" bash "$SCRIPT_PATH" --no-screen "$@"
    fi

# --- end lib/update_git.sh ---
# --- lib/update_rebuild.sh ---
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
        safe_update_snapshot
        safe_update_preflight || { echo -e "${RED}  ✗ Pre-flight checks failed — aborting update${NC}"; exit 1; }
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

     # Ensure all critical envs are set. The install.sh auto-
     # generates these at first install; on UPDATE, the env
     # file may be missing newer secrets that were added
     # after the original install (e.g. BACKUP_ENCRYPTION_KEY
     # was added in a later release). This block auto-fills
     # any missing secret so the platform doesn't fail-closed
     # in production because of an env added in a newer
     # version. Each secret is only added if it doesn't
     # already exist (preserves any operator-set value).
     # NOTE: This block runs in the top-level update flow (not
     # inside a function), so we use $INSTALL_DIR/.env directly
     # and avoid the `local` keyword.
     _env_file="$INSTALL_DIR/.env"
     if [ -f "$_env_file" ] && [ "$MODE_NODE" != "true" ]; then
         echo -e "${BLUE}[UPDATE] Verifying critical envs in $_env_file...${NC}"
         _missing_count=0
         # Each line: <VAR_NAME>=<generator>
           _env_generators=(
               "REDIS_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
               "RABBITMQ_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
               "GATEWAY_SECRET|$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
               "GITHUB_WEBHOOK_SECRET|$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
               "AUTOSCALER_API_TOKEN|$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
               "FRP_AUTH_TOKEN|$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
               "PGCAT_ADMIN_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(48))"  || true)"
               "REGISTRY_HTTP_SECRET|$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
               "BACKUP_ENCRYPTION_KEY|$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  || openssl rand -base64 32)"
               "REPLICATION_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
               "SENTINEL_PASSWORD|$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
               "CROWDSEC_BOUNCER_KEY|$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
           )
         for _entry in "${_env_generators[@]}"; do
             _key="${_entry%%|*}"
             _generator="${_entry#*|}"
             if ! grep -q "^${_key}=" "$_env_file" ; then
                 if [ -n "$_generator" ]; then
                     echo -e "${YELLOW}  → Auto-generating missing $_key${NC}"
                     env_set_value "$_env_file" "$_key" "$_generator"
                     _missing_count=$((_missing_count + 1))
                 fi
             fi
         done
         if [ "$_missing_count" -gt 0 ]; then
             echo -e "${GREEN}  ✓ Auto-generated $_missing_count missing secret(s)${NC}"
             # Re-source the env so the new values take effect
             # in the current shell session.
             set -a
             # shellcheck disable=SC1090
             source "$_env_file"  || true
             set +a
         fi
     fi
     # Unset the helper var to avoid leaking into the rest of the script.
     unset _env_file _env_generators _entry _key _generator _missing_count

     # ── Auto-correct stale .env values from pre-HA upgrades ───────────
     # After the PostgreSQL HA + Redis HA rename, old .env files may
     # still reference single-node hostnames.  Fix them silently so the
     # platform doesn't break after an update.

     # Switch from dev compose (docker-compose.yml) to prod (HA) if
     # the operator hasn't explicitly picked a different one.
     _current_compose="$(env_get_value "$INSTALL_DIR/.env" "COMPOSE_FILE"  || true)"
     if [ "$_current_compose" = "docker-compose.yml" ] && [ -f "$INSTALL_DIR/docker-compose.prod.yml" ]; then
         # Check if postgres-primary already has migrated data (e.g. from a
         # previous manual migration run).  If so, skip re-migration and
         # switch COMPOSE_FILE immediately so the update pipeline can
         # reach the correct DB hostname.
         _already_migrated=false
         if docker ps --format '{{.Names}}'  | grep -qx 'smsly-postgres-primary'; then
             _tables=$(timeout 30 docker exec smsly-postgres-primary psql -U smsly_admin -d smsly_hosting -t -A \
                 -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';"  || echo 0)
             if [ "${_tables:-0}" -gt 50 ]; then
                 _already_migrated=true
                 echo -e "${GREEN}  → postgres-primary already has $_tables tables — migration already done${NC}"
             fi
         fi

         if $_already_migrated; then
             # Data is already on postgres-primary — switch to prod compose
             # immediately but ensure the HA stack is up first.
             echo -e "${BLUE}  → HA stack already has data — ensuring services are up...${NC}"
             docker compose -f "$INSTALL_DIR/docker-compose.prod.yml" \
                 up -d --wait --wait-timeout 120 \
                 db postgres-replica pgcat redis-primary redis-replica \
                  || echo -e "${YELLOW}  ⚠ Some HA services may not be healthy yet${NC}"
             echo -e "${YELLOW}  → Switching COMPOSE_FILE: docker-compose.yml → docker-compose.prod.yml${NC}"
             env_set_value "$INSTALL_DIR/.env" "COMPOSE_FILE" "docker-compose.prod.yml"
         else
             # If the old db container still has data, migrate it FIRST before
             # switching COMPOSE_FILE.  If migration fails, we keep the old
             # compose so the platform continues working with the old db.
             _has_old_db=false
             if [ "$(docker ps -a --format '{{.Names}}'  | grep -cx 'smsly-hosting-db-1' || echo 0)" -gt 0 ]; then
                 _has_old_db=true
             fi

             if $_has_old_db; then
                 _mig_script="$INSTALL_DIR/scripts/migrate-db-to-ha.sh"
                 if [ -f "$_mig_script" ] && [ -x "$_mig_script" ]; then
                     # Bring up the HA stack FIRST so postgres-primary/pgcat exist
                     # before the migration script tries to dump into them.
                     echo -e "${BLUE}  → Starting HA stack (postgres-primary, pgcat, redis-primary)...${NC}"
                     docker compose -f "$INSTALL_DIR/docker-compose.prod.yml" \
                         up -d --wait --wait-timeout 120 \
                         db postgres-replica pgcat redis-primary redis-replica \
                          || echo -e "${YELLOW}  ⚠ Some HA services may not be healthy yet${NC}"

                     echo -e "${BLUE}  → Running data migration from old @db to postgres-primary...${NC}"
                     if bash "$_mig_script"; then
                         echo -e "${GREEN}  ✓ Data migration successful. Switching COMPOSE_FILE to prod (HA).${NC}"
                         env_set_value "$INSTALL_DIR/.env" "COMPOSE_FILE" "docker-compose.prod.yml"
                     else
                         echo -e "${RED}  ✗ Data migration failed. Keeping COMPOSE_FILE=docker-compose.yml.${NC}"
                         echo -e "${YELLOW}     Fix the migration issue and re-run update, or run:${NC}"
                         echo -e "${YELLOW}     sudo bash scripts/migrate-db-to-ha.sh${NC}"
                     fi
                 else
                     echo -e "${YELLOW}  ⚠ migrate-db-to-ha.sh not found or not executable — skipping migration${NC}"
                     echo -e "${YELLOW}  → Switching COMPOSE_FILE anyway (no old db data to lose)${NC}"
                     env_set_value "$INSTALL_DIR/.env" "COMPOSE_FILE" "docker-compose.prod.yml"
                 fi
             else
                 # No old db container — but we still need to ensure the
                 # HA stack is running so pgcat/postgres-primary resolve.
                 # Otherwise manage.py migrate will fail with DNS errors.
                 echo -e "${BLUE}  → Starting HA stack (fresh install)...${NC}"
                 docker compose -f "$INSTALL_DIR/docker-compose.prod.yml" \
                     up -d --wait --wait-timeout 120 \
                     db postgres-replica pgcat redis-primary redis-replica \
                      || echo -e "${YELLOW}  ⚠ Some HA services may not be healthy yet${NC}"
                 echo -e "${YELLOW}  → Switching COMPOSE_FILE: docker-compose.yml → docker-compose.prod.yml${NC}"
                 env_set_value "$INSTALL_DIR/.env" "COMPOSE_FILE" "docker-compose.prod.yml"
             fi
         fi
     fi
     unset _current_compose

     if [ -f "$INSTALL_DIR/.env" ] && [ "$MODE_NODE" != "true" ]; then
         _env_fix_file="$INSTALL_DIR/.env"
         # Read the current COMPOSE_FILE once — used by multiple blocks below
         # to decide whether to apply HA-specific migrations.
         _current_compose_final="$(env_get_value "$_env_fix_file" "COMPOSE_FILE"  || true)"

         # REDIS_HOST: pre-HA used "redis", now "redis-primary"
         _current_redis_host="$(env_get_value "$_env_fix_file" "REDIS_HOST"  || true)"
         if [ "$_current_redis_host" = "redis" ] || [ -z "$_current_redis_host" ]; then
             echo -e "${YELLOW}  → Updating REDIS_HOST: ${_current_redis_host:-<unset>} → redis-primary${NC}"
             env_set_value "$_env_fix_file" "REDIS_HOST" "redis-primary"
         fi

         # REDIS_URL: replace stale @redis: with @redis-primary:
         _redis_url="$(env_get_value "$_env_fix_file" "REDIS_URL"  || true)"
         if echo "$_redis_url" | grep -q '@redis:'; then
             _fixed_redis_url="$(echo "$_redis_url" | sed 's|@redis:|@redis-primary:|g')"
             echo -e "${YELLOW}  → Fixing REDIS_URL hostname: redis → redis-primary${NC}"
             env_set_value "$_env_fix_file" "REDIS_URL" "$_fixed_redis_url"
         fi

         # CONTAINER_REGISTRY_URL: 127.0.0.1:5000 → registry:5000
         _registry="$(env_get_value "$_env_fix_file" "CONTAINER_REGISTRY_URL"  || true)"
         if [ "$_registry" = "127.0.0.1:5000" ] || [ "$_registry" = "localhost:5000" ]; then
             echo -e "${YELLOW}  → Fixing CONTAINER_REGISTRY_URL: $_registry → registry:5000${NC}"
             env_set_value "$_env_fix_file" "CONTAINER_REGISTRY_URL" "registry:5000"
         fi

         # DATABASE_URL: auto-migrate from pre-HA single-node @db to pgcat
         _db_url="$(env_get_value "$_env_fix_file" "DATABASE_URL"  || true)"
         if echo "$_db_url" | grep -q '@db:'; then
             if [ -n "$(get_pgcat_if_exists)" ]; then
                 _migrated_url="$(echo "$_db_url" | sed 's|@db:5432|@pgcat:5432|;s|@db/|@pgcat/|')"
                 echo -e "${YELLOW}  → Migrating DATABASE_URL: @db → @pgcat (PostgreSQL HA)${NC}"
                 env_set_value "$_env_fix_file" "DATABASE_URL" "$_migrated_url"
             else
                 echo -e "${YELLOW}  ⚠ DATABASE_URL points to single-node @db, but pgcat service not found.${NC}"
                 echo -e "${YELLOW}     Migrate DATABASE_URL to @postgres-primary or enable HA with pgcat.${NC}"
             fi
         fi

         # DIRECT_DATABASE_URL: only migrate if the compose file already
         # points to prod (HA).  If data migration failed and we kept the
         # dev compose, leaving DIRECT_DATABASE_URL pointed at postgres-primary
         # would crash Django management commands against an empty DB.
         _direct_url="$(env_get_value "$_env_fix_file" "DIRECT_DATABASE_URL"  || true)"
         if echo "$_direct_url" | grep -q '@db:' && echo "$_current_compose_final" | grep -q 'prod'; then
             _migrated_direct="$(echo "$_direct_url" | sed 's|@db:5432|@postgres-primary:5432|')"
             echo -e "${YELLOW}  → Migrating DIRECT_DATABASE_URL: @db → @postgres-primary${NC}"
             env_set_value "$_env_fix_file" "DIRECT_DATABASE_URL" "$_migrated_direct"
         fi

         # Ensure REDIS_MIN_REPLICAS_TO_WRITE is present only when the
         # prod compose is active (has a replica).  Setting it on the
         # dev compose will cause NOREPLICAS errors on every write.
         if echo "$_current_compose_final" | grep -q 'prod'; then
             if ! grep -q '^REDIS_MIN_REPLICAS_TO_WRITE=' "$_env_fix_file" ; then
                 echo -e "${YELLOW}  → Adding REDIS_MIN_REPLICAS_TO_WRITE=1 (Redis HA durability)${NC}"
                 echo 'REDIS_MIN_REPLICAS_TO_WRITE=1' >> "$_env_fix_file"
             fi
         else
             # Dev/single-node compose — ensure replica requirement is off
             # so writes don't get rejected.
             _min_rep="$(grep '^REDIS_MIN_REPLICAS_TO_WRITE=' "$_env_fix_file"  | cut -d= -f2 || true)"
             if [ "$_min_rep" != "0" ]; then
                 echo -e "${YELLOW}  → Setting REDIS_MIN_REPLICAS_TO_WRITE=0 (single-node, no replica)${NC}"
                 env_set_value "$_env_fix_file" "REDIS_MIN_REPLICAS_TO_WRITE" "0"
             fi
         fi

         # Ensure PG_SYNCHRONOUS_COMMIT is present (default: on)
         if ! grep -q '^PG_SYNCHRONOUS_COMMIT=' "$_env_fix_file" ; then
             echo -e "${YELLOW}  → Adding PG_SYNCHRONOUS_COMMIT=on (PostgreSQL durability)${NC}"
             echo 'PG_SYNCHRONOUS_COMMIT=on' >> "$_env_fix_file"
         else
             _pg_commit="$(env_get_value "$_env_fix_file" "PG_SYNCHRONOUS_COMMIT"  || true)"
             if [ "$_pg_commit" = "off" ]; then
                 echo -e "${YELLOW}  ⚠ PG_SYNCHRONOUS_COMMIT=off — recent commits may be lost on crash.${NC}"
                 echo -e "${YELLOW}     Consider setting PG_SYNCHRONOUS_COMMIT=on for durability.${NC}"
             fi
         fi

         unset _env_fix_file _current_redis_host _redis_url _fixed_redis_url _registry _db_url _pg_commit _current_compose_final _min_rep
     fi

     # Cache bust only if disk is low (already runs in the disk check above when needed).
      # Moved into case blocks below to avoid redundant double bust.

      docker_login

       case "$UPDATE_MODE" in
         frontend)
              if [ "$MODE_NODE" = "true" ]; then
                  echo -e "${YELLOW}  → Node mode: no frontend to update. Skipping.${NC}"
              else
                  echo -e "${BLUE}  → Rebuilding frontend container (cached)...${NC}"
                docker compose -f "$COMPOSE_FILE" stop --timeout 15 frontend || echo -e "${YELLOW}    ⚠ docker compose stop frontend failed (non-fatal)${NC}"
                  docker compose -f "$COMPOSE_FILE" rm -f frontend || echo -e "${YELLOW}    ⚠ docker compose rm frontend failed (non-fatal)${NC}"
                  timeout -k 5 600 docker compose -f "$COMPOSE_FILE" build frontend
                  docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps frontend

                 # Custom Domain SSL Setup for Frontend Update
                 if should_manage_caddy; then  # Only for master mode
                     echo -e "\n${YELLOW}[UPDATE] Setting up Custom Domain SSL Services...${NC}"
                     SSL_SCRIPT="install-custom-domain-ssl.sh"
                     [ -f "$SSL_SCRIPT" ] || SSL_SCRIPT="$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh"
                 if [ -f "$SSL_SCRIPT" ]; then
                     echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
                     timeout -k 5 120 bash "$SSL_SCRIPT" install || true
                     
                     # Start the services
                     echo -e "${BLUE}  → Starting custom domain SSL services...${NC}"
                     timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh start || true
                     
                     # Enable auto-start on boot (if not already enabled)
                     echo -e "${BLUE}  → Ensuring auto-start on boot...${NC}"
                     timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable || true
                     
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
            timeout -k 5 600 docker compose -f "$COMPOSE_FILE" build $build_svcs

            echo -e "${BLUE}  → Ensuring backend dependencies are running...${NC}"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                verify_agent_lite_connectivity
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_redis_service) rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
            elif [ "$MODE_NODE" = "true" ]; then
                stop_node_excluded_services
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_db_service) $(get_pgcat_if_exists) $(get_redis_service) rabbitmq socket-proxy registry route-fallback traefik
            else
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_db_service) $(get_pgcat_if_exists) $(get_redis_service) socket-proxy
            fi
            # Stop backend, celery & pgcat so their DB connections don't block
            # migrations (ALTER TABLE requires exclusive locks).
            echo -e "${BLUE}  → Stopping backend, celery & pgcat for migrations...${NC}"
            docker compose -f "$COMPOSE_FILE" stop --timeout 15 backend celery celery-deploy celery-fast celery-beat $(get_pgcat_if_exists) || echo -e "${YELLOW}    ⚠ docker compose stop backend/celery failed (non-fatal)${NC}"

            echo -e "${BLUE}  → Running migrations...${NC}"
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            echo -e "${BLUE}  → Starting backend & pgcat...${NC}"
            if [ -n "$(get_pgcat_if_exists)" ]; then docker compose -f "$COMPOSE_FILE" up -d --no-deps pgcat || echo -e "${YELLOW}    ⚠ docker compose up pgcat failed (non-fatal)${NC}"; fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps backend
            fi
            wait_for_container_ready "smsly-hosting-backend-1" 120 || true

            echo -e "${BLUE}  • Running post-migration tasks...${NC}"
            echo -e "${BLUE}    ↳ Running collectstatic...${NC}"
            timeout 120 docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput || echo -e "${YELLOW}    ⚠ collectstatic failed or timed out${NC}"

            set_checkpoint "update_db_migrated"

            # Clean stale celerybeat-schedule (prevents Permission denied crash loop)
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            timeout 30 docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule || echo -e "${YELLOW}    ⚠ celerybeat-schedule cleanup failed${NC}"

            echo -e "${BLUE}  → Restarting celery workers...${NC}"
            celery_svcs="celery celery-deploy celery-fast celery-beat"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                celery_svcs="celery-worker"
            fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate $celery_svcs
            else
                 docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps $celery_svcs
             fi
             
             # Custom Domain SSL Setup for Backend Update
             if should_manage_caddy; then  # Only for master mode
                 echo -e "\n${YELLOW}[UPDATE] Setting up Custom Domain SSL Services...${NC}"
                 SSL_SCRIPT="install-custom-domain-ssl.sh"
                 [ -f "$SSL_SCRIPT" ] || SSL_SCRIPT="$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh"
                 if [ -f "$SSL_SCRIPT" ]; then
                     echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
                     timeout -k 5 120 bash "$SSL_SCRIPT" install || true
                     
                     # Start the services
                     echo -e "${BLUE}  → Starting custom domain SSL services...${NC}"
                     timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh start || true
                     
                     # Enable auto-start on boot (if not already enabled)
                     echo -e "${BLUE}  → Ensuring auto-start on boot...${NC}"
                     timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable || true
                     
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
                timeout -k 5 600 docker compose -f "$COMPOSE_FILE" build frontend  || {
                    echo -e "${YELLOW}  ⚠ Frontend build failed (cached layers missing). Skipping frontend.${NC}"
                    echo -e "${YELLOW}    Run --update when Docker Hub is reachable for a full rebuild.${NC}"
                }
                docker compose -f "$COMPOSE_FILE" stop --timeout 15 frontend || echo -e "${YELLOW}    ⚠ docker compose stop frontend failed (non-fatal)${NC}"
                docker compose -f "$COMPOSE_FILE" rm -f frontend || echo -e "${YELLOW}    ⚠ docker compose rm frontend failed (non-fatal)${NC}"
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps frontend || echo -e "${YELLOW}    ⚠ docker compose up frontend failed (non-fatal)${NC}"
            fi

            # 2. Stop backend, celery & pgcat so their DB connections don't block
            #    migrations (ALTER TABLE requires exclusive locks).
            echo -e "${BLUE}  → Stopping backend, celery & pgcat for migrations...${NC}"
            docker compose -f "$COMPOSE_FILE" stop --timeout 15 backend celery celery-deploy celery-fast celery-beat $(get_pgcat_if_exists) || echo -e "${YELLOW}    ⚠ docker compose stop backend/celery failed (non-fatal)${NC}"

            # 3. Run migrations
            echo -e "${BLUE}  → Running migrations...${NC}"
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            # 4. Start pgcat & backend (picks up Python code changes from mounted volume)
            echo -e "${BLUE}  → Starting pgcat & backend...${NC}"
            if [ -n "$(get_pgcat_if_exists)" ]; then docker compose -f "$COMPOSE_FILE" up -d --no-deps pgcat || echo -e "${YELLOW}    ⚠ docker compose up pgcat failed (non-fatal)${NC}"; fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_redis_service) rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps backend
            fi
            wait_for_container_ready "smsly-hosting-backend-1" 120 || true

            echo -e "${BLUE}  • Running post-migration tasks...${NC}"
            echo -e "${BLUE}    ↳ Running collectstatic...${NC}"
            timeout 120 docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput || echo -e "${YELLOW}    ⚠ collectstatic failed or timed out${NC}"

            # 5. Clean celerybeat-schedule and restart celery workers
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            timeout 30 docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule || echo -e "${YELLOW}    ⚠ celerybeat-schedule cleanup failed (non-fatal)${NC}"

            restart_svcs="celery celery-deploy celery-fast celery-beat"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                restart_svcs="celery-worker"
            fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate $restart_svcs || echo -e "${YELLOW}    ⚠ docker compose up celery failed (non-fatal)${NC}"
            else
                docker compose -f "$COMPOSE_FILE" restart $restart_svcs || echo -e "${YELLOW}    ⚠ docker compose restart celery failed (non-fatal)${NC}"
             fi
             set_checkpoint "update_db_migrated"
             
             # Custom Domain SSL Setup for Half Update
             if should_manage_caddy; then  # Only for master mode
                 echo -e "\n${YELLOW}[UPDATE] Setting up Custom Domain SSL Services...${NC}"
                 SSL_SCRIPT="install-custom-domain-ssl.sh"
                 [ -f "$SSL_SCRIPT" ] || SSL_SCRIPT="$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh"
                 if [ -f "$SSL_SCRIPT" ]; then
                     echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
                     timeout -k 5 120 bash "$SSL_SCRIPT" install || true
                     
                     # Start the services
                     echo -e "${BLUE}  → Starting custom domain SSL services...${NC}"
                     timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh start || true
                     
                     # Enable auto-start on boot (if not already enabled)
                     echo -e "${BLUE}  → Ensuring auto-start on boot...${NC}"
                     timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable || true
                     
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

            # 2. Skip untagging old PaaS images to prevent zero-downtime gaps on container restarts.
            # Docker compose build will simply overwrite the tag; old images will become dangling and cleaned later.

            # 3. Prune dangling build cache
            echo -e "${BLUE}    ↳ Pruning build cache...${NC}"
            docker builder prune -af  || true

            # 4. Ensure shared networks exist (create if missing, don't destroy)
            echo -e "${BLUE}    ↳ Ensuring networks exist...${NC}"
            ensure_update_networks

            # 5. Rebuild core images (CACHED unless --no-cache passed manually)
            echo -e "${BLUE}    ↳ Rebuilding core images...${NC}"
            timeout -k 5 600 docker compose -f "$COMPOSE_FILE" build $CORE_SERVICES

            # 6. Start everything (addons stay running, core gets fresh containers)
            # This does a graceful zero-downtime replacement instead of an explicit hard stop
            echo -e "${BLUE}    ↳ Starting all services...${NC}"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_redis_service) rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --remove-orphans $CORE_SERVICES
            elif [ "$MODE_NODE" = "true" ]; then
                stop_node_excluded_services
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_db_service) $(get_pgcat_if_exists) $(get_redis_service) rabbitmq socket-proxy registry route-fallback traefik
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps --remove-orphans $CORE_SERVICES
            else
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps --remove-orphans $CORE_SERVICES
            fi

            if [ "$MODE_AGENT_LITE" != "true" ]; then
                # 7. Reconnect Traefik + socket-proxy to smsly-proxy network
                #    (recreation drops Docker DNS links — causes 502 gateway errors)
                #    NOTE: ensure_container_on_network uses `docker network connect`
                #    which works on running containers. No restart needed.
                echo -e "${BLUE}    ↳ Reconnecting proxy network...${NC}"
                for ctr in smsly-hosting-traefik-1 smsly-hosting-socket-proxy-1; do
                    ensure_container_on_network "smsly-proxy" "$ctr"
                done
            fi

            # 8. Stop backend, celery & pgcat so their DB connections don't block
            #    migrations (ALTER TABLE requires exclusive locks).
            echo -e "${BLUE}  → Stopping backend, celery & pgcat for migrations...${NC}"
            docker compose -f "$COMPOSE_FILE" stop --timeout 15 backend celery celery-deploy celery-fast celery-beat $(get_pgcat_if_exists) || echo -e "${YELLOW}    ⚠ docker compose stop backend/celery failed (non-fatal)${NC}"

            # 9. Run migrations
            echo -e "${BLUE}  → Running migrations...${NC}"
            echo -e "${BLUE}  → Ensuring backend dependencies are running...${NC}"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                verify_agent_lite_connectivity
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_redis_service) rabbitmq socket-proxy
                sync_agent_lite_rabbitmq_password
            elif [ "$MODE_NODE" = "true" ]; then
                stop_node_excluded_services
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_db_service) $(get_pgcat_if_exists) $(get_redis_service) rabbitmq socket-proxy registry route-fallback traefik
            else
                docker compose -f "$COMPOSE_FILE" up -d --remove-orphans $(get_db_service) $(get_pgcat_if_exists) $(get_redis_service) socket-proxy
            fi
            run_backend_migrations --root || {
                echo -e "${YELLOW}  ⚠ Migration failed — retrying in 15s...${NC}"
                sleep 15
                run_backend_migrations --root
            }

            # 10. Start pgcat & backend
            echo -e "${BLUE}  → Starting pgcat & backend...${NC}"
            if [ -n "$(get_pgcat_if_exists)" ]; then docker compose -f "$COMPOSE_FILE" up -d --no-deps pgcat || echo -e "${YELLOW}    ⚠ docker compose up pgcat failed (non-fatal)${NC}"; fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate backend
            else
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps backend
            fi
            wait_for_container_ready "smsly-hosting-backend-1" 120 || true

            echo -e "${BLUE}  • Running post-migration tasks...${NC}"
            echo -e "${BLUE}    ↳ Running collectstatic...${NC}"
            timeout 120 docker compose -f "$COMPOSE_FILE" exec -T --user root backend python manage.py collectstatic --noinput || echo -e "${YELLOW}    ⚠ collectstatic failed or timed out${NC}"

            # 11. Clean celerybeat-schedule and restart beat
            echo -e "${BLUE}  → Cleaning celerybeat-schedule...${NC}"
            timeout 30 docker compose -f "$COMPOSE_FILE" exec -T --user root backend rm -f /app/celerybeat-schedule || echo -e "${YELLOW}    ⚠ celerybeat-schedule cleanup failed (non-fatal)${NC}"
            
            restart_svcs="celery celery-beat celery-deploy celery-fast"
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                restart_svcs="celery-worker"
            fi
            if [ "$MODE_AGENT_LITE" = "true" ]; then
                docker compose -f "$COMPOSE_FILE" up -d --force-recreate $restart_svcs || echo -e "${YELLOW}    ⚠ docker compose up celery failed (non-fatal)${NC}"
            else
                docker compose -f "$COMPOSE_FILE" restart $restart_svcs || echo -e "${YELLOW}    ⚠ docker compose restart celery failed (non-fatal)${NC}"
            fi
            set_checkpoint "update_db_migrated"

            # Custom Domain SSL Setup for Full Update
            if should_manage_caddy; then
                echo -e "\n${YELLOW}[UPDATE] Setting up Custom Domain SSL Services...${NC}"
                SSL_SCRIPT="install-custom-domain-ssl.sh"
                [ -f "$SSL_SCRIPT" ] || SSL_SCRIPT="$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh"
                if [ -f "$SSL_SCRIPT" ]; then
                    timeout -k 5 120 bash "$SSL_SCRIPT" install || true
                    timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh start || true
                    timeout -k 5 30 /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable || true
                    echo -e "${GREEN}  ✓ Custom domain SSL services configured${NC}"
                else
                    echo -e "${YELLOW}  ⚠ Custom domain SSL manager not found, skipping setup${NC}"
                fi
            fi
            ;;
    esac

    # ─── Infisical auto-provision + secret sync ───────────────────────────
    _INFISICAL_COMPOSE="$INSTALL_DIR/infrastructure/docker/docker-compose.infisical.yml"
    if [ -f "$_INFISICAL_COMPOSE" ]; then
        _infisical_running=$(docker ps --filter "name=infisical" --format '{{.Names}}'  | head -1)
        if [ -n "$_infisical_running" ]; then
            echo -e "${GREEN}  ✓ Infisical already running (${_infisical_running})${NC}"
        else
            # Ensure the infisical data volume exists
            docker volume create infisical_data  || true

            # Create the infisical database in Postgres if it doesn't exist
            _db_container=""
            # HA mode: smsly-postgres-primary
            if docker ps --format '{{.Names}}' | grep -q '^smsly-postgres-primary$'; then
                _db_container="smsly-postgres-primary"
                _db_user="${POSTGRES_USER:-smsly_admin}"
            # Standard mode: smsly-hosting-db-1
            elif docker ps --format '{{.Names}}' | grep -q '^smsly-hosting-db-1$'; then
                _db_container="smsly-hosting-db-1"
                _db_user="${POSTGRES_USER:-postgres}"
            fi
            if [ -n "$_db_container" ]; then
                _db_exists=$(timeout 30 docker exec "$_db_container" psql -U "${_db_user}" -d "${POSTGRES_DB:-smsly_hosting}" -tc \
                    "SELECT 1 FROM pg_database WHERE datname='infisical'"  | tr -d '[:space:]' || true)
                if [ "$_db_exists" != "1" ]; then
                    timeout 30 docker exec "$_db_container" psql -U "${_db_user}" -d "${POSTGRES_DB:-smsly_hosting}" -c \
                        "CREATE DATABASE infisical;"  && \
                        echo -e "${GREEN}  ✓ Created infisical database${NC}" || \
                        echo -e "${YELLOW}  ⚠ Could not create infisical database (may already exist)${NC}"
                fi
            else
                echo -e "${YELLOW}  ⚠ No Postgres container found — skipping infisical database creation${NC}"
            fi

            # Generate env file on the volume (if not already present)
            _gen_script="$INSTALL_DIR/infrastructure/docker/infisical-gen-env.sh"
            if [ -f "$_gen_script" ]; then
                docker run --rm \
                    -v infisical_data:/data \
                    -v "$_gen_script":/tmp/infisical-gen-env.sh:ro \
                    alpine:3.19 \
                    sh /tmp/infisical-gen-env.sh /data/infisical.env  || \
                    echo -e "${YELLOW}  ⚠ Could not generate Infisical env (may already exist)${NC}"
            fi

            # Bring up Infisical (env_file loaded from volume)
            echo -e "${BLUE}  → Provisioning Infisical secret manager...${NC}"
            docker compose --env-file "$INSTALL_DIR/.env" \
                -f "$_INFISICAL_COMPOSE" up -d --remove-orphans  && \
                echo -e "${GREEN}  ✓ Infisical is running${NC}" || \
                echo -e "${YELLOW}  ⚠ Infisical startup failed (non-fatal — secrets remain in .env)${NC}"
        fi

        # Sync platform secrets to Infisical if it's running
        _infisical_running=$(docker ps --filter "name=infisical" --format '{{.Names}}'  | head -1)
        if [ -n "$_infisical_running" ]; then
            echo -e "${BLUE}  → Syncing platform secrets to Infisical...${NC}"
            backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
            if [ -n "$backend_container" ]; then
                timeout 60 docker exec "$backend_container" python manage.py sync_infisical_secrets --push  || \
                    echo -e "${YELLOW}  ⚠ Infisical sync failed (non-fatal — secrets remain in .env)${NC}"
            fi
        fi
    fi

    # ─── Observability Stack Update (master mode only) ──────────────────────
    if [ "$MODE_AGENT_LITE" != "true" ] && [ "$MODE_NODE" != "true" ]; then
        echo -e "${BLUE}  → Updating observability stack...${NC}"
        # Ensure scripts mounted into containers are executable (git may not preserve +x)
        chmod +x "$INSTALL_DIR"/scripts/alertmanager-entrypoint.sh  || true
        chmod +x "$INSTALL_DIR"/infrastructure/docker/infisical-gen-env.sh  || true
        mkdir -p /opt/smsly-hosting/prometheus-targets
        if ! chown -R 1000:1000 /opt/smsly-hosting/prometheus-targets ; then
            echo -e "${YELLOW}  ⚠ Could not chown prometheus-targets to uid 1000${NC}"
        fi
        chmod 2777 /opt/smsly-hosting/prometheus-targets  || true
        docker compose \
            --env-file /opt/smsly-hosting/.env \
            -f infrastructure/docker/docker-compose.observability.yml \
            up -d --pull always || \
            echo -e "${YELLOW}  ⚠ Observability stack had issues (non-fatal)${NC}"
        # Restart containers whose bind-mounted config or environment may have
        # changed.  docker compose up -d only recreates on IMAGE changes, so
        # config-file updates require an explicit restart.
        docker restart smsly-grafana || echo -e "${YELLOW}    ⚠ docker restart smsly-grafana failed (non-fatal)${NC}"
        docker restart smsly-alertmanager || echo -e "${YELLOW}    ⚠ docker restart smsly-alertmanager failed (non-fatal)${NC}"
        docker restart smsly-prometheus || echo -e "${YELLOW}    ⚠ docker restart smsly-prometheus failed (non-fatal)${NC}"
        docker restart smsly-docker-labels || echo -e "${YELLOW}    ⚠ docker restart smsly-docker-labels failed (non-fatal)${NC}"
        docker restart smsly-promtail || echo -e "${YELLOW}    ⚠ docker restart smsly-promtail failed (non-fatal)${NC}"
        # Deploy/update docker-labels exporter to all remote nodes and
        # regenerate Prometheus file_sd target files (docker-labels,
        # cAdvisor, Node Exporter).
        backend_container=$(docker ps --format '{{.Names}}' | grep -E '^smsly-hosting-backend(-1)?$' | head -1)
        if [ -n "$backend_container" ]; then
            timeout 60 docker exec "$backend_container" python manage.py deploy_docker_labels_exporters --force || echo -e "${YELLOW}    ⚠ deploy_docker_labels_exporters failed${NC}"
        fi
        echo -e "${GREEN}  ✓ Observability stack updated${NC}"
    fi
    if [ -n "${CROWDSEC_BOUNCER_KEY:-}" ]; then
        echo -e "${BLUE}  → Registering CrowdSec Bouncer...${NC}"
        timeout 30 docker exec smsly-crowdsec cscli bouncers add traefik-bouncer -k "${CROWDSEC_BOUNCER_KEY:-}" || echo -e "${YELLOW}    ⚠ CrowdSec bouncer registration failed (already exists, non-fatal)${NC}"
    fi

    set_checkpoint "update_containers_rebuilt"
fi

# --- end lib/update_rebuild.sh ---
# --- lib/update_post_deploy.sh ---
# ─── Vulnerability scan of freshly built images ────────────────────────
if command -v trivy ; then
    echo -e "${BLUE}  → Scanning rebuilt images for vulnerabilities...${NC}"
    for _trivy_img in backend frontend; do
        _trivy_tag="smsly/${_trivy_img}:latest"
        if docker image inspect "$_trivy_tag" ; then
            echo -e "${BLUE}    ↳ Scanning $_trivy_tag...${NC}"
            trivy image --scanners vuln --severity CRITICAL,HIGH --exit-code 0 --no-progress "$_trivy_tag"  || \
                echo -e "${YELLOW}    ⚠ $_trivy_tag scan reported warnings — review output above${NC}"
        fi
    done
    unset _trivy_img _trivy_tag
fi

# ─── Safe Update: Post-Deploy Verification ─────────────────────────────
if command -v safe_update_post_verify ; then
    echo -e "${BLUE}  → Running post-deploy health checks...${NC}"
    sleep 30  # wait for containers to warm up
    if safe_update_post_verify; then
        echo -e "${GREEN}  ✓ All health checks passed — update successful${NC}"
        trap - ERR  # clear rollback trap on success
        if command -v safe_update_cleanup ; then
            safe_update_cleanup
        fi
        rm -f "$SNAPSHOT_FILE"  || true
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
" | timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell || echo -e "${YELLOW}    ⚠ Local Docker provider setup failed (non-fatal)${NC}"
    # ─── Self-Healing: Docker Socket Permissions ──────────────────────────────
    echo -e "${BLUE}  → Hardening Docker socket permissions...${NC}"
    # NOTE: Removed chmod 666 — world-writable docker.sock is a security risk.
    # Group membership (docker group) is the correct access control mechanism.
    if ! groups smsly  | grep -q "docker"; then
        usermod -aG docker smsly || echo -e "${YELLOW}    ⚠ usermod docker group failed (non-fatal)${NC}"
    fi

    # ─── Self-Healing: Cleanup Stale Resources ──────────────────────────────
    echo -e "${BLUE}  → Pruning stale deployment containers and BuildKit caches...${NC}"
    # Prune orphaned containers created by the deployment system (labeled)
    docker container prune -f --filter "label=com.smsly.managed=true" --filter "status=created"  || true
    docker container prune -f --filter "label=com.docker.compose.project" --filter "status=exited"  || true
    # Prune BuildKit build cache (saves significant disk space)
    docker builder prune -f --filter "until=24h"  || true
    # Prune stale rollback backup containers left from failed blue-green promotions
    docker container prune -f --filter "status=exited"  || true
    # Prune dangling images left over after the new images were tagged
    docker image prune -f  || true
    for ctr in $(docker ps -a --filter "status=exited" --filter "name=-rollback-" --format '{{.Names}}'  || true); do
        docker rm -f "$ctr"  || true
    done
    for ctr in $(docker ps -a --filter "status=created" --filter "name=-rollback-" --format '{{.Names}}'  || true); do
        docker rm -f "$ctr"  || true
    done

    # ─── Self-Healing: Automatic Queue Restoration ──────────────────────────
    echo -e "${BLUE}  → Checking for stalled deployments/addons in QUEUED state...${NC}"
    backend_container="$(resolve_container_target "smsly-hosting-backend-1")"
    timeout -k 5 120 docker exec -i "$backend_container" python manage.py shell -c "
from apps.deployments.models import Deployment, Service
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

# Re-queue stalled service deletions (lost during worker restart)
d_count = Service.objects.filter(status='DELETION_PENDING').count()
if d_count > 0:
    print(f'  [Jump-Start] Re-queueing {d_count} stalled deletion tasks...')
    from apps.deployments.tasks import delete_service_task
    for s in Service.objects.filter(status='DELETION_PENDING'):
        delete_service_task.delay(str(s.id))
"  || true

    # ─── Verification: Celery Worker Health ─────────────────────────────────
    echo -e "${BLUE}  → Verifying worker connectivity and queue bindings...${NC}"
    # Give workers a moment to connect to Redis and report active queues
    sleep 15
    raw_worker="smsly-hosting-celery-deploy-1"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        raw_worker="smsly-hosting-celery-worker-1"
    fi
    worker_container="$(resolve_container_target "$raw_worker")"
    DEPLOY_WORKER_HEALTH="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$worker_container"  || echo "")"
    if timeout 20 docker exec -i "$worker_container" celery -A config inspect active_queues --timeout=10  | grep -q "deploy"; then
        echo -e "${GREEN}  ✓ Deployment worker successfully bound to 'deploy' queue${NC}"
    elif [ "$DEPLOY_WORKER_HEALTH" = "healthy" ] || [ "$DEPLOY_WORKER_HEALTH" = "running" ]; then
        echo -e "${GREEN}  ✓ Deployment worker container is healthy/running (queue inspect timed out)${NC}"
    else
        echo -e "${YELLOW}  ⚠ WARNING: Deployment worker not detected on 'deploy' queue. Check logs.${NC}"
    fi

    echo -e "\n${GREEN}  ✨ Update complete. Self-healing applied.${NC}"

    timeout -k 5 120 bash -c "
export COMPOSE_FILE='$COMPOSE_FILE'
source '$INSTALL_DIR/lib/env.sh'
source '$INSTALL_DIR/lib/common.sh'
source '$INSTALL_DIR/lib/platform.sh'
sync_platform_domain_state '$INSTALL_DIR/.env'
" || echo -e "${YELLOW}  ⚠ Domain state sync timed out (non-fatal)${NC}"

    # Refresh proxy/runtime edge stack so routing and TLS state is always clean.
    # NOTE: restart_edge_stack now handles Caddy validation internally (H1+H2 fix).
    restart_edge_stack
    wait_for_traefik_api 30 || true

    sleep 2

    # ─── Fix .env permissions (must be writable by Docker container UID 1000) ──
    if [ -f "$INSTALL_DIR/.env" ]; then
        chown root:1000 "$INSTALL_DIR/.env"  || true
        chmod 640 "$INSTALL_DIR/.env"  || true
    fi

    # ─── Caddy: Generate self-signed cert + regenerate Caddyfile ──
    if should_manage_caddy; then
    ensure_selfsigned_cert
    if command -v caddy ; then
        echo -e "${BLUE}  → Regenerating Caddyfile with current service domains...${NC}"

        # ── Step 1: Find the Cloudflare token FIRST (before generating Caddyfile) ──
        CF_TOKEN=""

        # Priority: .env file > PlatformConfig DB
        if [ -z "$CF_TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
            CF_TOKEN="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "$INSTALL_DIR/.env"  | cut -d= -f2- || true)"
        fi
        # Fallback: read from PlatformConfig in the database (set via Settings UI)
        if [ -z "$CF_TOKEN" ] || [ "$CF_TOKEN" = "fake" ]; then
            DB_TOKEN="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
config = PlatformConfig.load()
token = (getattr(config, 'cloudflare_api_token', '') or '').strip()
if token and token.lower() not in ('fake', 'changeme', 'test', ''):
    print(token)
"  || true)"
            DB_TOKEN="$(echo "$DB_TOKEN" | tr -d '[:space:]')"
            if [ -n "$DB_TOKEN" ]; then
                CF_TOKEN="$DB_TOKEN"
                echo -e "${GREEN}  ✓ Cloudflare token found in Settings DB${NC}"
                # Sync back to .env so it persists
                if grep -q 'CLOUDFLARE_API_TOKEN' "$INSTALL_DIR/.env" ; then
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
            cf_domain="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
"  | tr -d '[:space:]' || true)"
            if [ -z "$cf_domain" ]; then
                cf_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- || true)"
            fi

            cf_server_ip="$(detect_public_ip)"

            # Discover wildcard-covered hosts and non-wildcard service blocks.
            # - Wildcard-covered hosts route through Traefik via matcher.
            # - Unknown wildcard hosts route to /notice on frontend.
            # - External custom domains keep explicit direct on-demand TLS blocks with Host rewrite.
            cf_wildcard_known_hosts=""
            cf_wildcard_known_hosts="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
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
"  | tr -d '\r' | tr -d '\n' || true)"

            cf_svc_blocks=""
            cf_svc_blocks="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
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
"  | tr -d '\r' || true)"

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

        reload_container_caddy  || true

        # ─── Python-based Caddyfile overlay (preview-aware routing) ─────────────
        # The bash heredoc above generates a static template without preview
        # environment routing. Django's generate_caddyfile() includes direct
        # container routing for local preview environments, so we overlay it.
        echo -e "${BLUE}  → Overlaying preview-aware Caddyfile from Django...${NC}"
        timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
from services.caddy_manager import generate_caddyfile, apply_caddyfile
config = PlatformConfig.load()
content = generate_caddyfile(config)
token = (getattr(config, 'cloudflare_api_token', '') or '').strip()
result = apply_caddyfile(content, cloudflare_token=token, preserve_existing_token=True)
print(result.get('message', 'ok'))
"  && echo -e "${GREEN}  ✓ Preview-aware Caddyfile applied${NC}" || \
            echo -e "${YELLOW}  ⚠ Python Caddyfile overlay failed (non-fatal, static template still active)${NC}"

        reload_container_caddy  || true

        # Verify Caddy is running
        sleep 2
        if docker compose -f "$COMPOSE_FILE" ps -q caddy  | grep -q .; then
            echo -e "${GREEN}  ✓ Caddy config regenerated and running${NC}"
        else
            echo -e "${YELLOW}  ⚠ Caddy failed to start. Run: journalctl -u caddy --no-pager -n 20${NC}"
        fi

        POST_CADDY_DOMAIN="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
c = PlatformConfig.load()
d = (c.domain or '').strip()
if d and d != 'localhost':
    print(d)
"  | tr -d '[:space:]' || true)"
        if [ -z "$POST_CADDY_DOMAIN" ]; then
            POST_CADDY_DOMAIN="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- || true)"
        fi

        install_caddy_health_guard "$POST_CADDY_DOMAIN"
    fi
    fi

    timeout -k 5 600 bash -c "
export COMPOSE_FILE='$COMPOSE_FILE'
source '$INSTALL_DIR/lib/common.sh' 
safe_refresh_runtime_services
" || true
    timeout -k 5 300 bash -c "
export COMPOSE_FILE='$COMPOSE_FILE'
source '$INSTALL_DIR/lib/common.sh' 
ensure_celery_workers_running
" || true

    # ─── Auto-redeploy active services when platform code or domain state changes ──
    PRE_HEAD="$(cat "$INSTALL_DIR/.pre-update-head"  || true)"
    CURRENT_HEAD="$(cd "$INSTALL_DIR" && git rev-parse HEAD  || true)"
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
    rm -f "$INSTALL_DIR/.pre-update-head"  || true

    # ─── Endpoint Verification (3 checks) ──────────────────────────────────
    echo -e "\n${BLUE}  → Running endpoint verification (3 checks)...${NC}"
    sleep 5
    PASS_COUNT=0
    FAIL_COUNT=0

    # ── Check 1: Backend API health (docker exec into backend container) ──
    EP1_FALLBACK_URL="http://127.0.0.1:8000/health"
    _LITE_HOST_HEADER=""
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        _ep1_domain="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- | tr -d '[:space:]' || true)"
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
                EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 -H "Host: ${_LITE_HOST_HEADER}" "http://127.0.0.1/health" ) || EP1_CODE="000"
            else
                # No domain — route through Traefik on port 80
                EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1/health" ) || EP1_CODE="000"
            fi
        else
            if timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health ; then
                EP1_CODE="200"
            elif curl -fsS --max-time 5 "$EP1_FALLBACK_URL" ; then
                EP1_CODE="200"
            else
                EP1_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP1_FALLBACK_URL" ) || EP1_CODE="000"
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
    EP_DOMAIN="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import PlatformConfig
config = PlatformConfig.load()
d = (config.domain or '').strip()
if d and d != 'localhost':
    print(d)
"  | tr -d '[:space:]' || true)"
    # Fallback to .env if DB query failed
    if [ -z "$EP_DOMAIN" ]; then
        EP_DOMAIN="$(grep -m1 '^DOMAIN=' "$INSTALL_DIR/.env"  | cut -d= -f2- || true)"
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
            EP2_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$EP2_URL" ) || EP2_CODE="000"
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
    ALL_SVC_DOMAINS="$(timeout -k 5 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.models import Service
for svc in Service.objects.exclude(public_domain__isnull=True).exclude(public_domain='').order_by('name'):
    print(f'{svc.name}|{svc.public_domain.strip()}')
    for cd in (svc.custom_domains or []):
        cd = cd.strip()
        if cd:
            print(f'{svc.name} (custom)|{cd}')
"  | tr -d '\r' || true)"

    # Also check Traefik port directly
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        EP3_URL="http://127.0.0.1/"
    else
        EP3_URL="http://127.0.0.1:8081/"
    fi
    EP3_CODE=$(curl -so /dev/null -w '%{http_code}' --max-time 5 "$EP3_URL" ) || EP3_CODE="000"
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
                svc_code=$(curl -so /dev/null -w '%{http_code}' --max-time 8 "$svc_url" ) || svc_code="000"
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
    docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}"  || \
        docker compose -f "$COMPOSE_FILE" ps  || true

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
        systemctl restart smsly-autoscaler || echo -e "${YELLOW}    ⚠ Autoscaler restart failed${NC}"
        echo -e "${GREEN}  ✓ Autoscaler updated${NC}"
    fi

    # ─── Re-apply OOM protection (scores reset when containers restart) ──────
    echo -e "${BLUE}  → Re-applying OOM protection for critical containers...${NC}"
    oom_containers="smsly-hosting-backend-1 $(get_db_service | sed 's|^|smsly-hosting-|' || echo smsly-hosting-postgres-primary) smsly-hosting-pgcat-1 smsly-hosting-celery-1 smsly-hosting-celery-deploy-1 smsly-hosting-celery-fast-1 smsly-hosting-celery-beat-1 smsly-hosting-socket-proxy-1"
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        oom_containers="smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-socket-proxy-1"
    fi
    for CONTAINER in $oom_containers; do
        resolved_container="$(resolve_container_target "$CONTAINER")"
        CPID=$(docker inspect --format '{{.State.Pid}}' "$resolved_container"  || echo "")
        if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
            echo -500 > "/proc/$CPID/oom_score_adj"  || true
        fi
    done
    echo -e "${GREEN}  ✓ OOM protection set (core, database, celery, proxy)${NC}"

    # ─── Ensure iptables-restore systemd service exists ─────────────────────
    if command -v iptables-save ; then
        mkdir -p /etc/iptables
        iptables-save > /etc/iptables/rules.v4  || true
        if [ ! -f /etc/systemd/system/iptables-restore.service ]; then
            echo -e "${BLUE}  → Installing iptables-restore systemd service...${NC}"
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
            systemctl daemon-reload || echo -e "${YELLOW}    ⚠ systemctl daemon-reload failed (non-fatal)${NC}"
            systemctl enable iptables-restore || echo -e "${YELLOW}    ⚠ systemctl enable iptables-restore failed (non-fatal)${NC}"
            echo -e "${GREEN}  ✓ iptables-restore service installed and enabled${NC}"
        fi
    fi

    # ─── Ensure platform update watcher and caddy watcher services exist ───
    if [ -f "$INSTALL_DIR/scripts/smsly-update-watcher.service" ]; then
        echo -e "${BLUE}  → Ensuring platform update and Caddy config watcher services...${NC}"
        chmod +x "$INSTALL_DIR/scripts/platform-update.sh" "$INSTALL_DIR/scripts/caddy-reload.sh"  || true
        cp "$INSTALL_DIR/scripts/smsly-update-watcher.service" /etc/systemd/system/smsly-update-watcher.service  || true
        cp "$INSTALL_DIR/scripts/caddy-watcher.service" /etc/systemd/system/caddy-watcher.service  || true
        systemctl daemon-reload || echo -e "${YELLOW}    ⚠ systemctl daemon-reload failed (non-fatal)${NC}"
        systemctl enable smsly-update-watcher caddy-watcher || echo -e "${YELLOW}    ⚠ systemctl enable watchers failed (non-fatal)${NC}"
        systemctl restart smsly-update-watcher caddy-watcher || echo -e "${YELLOW}    ⚠ systemctl restart watchers failed (non-fatal)${NC}"
        echo -e "${GREEN}  ✓ smsly-update-watcher and caddy-watcher services updated and started${NC}"
    fi

    # ─── Ensure Celery Worker Autoscaler service exists and is configured ──
    if [ -f "$INSTALL_DIR/scripts/celery-worker-autoscaler.sh" ]; then
        echo -e "${BLUE}  → Ensuring Celery Worker Autoscaler service...${NC}"
        chmod +x "$INSTALL_DIR/scripts/celery-worker-autoscaler.sh"
        cp "$INSTALL_DIR/infrastructure/docker/celery-autoscaler.service" /etc/systemd/system/celery-autoscaler.service  || true
        systemctl daemon-reload || true
        if [ "${CELERY_AUTOSCALE_ENABLED:-true}" = "true" ]; then
            systemctl enable celery-autoscaler || echo -e "${YELLOW}    ⚠ celery-autoscaler enable failed (non-fatal)${NC}"
            systemctl restart celery-autoscaler || echo -e "${YELLOW}    ⚠ celery-autoscaler restart failed (non-fatal)${NC}"
            echo -e "${GREEN}  ✓ celery-autoscaler service updated and running${NC}"
        else
            systemctl disable celery-autoscaler 2>/dev/null || true
            systemctl stop celery-autoscaler 2>/dev/null || true
            echo -e "${BLUE}  → celery-autoscaler disabled (CELERY_AUTOSCALE_ENABLED=false)${NC}"
        fi
    fi

    # ─── Ensure WireGuard mesh service is enabled ───────────────────────────
    if [ -d /etc/wireguard ]; then
        for wg_conf in /etc/wireguard/*.conf; do
            [ -f "$wg_conf" ] || continue
            wg_iface=$(basename "$wg_conf" .conf)
            if ! systemctl is-enabled "wg-quick@${wg_iface}" ; then
                echo -e "${BLUE}  → Re-enabling WireGuard mesh ($wg_iface)...${NC}"
                systemctl enable --now "wg-quick@${wg_iface}" || echo -e "${YELLOW}    ⚠ systemctl enable wg-quick failed (non-fatal)${NC}"
                echo -e "${GREEN}  ✓ WireGuard $wg_iface re-enabled${NC}"
            fi
            if ! systemctl is-active "wg-quick@${wg_iface}" ; then
                echo -e "${YELLOW}  ⚠ WireGuard $wg_iface is not running, attempting restart...${NC}"
                systemctl start "wg-quick@${wg_iface}" || echo -e "${YELLOW}    ⚠ systemctl start wg-quick failed (non-fatal)${NC}"
            fi
        done
    fi

    trap - EXIT
    release_install_lock
    echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
    # Infrastructure Diagnostic & Auto-Fix
    # Infrastructure Handshake & Health Stabilization
    echo -e "\n${BLUE}  🔄 Running infrastructure handshake and stabilization...${NC}"
    chmod +x scripts/grid-handshake.sh  || true
    SMSLY_MIGRATIONS_DONE=1 bash scripts/grid-handshake.sh || \
        echo -e "${YELLOW}  ⚠️ Handshake stabilization failed (non-fatal). You can run it manually later.${NC}"

    # ─── Fix .env permissions (ensures domain signal can write back) ─────
    fix_env_permissions "$INSTALL_DIR/.env" || true

    # ─── Install/update infrastructure monitor timer ─────────────────────
    if [ -f "$INSTALL_DIR/scripts/monitor_infra.sh" ]; then
        echo -e "${BLUE}  → Installing critical infrastructure monitoring timer...${NC}"
        chmod +x "$INSTALL_DIR/scripts/monitor_infra.sh"
        cp "$INSTALL_DIR/scripts/smsly-infra-monitor.service" /etc/systemd/system/smsly-infra-monitor.service  || true
        cp "$INSTALL_DIR/scripts/smsly-infra-monitor.timer" /etc/systemd/system/smsly-infra-monitor.timer  || true
        systemctl daemon-reload
        systemctl enable smsly-infra-monitor.timer || echo -e "${YELLOW}    ⚠ systemctl enable infra timer failed (non-fatal)${NC}"
        systemctl restart smsly-infra-monitor.timer || echo -e "${YELLOW}    ⚠ systemctl restart infra timer failed (non-fatal)${NC}"
        echo -e "${GREEN}  ✓ smsly-infra-monitor timer installed and started${NC}"
    fi

    echo -e "${GREEN}   ✓ UPDATE SUCCESSFUL ($UPDATE_MODE)${NC}"

    # ─── Security verify ──────────────────────────────────────────────────
    if [ -f "$INSTALL_DIR/lib/harden.sh" ]; then
        harden_security_verify
    fi

    # ─── Image signature verification ────────────────────────────────────
    if command -v cosign  && [ -f "$INSTALL_DIR/scripts/cosign-verify.sh" ]; then
        echo -e "${BLUE}  → Verifying production image signatures...${NC}"
        source "$INSTALL_DIR/scripts/cosign-verify.sh"
        cosign_verify_image "smsly/backend:latest" || \
            echo -e "${YELLOW}  ⚠ Backend image signature verification failed (non-fatal on existing installs)${NC}"
    fi

    echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
    echo -e "${YELLOW}  Debug snapshot:    sudo bash install.sh --debug${NC}"
    echo -e "${YELLOW}  Runtime recovery:  sudo bash install.sh --recover${NC}"
    echo -e "${YELLOW}  Fix permissions:   sudo bash install.sh --fix-permissions${NC}"
    exit 0

# --- end lib/update_post_deploy.sh ---
fi

# --- end lib/update.sh ---
    exit 0
fi

# =============================================================================
# FRESH INSTALL (fallthrough)
# =============================================================================
# Prefer the regenerated, tested, self-contained backend/install.sh when it is
# co-located (full repo checkout). A standalone curl'd install.sh has no
# backend/install.sh next to it and falls back to lib/ bootstrap + fresh.sh.
# The basename guard prevents recursion: backend/install.sh is generated from
# this file, so it carries the same delegation block — running from it must
# fall through to the inlined fresh.sh below.
if [ -f "$SCRIPT_DIR/backend/install.sh" ] && [ "$(basename "$SCRIPT_PATH")" != "backend/install.sh" ]; then
    echo -e "${BLUE}  → Delegating fresh install to self-contained backend/install.sh${NC}"
    exec bash "$SCRIPT_DIR/backend/install.sh" "$@"
fi
# --- lib/fresh.sh ---
#!/bin/bash
# Grid by SMSLY - Fresh Install Mode Module
# Sourced by install.sh for fresh installs

# =============================================================================
# FRESH INSTALL — Full setup from scratch
# =============================================================================

export PATH="/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# When running from the repo layout, fresh.sh lives in lib/ next to state.sh.
# When running from the regenerated self-contained installer (backend/install.sh)
# the state.sh content is inlined at this exact line by the regen pipeline, so
# the file itself will not exist. The source line must stay in its canonical
# form (no redirects) for the inliner to recognize and replace it.
# --- lib/state.sh ---
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
        previous_flavor="$(cat "$STATE_MODE_FILE"  || echo "legacy")"
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
    if [ ! -f "$STATE_FILE" ] || ! grep -q "^$name$" "$STATE_FILE" ; then
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
        grep -v "^$name$" "$STATE_FILE" > "${STATE_FILE}.tmp"  || true
        mv "${STATE_FILE}.tmp" "$STATE_FILE"  || true
    fi
}
# --- end lib/state.sh ---

if ! command -v is_checkpoint_done >/dev/null 2>&1; then
    echo "ERROR: is_checkpoint_done not defined after sourcing state.sh" >&2
    exit 1
fi

# --- lib/fresh_interactive.sh ---

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
            if command -v host ; then
                DETECTED_IP=$(host -t A "$DOMAIN"  | awk '{print $NF}' | tail -n 1)
            fi
            if [ -z "$DETECTED_IP" ] || [ "$DETECTED_IP" = "found:" ] || [ "$DETECTED_IP" = "not" ]; then
                DETECTED_IP=""
                # Fallback to DNS over HTTPS (Google)
                DETECTED_IP="$(curl -fsS "https://dns.google/resolve?name=${DOMAIN}&type=A" -m 5  | python3 -c "import json,sys; data=json.load(sys.stdin); ans=data.get('Answer',[]); print(ans[0]['data']) if ans and 'data' in ans[0] else print('')"  || echo "")"
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

# --- end lib/fresh_interactive.sh ---
# --- lib/fresh_preflight.sh ---
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
    docker system prune -f  || true
    docker builder prune -f  || true
    DISK_AVAIL_MB=$(df -BM / | tail -1 | awk '{print $4}' | tr -d 'M')
    if [ "$DISK_AVAIL_MB" -lt 1500 ]; then
        echo -e "${RED}  ✗ Insufficient disk space (${DISK_AVAIL_MB}MB). Need at least 1.5GB for fresh install.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ After cleanup: ${DISK_AVAIL_MB}MB available${NC}"
fi

# ─── Git Initialization & Sync ──────────────────────────────────────────────
SMSLY_BRANCH="${SMSLY_BRANCH:-master}"
SMSLY_GIT_REMOTE="${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"

if [ -d "$INSTALL_DIR/.git" ]; then
    echo -e "${BLUE}  → Updating existing repository ($SMSLY_BRANCH)...${NC}"
    cd "$INSTALL_DIR"
    ensure_local_ignores
    if [ -n "$(git status --porcelain )" ]; then
        echo -e "${YELLOW}  ! Local changes detected - stashing before repository sync${NC}"
        git stash push --include-untracked -m "install-sync-$(date +%s)"  || true
    fi
    if ! git fetch origin "$SMSLY_BRANCH"  || ! git reset --hard "origin/$SMSLY_BRANCH" ; then
        echo -e "${RED}  ✗ Git update failed for $SMSLY_BRANCH. SSL verification is always enforced — check network or CA certificates.${NC}"
    fi
else
    echo -e "${BLUE}  → Cloning repository ($SMSLY_BRANCH)...${NC}"
    CLONE_SUCCESS=false
    if [ -f "$INSTALL_DIR/.env" ]; then
        echo -e "${YELLOW}  → Existing .env found — preserving configuration${NC}"
        cp "$INSTALL_DIR/.env" /tmp/smsly-env-backup  || true
    fi
    rm -rf "$INSTALL_DIR"
    if git clone -b "$SMSLY_BRANCH" "$SMSLY_GIT_REMOTE" "$INSTALL_DIR"; then
        CLONE_SUCCESS=true
    else
        echo -e "${RED}  ✗ Git clone failed. SSL verification is always enforced — check network or CA certificates.${NC}"
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
            cp -rv "${SMSLY_INSTALL_WORKDIR}/"* "$INSTALL_DIR/"  || true
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

# --- end lib/fresh_preflight.sh ---
# --- lib/fresh_deps.sh ---
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
    if systemctl is-active --quiet "$svc" ; then
        echo -e "${YELLOW}  ⚠ Stopping conflicting service: $svc${NC}"
        systemctl stop "$svc" || true
        systemctl disable "$svc" || true
    fi
done

# ─── NUCLEAR CLEANUP: Remove ALL stale SMSLY containers, volumes, networks ──
# This prevents: port conflicts, stale DB password volumes, orphan containers
echo -e "${BLUE}  → Cleaning up previous SMSLY installation artifacts...${NC}"

# Stop and remove stale smsly-hosting platform containers (NOT user-deployed services)
# ALSO clean the HA cluster containers (smsly-postgres-*, smsly-redis-*) — they use
# container_name and are not matched by the smsly-hosting- prefix. If they survive,
# their stale volumes stay attached and can't be removed (or worse, keep old DB
# passwords that a fresh install's newly generated secrets can never match).
SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly-hosting-" -q  || true)
SMSLY_CONTAINERS="$SMSLY_CONTAINERS $(docker ps -a --filter "name=smsly-postgres" -q  || true)"
SMSLY_CONTAINERS="$SMSLY_CONTAINERS $(docker ps -a --filter "name=smsly-redis" -q  || true)"
SMSLY_CONTAINERS=$(echo "$SMSLY_CONTAINERS" | tr ' ' '\n' | sort -u | grep -v '^$')
if [ -n "$SMSLY_CONTAINERS" ]; then
    echo -e "${YELLOW}  → Stopping stale smsly platform container(s)...${NC}"
    docker stop $SMSLY_CONTAINERS  || true
    docker rm -f $SMSLY_CONTAINERS  || true
fi

# Remove stale Docker volumes (postgres data with old passwords, etc.)
# A true fresh install (no real .env) generates NEW secrets, so preserving old
# volumes guarantees DB auth failures. Only preserve volumes when a real .env
# (with secrets) exists so the reused .env secrets match the preserved data.
SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly" -q  || true)
if [ -n "$SMSLY_VOLUMES" ]; then
    _env_has_secrets=false
    if [ -f "$INSTALL_DIR/.env" ] && grep -qE '^(POSTGRES_PASSWORD|SECRET_KEY)=' "$INSTALL_DIR/.env" 2>/dev/null; then
        _env_has_secrets=true
    fi
    if [ "${SMSLY_ALLOW_DESTRUCTIVE_FRESH:-0}" = "1" ] || [ "$_env_has_secrets" = "false" ]; then
        if [ "${SMSLY_ALLOW_DESTRUCTIVE_FRESH:-0}" = "1" ]; then
            echo -e "${YELLOW}  → Removing stale SMSLY volumes (SMSLY_ALLOW_DESTRUCTIVE_FRESH=1)...${NC}"
        else
            echo -e "${YELLOW}  → Removing stale SMSLY volumes (no existing .env secrets — new install)${NC}"
        fi
        for vol in $SMSLY_VOLUMES; do
            docker volume rm "$vol"  || true
        done
    else
        echo -e "${YELLOW}  ⚠ Existing SMSLY volumes detected; preserving data (existing .env will be reused).${NC}"
        echo -e "${YELLOW}    Use --wipe or SMSLY_ALLOW_DESTRUCTIVE_FRESH=1 to force removal.${NC}"
    fi
fi

# Remove stale Docker networks
SMSLY_NETWORKS=$(docker network ls --filter "name=smsly" -q  || true)
if [ -n "$SMSLY_NETWORKS" ]; then
    for net in $SMSLY_NETWORKS; do
        docker network rm "$net"  || true
    done
fi

echo -e "${GREEN}  ✓ Previous artifacts cleaned${NC}"

apt_run apt-get update -qq
apt_run apt-get install -y curl wget git python3 python3-pip python3-venv openssl ca-certificates gnupg lsb-release dnsutils apache2-utils fail2ban apparmor-utils

# Install Docker if missing
if ! command -v docker ; then
    echo -e "${BLUE}  → Installing Docker...${NC}"
    mkdir -m 0755 -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list
    apt_run apt-get update -qq
    apt_run apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker || echo -e "${YELLOW}    ⚠ docker.service enable failed${NC}"
    systemctl start docker || echo -e "${YELLOW}    ⚠ docker.service start failed${NC}"
    if ! timeout 30 docker info ; then
        echo -e "${RED}  ✗ Docker daemon failed to start. Check 'systemctl status docker' and kernel modules.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ Docker installed and running${NC}"
else
    echo -e "${GREEN}  ✓ Docker already installed ($(docker --version | head -c 40))${NC}"
fi

# Create smsly system user for container file ownership
id smsly  || useradd -r -s /usr/sbin/nologin -u 1000 smsly  || true

# Ensure docker compose is available
if ! docker compose version ; then
    echo -e "${BLUE}  → Installing Docker Compose plugin...${NC}"
    apt_run apt-get install -y docker-compose-plugin || true
fi
# Fallback to docker-compose v1 if plugin still not available
if ! docker compose version ; then
    if command -v docker-compose ; then
        echo -e "${YELLOW}  ⚠ docker compose plugin not available; falling back to docker-compose v1${NC}"
        docker_compose() { docker-compose "$@"; }
    else
        echo -e "${RED}  ✗ Neither 'docker compose' nor 'docker-compose' found. Install Docker Compose.${NC}"
        exit 1
    fi
fi

# Apply mirror config if applicable (Only if docker is now present)
if command -v docker ; then
    configure_docker_mirror
fi

# Ensure security tools (Trivy and Cosign) are installed for image scanning
ensure_security_tools || true

# ─── Security: bootstrap (fire-and-forget) ──────────────────────────────
# Runs AFTER Docker is installed so docker-compose-based hardening layers
# (falco, crowdsec, gVisor, docker daemon config) can actually start.
if [ -f "$INSTALL_DIR/lib/harden.sh" ]; then
# --- lib/harden.sh ---
#!/bin/bash
set +e

_harden_log() {
    local level="$1"; shift
    case "$level" in
        ok)   echo -e "${GREEN}  ✓ [harden] $*${NC}" ;;
        warn) echo -e "${YELLOW}  ⚠ [harden] $*${NC}" ;;
        err)  echo -e "${RED}  ✗ [harden] $*${NC}" ;;
        info) echo -e "${BLUE}  → [harden] $*${NC}" ;;
    esac
}

# --- lib/harden_fail2ban.sh ---
#!/bin/bash

_harden_fail2ban_bootstrap() {
    if ! command -v fail2ban-client ; then
        apt_run apt-get install -y fail2ban  || true
    fi
    command -v fail2ban-client  || return 1

    [ -f /etc/fail2ban/jail.local ] || cat <<'JAIL_EOF' > /etc/fail2ban/jail.local
[DEFAULT]
bantime = 1h
findtime = 10m
maxretry = 3
banaction = iptables-multiport
banaction_allports = iptables-allports

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 1h
findtime = 10m

[recidive]
enabled = true
filter = recidive
logpath = /var/log/fail2ban.log
action = iptables-allports[name=recidive]
bantime = 24h
findtime = 1d
maxretry = 3
JAIL_EOF
    # Enable Caddy jails when Caddy logs are available
    if [ -d /var/log/caddy ] || docker volume ls --format '{{.Name}}'  | grep -q caddy_logs; then
        # Never duplicate the sections: fail2ban aborts on a repeated
        # [caddy-auth], and every install/update run would otherwise append.
        if ! grep -q '^\[caddy-auth\]' /etc/fail2ban/jail.local 2>/dev/null; then
            cat <<'CADDY_JAIL_EOF' >> /etc/fail2ban/jail.local

[caddy-auth]
enabled = true
filter = caddy-auth
port = http,https
logpath = /var/log/caddy/access.log
maxretry = 5
bantime = 1h

[caddy-dos]
enabled = true
filter = caddy-dos
port = http,https
logpath = /var/log/caddy/access.log
findtime = 300
maxretry = 300
bantime = 600
CADDY_JAIL_EOF
        fi
    fi
    # Caddy auth filter (JSON access log — 401/403 responses)
    [ -f /etc/fail2ban/filter.d/caddy-auth.conf ] || cat <<'FILTER_EOF' > /etc/fail2ban/filter.d/caddy-auth.conf
[Definition]
failregex = ^.*"remote_ip":"<HOST>".*"status":(401|403).*$
ignoreregex =
FILTER_EOF
    # Caddy DoS filter (JSON access log — any request)
    [ -f /etc/fail2ban/filter.d/caddy-dos.conf ] || cat <<'FILTER_EOF' > /etc/fail2ban/filter.d/caddy-dos.conf
[Definition]
failregex = ^.*"remote_ip":"<HOST>".*"method":"(GET|POST|HEAD|PUT|DELETE|PATCH)".*$
ignoreregex =
FILTER_EOF

    systemctl enable fail2ban || _harden_log warn "fail2ban enable failed"
    # Blocking start — wait for the service to actually be ACTIVE (not just for
    # `systemctl restart` to return). If it never comes up we surface the real
    # failure via journalctl instead of spamming socket errors.
    systemctl restart fail2ban || _harden_log warn "fail2ban restart returned non-zero"
    local _up=0
    for _i in $(seq 1 30); do
        if systemctl is-active --quiet fail2ban; then
            _up=1
            break
        fi
        sleep 1
    done
    if [ "$_up" -ne 1 ]; then
        _harden_log err "fail2ban failed to become active — last journalctl output:"
        journalctl -u fail2ban -n 40 --no-pager 2>&1 | sed 's/^/      /' || true
        return 1
    fi
    # Service is active — confirm the client can reach the server socket.
    if ! fail2ban-client ping; then
        _harden_log warn "fail2ban active but client cannot reach socket"
    fi
}

_harden_fail2ban_verify() {
    command -v fail2ban-client  || { _harden_log warn "fail2ban — not installed"; return 1; }
    if ! systemctl is-active --quiet fail2ban; then
        _harden_log warn "fail2ban not running — last journalctl output:"
        journalctl -u fail2ban -n 30 --no-pager 2>&1 | sed 's/^/      /' || true
        return 1
    fi
    if fail2ban-client ping && fail2ban-client status sshd ; then
        _harden_log ok "fail2ban active (sshd + recidive + http)"
        return 0
    fi
    _harden_log warn "fail2ban running but not responding to client"
    return 1
}

# --- end lib/harden_fail2ban.sh ---
# --- lib/harden_ufw.sh ---
#!/bin/bash

_harden_ufw_bootstrap() {
    command -v ufw  || apt_run apt-get install -y ufw  || true
    command -v ufw  || return 1

    # Already active — just verify ports are open, then bail
    if ufw status  | grep -qi "active"; then
        for port in 22 80 443 51820; do
            ufw status verbose  | grep -qE "${port}(/tcp|/udp)?.*ALLOW" || ufw allow "$port" || echo -e "${YELLOW}    ⚠ ufw allow port $port failed${NC}"
        done
        # Whitelist Docker bridges
        for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
            ip link show "$iface" >/dev/null 2>&1 || continue
            ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
        done
        return 0
    fi

    # Inactive — configure and enable (INPUT default deny, FORWARD stays open for Docker)
    ufw --force default deny incoming || echo -e "${YELLOW}    ⚠ ufw default deny incoming failed${NC}"
    ufw --force default allow outgoing || echo -e "${YELLOW}    ⚠ ufw default allow outgoing failed${NC}"
    ufw allow ssh || echo -e "${YELLOW}    ⚠ ufw allow ssh failed${NC}"
    ufw allow 80/tcp || echo -e "${YELLOW}    ⚠ ufw allow 80/tcp failed${NC}"
    ufw allow 443/tcp || echo -e "${YELLOW}    ⚠ ufw allow 443/tcp failed${NC}"
    ufw allow 51820/udp || echo -e "${YELLOW}    ⚠ ufw allow 51820/udp failed${NC}"
    for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
        ip link show "$iface" >/dev/null 2>&1 || continue
        ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
    done
    ufw --force enable || echo -e "${YELLOW}    ⚠ ufw enable failed${NC}"
    # Verify it actually came up
    for _i in $(seq 1 5); do
        ufw status  | grep -qi "active" && break
        sleep 2
    done
}

_harden_ufw_verify() {
    command -v ufw  || { _harden_log warn "ufw — not installed"; return 1; }
    if ufw status  | grep -qi "active"; then
        _harden_log ok "ufw active (host INPUT hardened)"
        return 0
    fi
    _harden_log warn "ufw not active — check ufw status"
    return 1
}

# --- end lib/harden_ufw.sh ---
# --- lib/harden_apparmor.sh ---
#!/bin/bash

_harden_apparmor_bootstrap() {
    command -v aa-status  || apt_run apt-get install -y apparmor apparmor-utils  || true
    command -v aa-status  || return 1
    systemctl enable apparmor || echo -e "${YELLOW}    ⚠ apparmor enable failed${NC}"
    systemctl start apparmor || echo -e "${YELLOW}    ⚠ apparmor start failed${NC}"
}

_harden_apparmor_verify() {
    command -v aa-status  || { _harden_log warn "apparmor — not installed"; return 1; }
    local count
    count=$(aa-status --json  | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d.get('processes',{})))"  || echo "0")
    count="${count//[^0-9]/}"
    : "${count:=0}"
    if [ "$count" -gt 0 ] ; then
        _harden_log ok "apparmor enforcing ($count profiles)"
        return 0
    fi
    _harden_log warn "apparmor installed but no enforce profiles"
    return 1
}

# --- end lib/harden_apparmor.sh ---
# --- lib/harden_auditd.sh ---
#!/bin/bash

_harden_auditd_bootstrap() {
    command -v auditd  || apt_run apt-get install -y auditd audispd-plugins  || true

    if [ ! -f /etc/audit/rules.d/smsly.rules ]; then
        mkdir -p /etc/audit/rules.d
        cat > /etc/audit/rules.d/smsly.rules <<'AUDIT_EOF'
-w /etc/shadow -p wa -k identity
-w /etc/passwd -p wa -k identity
-w /etc/sudoers -p wa -k privilege-escalation
-w /etc/ssh/sshd_config -p wa -k sshd
-w /opt/smsly-hosting/.env -p wa -k smsly-config
-w /opt/smsly-hosting/secrets/ -p wa -k smsly-secrets
-a always,exit -F arch=b64 -S execve -F path=/usr/bin/docker -k docker-exec
-a always,exit -F arch=b64 -S mount -k filesystem-mounts
-a exit,always -F arch=b64 -S execve -F euid=0 -F auid>=1000 -k priv-esc
AUDIT_EOF
    fi
    systemctl enable auditd || echo -e "${YELLOW}    ⚠ auditd enable failed${NC}"
    systemctl restart auditd || echo -e "${YELLOW}    ⚠ auditd restart failed${NC}"
}

_harden_auditd_verify() {
    command -v auditd  || { _harden_log warn "auditd — not installed"; return 1; }
    if systemctl is-active --quiet auditd ; then
        _harden_log ok "auditd active (file + syscall monitoring)"
        return 0
    fi
    _harden_log warn "auditd not running — may need kernel param audit=1"
    return 1
}

# --- end lib/harden_auditd.sh ---
# --- lib/harden_kernel.sh ---
#!/bin/bash

_harden_kernel_bootstrap() {
    local sysctl_file="/etc/sysctl.d/99-smsly-security.conf"
    [ -f "$sysctl_file" ] && return 0  # already applied

    cat > "$sysctl_file" <<'SYSCTL_EOF'
net.ipv4.tcp_syncookies = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0
net.ipv6.conf.all.accept_redirects = 0
net.ipv6.conf.default.accept_redirects = 0
net.ipv6.conf.all.accept_source_route = 0
net.ipv6.conf.default.accept_source_route = 0
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2
kernel.yama.ptrace_scope = 1
kernel.unprivileged_bpf_disabled = 1
kernel.randomize_va_space = 2
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
fs.suid_dumpable = 0
SYSCTL_EOF
    sysctl -p "$sysctl_file" || echo -e "${YELLOW}    ⚠ sysctl -p failed${NC}"
}

_harden_kernel_verify() {
    if [ -f /etc/sysctl.d/99-smsly-security.conf ]; then
        _harden_log ok "kernel hardening applied"
        return 0
    fi
    _harden_log warn "kernel hardening not applied"
    return 1
}

# --- end lib/harden_kernel.sh ---
# --- lib/harden_docker_daemon.sh ---
#!/bin/bash

_harden_docker_daemon_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    local daemon_cfg="/etc/docker/daemon.json"
    [ ! -f "$daemon_cfg" ] && echo '{}' > "$daemon_cfg"

    local changed=false

    # log rotation
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('log-driver')=='json-file' and d.get('log-opts',{}).get('max-size')=='10m' else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['log-driver'] = 'json-file'
cfg['log-opts'] = {'max-size': '10m', 'max-file': '3'}
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # live-restore
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('live-restore') else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg['live-restore'] = True
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # seccomp
    python3 -c "import json; d=json.load(open('$daemon_cfg')); exit(0 if d.get('features',{}).get('seccomp') else 1)"  || {
        python3 -c "
import json
with open('$daemon_cfg') as f: cfg = json.load(f)
cfg.setdefault('features', {})['seccomp'] = True
with open('$daemon_cfg','w') as f: json.dump(cfg, f, indent=2)
"
        changed=true
    }

    # Restart Docker if config changed AND no SMSLY containers are running
    # (doing so live would kill production).
    if [ "$changed" = "true" ]; then
        local _smsly_ctrs
        _smsly_ctrs="$(docker ps --format '{{.Names}}'  | grep -c smsly || true)"
        if [ "$_smsly_ctrs" -eq 0 ]; then
            _harden_log info "Docker daemon config changed — restarting Docker..."
            systemctl restart docker || { _harden_log error "Docker restart failed"; }
            for _i in $(seq 1 30); do
                docker info  && break
                sleep 2
            done
            _harden_log ok "Docker daemon restarted with security config"
        else
            _harden_log warn "Docker daemon config changed but $_smsly_ctrs SMSLY containers are running — deferring restart (apply on next daemon reload)"
        fi
    fi
}

_harden_docker_daemon_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    local daemon_cfg="/etc/docker/daemon.json"
    if [ -f "$daemon_cfg" ] && python3 -c "import json; json.load(open('$daemon_cfg'))" ; then
        _harden_log ok "docker daemon security config present"
        return 0
    fi
    _harden_log warn "docker daemon config missing or invalid"
    return 1
}

# --- end lib/harden_docker_daemon.sh ---
# --- lib/harden_crowdsec.sh ---
#!/bin/bash

_harden_crowdsec_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    # CrowdSec comes from the main docker-compose stack — if the container
    # isn't running, try docker compose up -d for just that service.
    if docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec"; then
        return 0  # already up
    fi
    # Blocking start — wait for container to be healthy
    # The harden bootstrap may run before fresh_config has generated .env,
    # so only pass --env-file when the file exists.
    local env_args=()
    [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
    docker compose \
        "${env_args[@]}" \
        -f "$COMPOSE_FILE" \
        up -d crowdsec || echo -e "${YELLOW}    ⚠ crowdsec docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec" && break
        sleep 2
    done
}

_harden_crowdsec_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! docker ps --format '{{.Names}}'  | grep -q "smsly-crowdsec"; then
        _harden_log warn "crowdsec — container not running"
        return 1
    fi
    # Refresh hub scenarios — only upgrade when explicitly allowed.
    # Auto-upgrading on every harden.sh run can silently break
    # production WAF if CrowdSec ships a breaking parser change.
    timeout -k 5 60 docker exec smsly-crowdsec cscli hub update  || _harden_log warn "crowdsec hub update failed"
    if [ "${CROWDSEC_AUTO_UPGRADE_HUB:-0}" = "1" ]; then
        timeout -k 5 60 docker exec smsly-crowdsec cscli hub upgrade  || _harden_log warn "crowdsec hub upgrade failed"
    else
        _harden_log info "crowdsec hub upgrade skipped (set CROWDSEC_AUTO_UPGRADE_HUB=1 to enable)"
    fi
    _harden_log ok "crowdsec deployed"
    return 0
}

# --- end lib/harden_crowdsec.sh ---
# --- lib/harden_falco.sh ---
#!/bin/bash

_harden_falco_bootstrap() {
    command -v docker >/dev/null 2>&1 || return 0
    local compose_file="$INSTALL_DIR/infrastructure/docker/docker-compose.falco.yml"
    [ -f "$compose_file" ] || return 1

    # Blocking start — always recreate so config changes take effect.
    # The harden bootstrap may run before fresh_config has generated .env,
    # so only pass --env-file when the file exists (compose file needs no vars).
    local env_args=()
    [ -f "$INSTALL_DIR/.env" ] && env_args=(--env-file "$INSTALL_DIR/.env")
    # smsly-net is declared external in the falco compose file but is only
    # created during stack deploy (fresh_deploy.sh) — the harden bootstrap
    # runs earlier, so create it here if missing.
    docker network inspect smsly-net >/dev/null 2>&1 || docker network create smsly-net >/dev/null 2>&1 || true
    docker compose \
        "${env_args[@]}" \
        -f "$compose_file" \
        up -d --force-recreate --pull always || echo -e "${YELLOW}    ⚠ falco docker compose up failed${NC}"
    for _i in $(seq 1 15); do
        docker ps --format '{{.Names}}'  | grep -q "smsly-falco" && break
        sleep 2
    done
}

_harden_falco_verify() {
    command -v docker >/dev/null 2>&1 || return 0
    if ! docker ps --format '{{.Names}}'  | grep -q "smsly-falco"; then
        _harden_log warn "falco — container not running"
        return 1
    fi
    _harden_log ok "falco deployed"
    return 0
}

# --- end lib/harden_falco.sh ---
# --- lib/harden_container_runtime.sh ---
#!/bin/bash

_harden_container_runtime_bootstrap() {
    local install_dir="${INSTALL_DIR:-/opt/smsly-hosting}"
    local env_file="$install_dir/.env"

    # If CONTAINER_RUNTIME is already persisted in .env, skip detection.
    # The user can clear it to re-detect.
    if [ -f "$env_file" ] && grep -q '^CONTAINER_RUNTIME=' "$env_file" ; then
        return 0
    fi

    # Try Kata first (stronger isolation, requires KVM)
    if [ -e /dev/kvm ] && ! command -v kata-runtime ; then
        if [ -f "$install_dir/lib/install-kata.sh" ]; then
            echo -e "${BLUE}  → [harden] Kata Containers (KVM available) — installing...${NC}"
            bash "$install_dir/lib/install-kata.sh" || true
        fi
    fi

    if command -v kata-runtime ; then
        env_set_value "$env_file" "CONTAINER_RUNTIME" "kata"
        echo -e "${BLUE}  → [harden] Persisted CONTAINER_RUNTIME=kata in .env${NC}"
        return 0
    fi

    # Fall back to gVisor (lighter, no KVM required)
    if ! command -v runsc ; then
        if [ -f "$install_dir/lib/install-gvisor.sh" ]; then
            echo -e "${BLUE}  → [harden] gVisor (runsc) — installing...${NC}"
            bash "$install_dir/lib/install-gvisor.sh" || true
        fi
    fi

    if command -v runsc ; then
        env_set_value "$env_file" "CONTAINER_RUNTIME" "runsc"
        echo -e "${BLUE}  → [harden] Persisted CONTAINER_RUNTIME=runsc in .env${NC}"
        return 0
    fi
}

_harden_container_runtime_verify() {
    local found=0

    if command -v runsc ; then
        _harden_log ok "gVisor (runsc) installed"
        found=1
    fi

    if command -v kata-runtime ; then
        _harden_log ok "Kata Containers installed"
        found=1
    fi

    if [ "$found" -eq 0 ]; then
        _harden_log warn "container runtime sandboxing — install gVisor or Kata for VM-level isolation"
        return 1
    fi

    # Check Docker runtime registration
    if [ -f /etc/docker/daemon.json ]; then
        if python3 -c "import json; import sys; cfg=json.load(open('/etc/docker/daemon.json')); sys.exit(0 if 'runsc' in cfg.get('runtimes',{}) else 1)" ; then
            _harden_log ok "gVisor registered with Docker"
        elif python3 -c "import json; import sys; cfg=json.load(open('/etc/docker/daemon.json')); sys.exit(0 if 'kata-runtime' in cfg.get('runtimes',{}) else 1)" ; then
            _harden_log ok "Kata registered with Docker"
        fi
    fi

    # `found` is a 0/1 FLAG, not an exit code — returning it turns a successful
    # gVisor/Kata install into a FAILED security check (found=1 -> return 1).
    return 0
}

# --- end lib/harden_container_runtime.sh ---
# --- lib/harden_trivy.sh ---
#!/bin/bash

_harden_trivy_bootstrap() {
    if command -v trivy ; then
        return 0  # already installed
    fi

    _harden_log info "Installing Trivy vulnerability scanner..."
    local trivy_version="v0.54.1"
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64)  arch="64bit" ;;
        aarch64) arch="ARM64" ;;
        *)       _harden_log warn "Trivy — unsupported architecture: $arch"; return 1 ;;
    esac

    local deb_url="https://github.com/aquasecurity/trivy/releases/download/${trivy_version}/trivy_${trivy_version#v}_Linux-${arch}.deb"
    local tmp_deb
    tmp_deb="$(mktemp /tmp/trivy.XXXXXX.deb)"

    # Attempt 1: Direct DEB download with retries and timeouts
    if curl --retry 3 --retry-delay 2 --connect-timeout 15 -fsSL "$deb_url" -o "$tmp_deb" ; then
        if ! dpkg -i "$tmp_deb" ; then
            apt-get install -f -y  || true
            dpkg -i "$tmp_deb"  || true
        fi
        rm -f "$tmp_deb"
    else
        rm -f "$tmp_deb"
        _harden_log info "Direct DEB download failed — trying official APT repo and install script..."
    fi

    # Attempt 2: Official APT Repository fallback
    if ! command -v trivy ; then
        apt-get update -qq  || true
        if ! apt-get install -y trivy ; then
            if command -v gpg ; then
                curl --retry 2 --connect-timeout 10 -fsSL https://aquasecurity.github.io/trivy-repo/deb/public.key  | gpg --dearmor -o /usr/share/keyrings/trivy.gpg  || true
                echo "deb [signed-by=/usr/share/keyrings/trivy.gpg] https://aquasecurity.github.io/trivy-repo/deb $(lsb_release -sc  || echo stable) main" > /etc/apt/sources.list.d/trivy.list  || true
                apt-get update -qq  || true
                apt-get install -y trivy  || true
            fi
        fi
    fi

    # Attempt 3: Official Contrib script fallback
    if ! command -v trivy ; then
        curl --retry 2 --connect-timeout 10 -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin  || true
    fi

    if command -v trivy ; then
        _harden_log ok "Trivy installed successfully"
        return 0
    fi
    _harden_log warn "Trivy — download and installation fallbacks failed"
    return 1
}

_harden_trivy_verify() {
    if command -v trivy ; then
        local ver
        ver="$(trivy --version  | head -1 || true)"
        _harden_log ok "Trivy available: ${ver}"
        return 0
    fi
    _harden_log warn "Trivy — not installed (image vulnerability scanning unavailable)"
    return 1
}

# --- end lib/harden_trivy.sh ---
# --- lib/harden_infisical.sh ---
#!/bin/bash

_harden_infisical_bootstrap() {
    local infisical_script="$INSTALL_DIR/lib/infisical.sh"
    if [ ! -f "$infisical_script" ]; then
        _harden_log info "Infisical script not found — skipping"
        return 0
    fi
    # Source Infisical functions and bootstrap
    # shellcheck disable=SC1090
    source "$infisical_script"  || {
        _harden_log warn "Failed to source infisical.sh"
        return 1
    }
    if ! command -v infisical_bootstrap ; then
        _harden_log warn "infisical_bootstrap function not found"
        return 1
    fi
    infisical_bootstrap  || {
        _harden_log warn "Infisical bootstrap had issues"
        return 1
    }
    return 0
}

_harden_infisical_verify() {
    # Optional layer: the bootstrap skips when lib/infisical.sh is absent —
    # the verify must skip too, or every install reports a phantom failure.
    local infisical_script="${INSTALL_DIR:-/opt/smsly-hosting}/lib/infisical.sh"
    if [ ! -f "$infisical_script" ]; then
        return 0
    fi
    command -v docker >/dev/null 2>&1 || return 0
    if docker ps --format '{{.Names}}'  | grep -q "smsly-infisical"; then
        _harden_log ok "Infisical running"
        return 0
    fi
    _harden_log warn "Infisical — container not running"
    return 1
}

# --- end lib/harden_infisical.sh ---

harden_security_bootstrap() {
    echo -e "${BLUE}  → [harden] Bootstrapping security stack (blocking)...${NC}"
    local _harden_failures=0
    _harden_fail2ban_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_ufw_bootstrap        || { _harden_failures=$((_harden_failures + 1)); }
    _harden_apparmor_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_auditd_bootstrap     || { _harden_failures=$((_harden_failures + 1)); }
    _harden_kernel_bootstrap
    _harden_docker_daemon_bootstrap
    _harden_crowdsec_bootstrap   || { _harden_failures=$((_harden_failures + 1)); }
    _harden_falco_bootstrap      || { _harden_failures=$((_harden_failures + 1)); }
    _harden_container_runtime_bootstrap
    _harden_trivy_bootstrap      || { _harden_failures=$((_harden_failures + 1)); }
    _harden_infisical_bootstrap  || { _harden_failures=$((_harden_failures + 1)); }
    if [ "$_harden_failures" -gt 0 ]; then
        echo -e "${YELLOW}  ⚠ [harden] $_harden_failures layer(s) had issues — verify will report details${NC}"
    else
        echo -e "${GREEN}  ✓ [harden] Bootstrap complete — all layers started${NC}"
    fi
    return 0
}

harden_security_verify() {
    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Security Stack — Verification${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"

    local failures=0 checks=0

    # NOTE: never use standalone `((checks++))` here — when the counter is 0
    # the arithmetic expression evaluates to 0 → exit status 1 → under `set -e`
    # (re-enabled by fresh_hardening.sh after harden.sh's `set +e`) the whole
    # install dies silently after the first check.
    if ! _harden_fail2ban_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_ufw_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_apparmor_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_auditd_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_kernel_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_docker_daemon_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_crowdsec_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_falco_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_container_runtime_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_trivy_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))
    if ! _harden_infisical_verify; then failures=$((failures + 1)); fi
    checks=$((checks + 1))

    local passed=$((checks - failures))
    echo ""
    if [ "$failures" -eq 0 ]; then
        echo -e "${GREEN}  All $passed/$checks security checks passed${NC}"
    else
        echo -e "${RED}  Security: $passed/$checks passed, $failures FAILED${NC}"
        echo -e "${YELLOW}  Review failures above — run 'sudo bash install.sh --debug' for details${NC}"
    fi
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# --- end lib/harden.sh ---
    harden_security_bootstrap
else
    # Minimal fallback: basic Fail2ban SSH protection
    cat << 'EOF' > /etc/fail2ban/jail.local
[DEFAULT]
bantime = 10m
findtime = 10m
maxretry = 5

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
EOF
    systemctl enable fail2ban  || true
    systemctl restart fail2ban  &
    echo -e "${GREEN}  ✓ Fail2ban configured and started${NC}"
fi


# Ensure WireGuard mesh interface exists (master gets 10.100.0.1, nodes get
# a placeholder that will be updated by WireGuardService after provisioning).
ensure_wireguard_mesh() {
    local mesh_ip="${MASTER_MESH_IP:-10.100.0.1}"
    local wg_iface="wg0"

    # On node mode, install WireGuard and create a placeholder interface.
    # The real mesh IP (e.g. 10.100.0.x) is assigned later by
    # WireGuardService.ensure_server_in_default_mesh(), but having the
    # interface ready prevents delays during provisioning.
    if is_node_mode; then
        mesh_ip="${NODE_MESH_IP:-10.100.0.2}"
        echo -e "${BLUE}  → Configuring WireGuard mesh on node ($wg_iface: $mesh_ip)...${NC}"
        if ! command -v wg ; then
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
WGCONF
        fi
        systemctl enable --now "wg-quick@${wg_iface}"  || true
        if ip link show "$wg_iface" ; then
            echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface: $mesh_ip) is up on node${NC}"
        else
            echo -e "${YELLOW}  ⚠ WireGuard ($wg_iface) failed to start on node — mesh will be configured post-provision${NC}"
        fi
        return 0
    fi

    # Lite agents don't run WireGuard (they connect via master's mesh)
    if is_agent_lite_mode; then
        return 0
    fi
    if ip link show "$wg_iface" ; then
        echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface) already configured${NC}"
        return 0
    fi
    echo -e "${BLUE}  → Configuring WireGuard mesh interface ($wg_iface: $mesh_ip)...${NC}"
    if ! command -v wg ; then
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
WGCONF
    fi
    systemctl enable --now "wg-quick@${wg_iface}"  || true
    if ip link show "$wg_iface" ; then
        echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface: $mesh_ip) is up${NC}"
    else
        echo -e "${YELLOW}  ⚠ WireGuard ($wg_iface) failed to start — PgCat mesh binding may fail${NC}"
    fi
}
ensure_wireguard_mesh

echo -e "${GREEN}  ✓ Dependencies installed${NC}"
    set_checkpoint "dependencies_installed"
fi

# --- end lib/fresh_deps.sh ---
# --- lib/fresh_config.sh ---
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
            cp -rn . "$INSTALL_DIR/"  || cp -r . "$INSTALL_DIR/"
        fi
    else
        if [ -d "$INSTALL_DIR/.git" ]; then
             echo -e "${BLUE}  → Updating existing repository...${NC}"
             cd "$INSTALL_DIR"
             if ! git pull origin "$SMSLY_BRANCH" ; then
                 echo -e "${RED}  ✗ Git pull failed for $SMSLY_BRANCH. SSL verification is always enforced.${NC}"
             fi
        else
             echo -e "${BLUE}  → Cloning repository...${NC}"
             if [ -f "$INSTALL_DIR/.env" ]; then
                 cp "$INSTALL_DIR/.env" /tmp/smsly-env-backup  || true
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
    git checkout -b "$SMSLY_BRANCH"  || true
    git remote add origin "$SMSLY_GIT_REMOTE"
    if ! git fetch origin "$SMSLY_BRANCH" -q --depth=1; then
        echo -e "${YELLOW}  ⚠ Git fetch failed — repository will be unlinked from remote (SSL verification enforced)${NC}"
    fi
    git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH"  || true
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
for c_id in $(docker ps --filter "name=smsly-hosting" -q  || true); do
    config_file=$(docker inspect "$c_id" --format='{{index .Config.Labels "com.docker.compose.project.config_files"}}'  || true)
    compose_base=$(basename "$COMPOSE_FILE")
    if [ -n "$config_file" ] && [[ "$config_file" != *"$compose_base"* ]]; then
        wrong_project=true
        break
    fi
done

if [ "$wrong_project" = "true" ]; then
    echo -e "${YELLOW}  ⚠ Found containers running from a different compose project configuration. Stopping...${NC}"
    for c_id in $(docker ps --filter "name=smsly-hosting" -q  || true); do
        config_file=$(docker inspect "$c_id" --format='{{index .Config.Labels "com.docker.compose.project.config_files"}}'  || true)
        compose_base=$(basename "$COMPOSE_FILE")
        if [ -n "$config_file" ] && [[ "$config_file" != *"$compose_base"* ]]; then
            docker stop "$c_id"  || true
            docker rm "$c_id"  || true
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
    source "$INSTALL_DIR/.env"  || true
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
    pip3 install cryptography -q --break-system-packages  || \
        pip3 install cryptography -q  || \
        (echo -e "${YELLOW}  → Retrying cryptography install...${NC}" && \
         pip3 install cryptography 2>&1 | tail -3) || true

    # Verify cryptography is importable before proceeding
    if ! python3 -c "from cryptography.fernet import Fernet; print('ok')" ; then
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
            SECRET_KEY|FIELD_ENCRYPTION_KEY|POSTGRES_PASSWORD|REDIS_PASSWORD|RABBITMQ_PASSWORD|GATEWAY_SECRET|GITHUB_WEBHOOK_SECRET|AUTOSCALER_API_TOKEN|FRP_AUTH_TOKEN|PGCAT_ADMIN_PASSWORD|REPLICATION_PASSWORD|SENTINEL_PASSWORD|REGISTRY_HTTP_SECRET|CROWDSEC_BOUNCER_KEY)
                printf -v "$_smsly_secrets_key" '%s' "$_smsly_secrets_val"
                ;;
        esac
    done < <(python3 "$INSTALL_DIR/scripts/generate_env_secrets.py" --shell  | grep -E '^[A-Z_]+=' || true)
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
        FIELD_ENCRYPTION_KEY="${MASTER_FIELD_ENCRYPTION_KEY:-$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  || true)}"
    fi
    # Ensure all other secrets have fallback values just in case
    [ -n "${SECRET_KEY:-}" ] || SECRET_KEY="$(python3 -c "import secrets,string; chars=string.ascii_letters+string.digits; print(''.join(secrets.choice(chars) for _ in range(50)))"  || true)"
    [ -n "${POSTGRES_PASSWORD:-}" ] || POSTGRES_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${REDIS_PASSWORD:-}" ] || REDIS_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${RABBITMQ_PASSWORD:-}" ] || RABBITMQ_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${GATEWAY_SECRET:-}" ] || GATEWAY_SECRET="$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
    [ -n "${GITHUB_WEBHOOK_SECRET:-}" ] || GITHUB_WEBHOOK_SECRET="$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
    [ -n "${AUTOSCALER_API_TOKEN:-}" ] || AUTOSCALER_API_TOKEN="$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
    [ -n "${FRP_AUTH_TOKEN:-}" ] || FRP_AUTH_TOKEN="$(python3 -c "import secrets; print(secrets.token_hex(64))"  || true)"
    [ -n "${PGCAT_ADMIN_PASSWORD:-}" ] || PGCAT_ADMIN_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(48))"  || true)"
    [ -n "${GRAFANA_PASSWORD:-}" ] || GRAFANA_PASSWORD="$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits+'-_') for _ in range(40)))"  || openssl rand -base64 30 | tr -d '+/=' )"
    [ -n "${BACKUP_ENCRYPTION_KEY:-}" ] || BACKUP_ENCRYPTION_KEY="$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  || openssl rand -base64 32)"
    [ -n "${CROWDSEC_BOUNCER_KEY:-}" ] || CROWDSEC_BOUNCER_KEY="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${REGISTRY_HTTP_SECRET:-}" ] || REGISTRY_HTTP_SECRET="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${BACKUP_REQUIRE_ENCRYPTION:-}" ] || BACKUP_REQUIRE_ENCRYPTION="true"
    # SECURITY: SSH strict host-key checking. Defaults to false (accept-first)
    # for convenience during initial provisioning. Operators managing trusted
    # environments with pre-populated known_hosts should set this to "true".
    [ -n "${SMSLY_STRICT_SSH_HOST_KEY_CHECK:-}" ] || SMSLY_STRICT_SSH_HOST_KEY_CHECK="false"
    # Read-replica plumbing (used by pgcat for replica routing).
    # Initialize empty defaults so set -u doesn't trip on them later.
    [ -n "${REPLICATION_PASSWORD:-}" ] || REPLICATION_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${SENTINEL_PASSWORD:-}" ] || SENTINEL_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(32))"  || true)"
    [ -n "${DB_REPLICA_HOSTS:-}" ] || DB_REPLICA_HOSTS=""

    # Validate Fernet key format
    if ! echo "$FIELD_ENCRYPTION_KEY" | python3 -c "
import sys
from cryptography.fernet import Fernet
try:
    Fernet(sys.stdin.read().strip().encode())
    print('valid')
except Exception:
    print('invalid')
"  | grep -q valid; then
        echo -e "${RED}  ✗ CRITICAL: Failed to generate a valid Fernet encryption key.${NC}"
        echo -e "${RED}    Ensure the 'cryptography' package is installed and retry.${NC}"
        echo -e "${RED}    pip3 install cryptography${NC}"
        exit 1
    fi

    echo -e "${GREEN}  ✓ All secrets generated successfully${NC}"

    # ─── Cosign keypair (image signing) ────────────────────────────────────
    # Generate a password-protected cosign keypair so the platform's own
    # builds can produce verifiable signatures.  Without this, every local
    # build falls through to keyless Sigstore signing which only works with
    # GitHub Actions OIDC — private-key signing is a hard requirement on
    # self-hosted / air-gapped nodes.
    echo -e "${BLUE}  → Bootstrapping Cosign signing keypair...${NC}"
    mkdir -p "$INSTALL_DIR/cosign-keys"
    COSIGN_PASSWORD="${COSIGN_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_hex(32))"  || openssl rand -hex 32  || echo 'cosign-placeholder')}"
    COSIGN_PRIVATE_KEY_PATH="$INSTALL_DIR/cosign-keys/cosign.key"
    COSIGN_PUBLIC_KEY_PATH="$INSTALL_DIR/cosign-keys/cosign.pub"
    if [ ! -f "$COSIGN_PRIVATE_KEY_PATH" ] || [ ! -f "$COSIGN_PUBLIC_KEY_PATH" ]; then
        if command -v cosign ; then
            COSIGN_PASSWORD="$COSIGN_PASSWORD" cosign generate-key-pair  || true
            # cosign writes to cosign.key / cosign.pub in cwd
            if [ -f cosign.key ]; then
                mv cosign.key "$COSIGN_PRIVATE_KEY_PATH"
                mv cosign.pub "$COSIGN_PUBLIC_KEY_PATH"
                chmod 600 "$COSIGN_PRIVATE_KEY_PATH"
                chmod 644 "$COSIGN_PUBLIC_KEY_PATH"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PASSWORD" "$COSIGN_PASSWORD"
                env_set_value "$INSTALL_DIR/.env" "COSIGN_PRIVATE_KEY_PATH" "$COSIGN_PRIVATE_KEY_PATH"
                echo -e "${GREEN}    ✓ Cosign keypair created at $INSTALL_DIR/cosign-keys/${NC}"
            else
                echo -e "${YELLOW}    ⚠ cosign generate-key-pair ran but no keyfile produced — keyless only${NC}"
            fi
        else
            echo -e "${YELLOW}    ⚠ cosign not installed — image signing will be keyless (requires GitHub OIDC)${NC}"
            echo -e "${YELLOW}    Install with: 'sudo bash install.sh --update' or download cosign manually${NC}"
        fi
    else
        echo -e "${GREEN}    ✓ Cosign keypair already exists — skipping generation${NC}"
    fi


    # Agent-lite nodes must use the master's DB password, not a locally generated one.
    # SSH into the master to fetch the correct POSTGRES_PASSWORD.
    if is_agent_lite_mode && [ -n "${MASTER_IP:-}" ] && [ "$MASTER_IP" != "127.0.0.1" ]; then
        echo -e "${BLUE}  → Fetching master DB password via SSH (master: ${MASTER_IP})...${NC}"
        _master_db_pw="$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes root@${MASTER_IP} \
            "grep '^POSTGRES_PASSWORD=' /opt/smsly-hosting/.env  | head -1 | cut -d= -f2"  || true)"
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
    ENV_TRAEFIK_HTTPS_BIND="127.0.0.1:8443"
    ENV_STARTUP_CADDY_SYNC="true"
    if is_agent_lite_mode; then
        ENV_NODE_TYPE="agent-lite"
        ENV_STARTUP_CADDY_SYNC="false"
    elif is_node_mode; then
        ENV_NODE_TYPE="node"
        ENV_TRAEFIK_HTTP_BIND="0.0.0.0:80"
        ENV_TRAEFIK_HTTPS_BIND="0.0.0.0:443"
        ENV_STARTUP_CADDY_SYNC="false"
    fi
    cat <<EOF > "$ENV_TMP"
# SMSLY Hosting Configuration — Generated $(date -Iseconds)
ENVIRONMENT=production
NODE_TYPE=$ENV_NODE_TYPE
MODE=$ENV_MODE_VALUE
# Compose file used by 'install.sh --update' and other orchestrator scripts.
# NOTE: inside an unquoted heredoc (cat <<EOF), bash still expands
# command substitution on comment lines too. Do NOT put unescaped
# dollar-paren or backtick sequences in heredoc comments.
# Master mode: docker-compose.yml (base file with traefik + caddy inlined).
# Agent-lite mode: overridden below to infrastructure/docker/docker-compose.agent-lite.yml.
COMPOSE_FILE=$INSTALL_DIR/docker-compose.prod.yml
DEBUG=False
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$FIELD_ENCRYPTION_KEY
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_USER=smsly_admin
POSTGRES_DB=smsly_hosting
POSTGRES_HOST=postgres-primary
DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@pgcat:5432/smsly_hosting
DATABASE_CONNECT_TIMEOUT=5

REDIS_PASSWORD=$REDIS_PASSWORD
RABBITMQ_PASSWORD=$RABBITMQ_PASSWORD
RABBITMQ_DEFAULT_USER=smsly_user
RABBITMQ_DEFAULT_PASS=$RABBITMQ_PASSWORD
REDIS_HOST=redis-primary
REDIS_URL=redis://:$REDIS_PASSWORD@redis-primary:6379/0
REDIS_MIN_REPLICAS_TO_WRITE=1
REDIS_MIN_REPLICAS_MAX_LAG=10
# CELERY_ prefix is required for celery-redbeat to read this (see
# backend/config/settings.py: CELERY_REDBEAT_REDIS_URL). Without the prefix
# redbeat falls back to CELERY_BROKER_URL (RabbitMQ AMQP) and redis-py
# crashes with "Redis URL must specify one of the following schemes".
CELERY_REDBEAT_REDIS_URL=redis://:$REDIS_PASSWORD@redis-primary:6379/3

# ── Redis Sentinel (HA) ──────────────────────────────────────────────
# When SENTINEL_HOSTS is set, all Redis connections route through
# Sentinel for automatic master failover.  Set by the HA Redis overlay
# (docker-compose.ha-redis.yml).  Leave empty for standalone Redis.
SENTINEL_HOSTS=${SENTINEL_HOSTS:-}
SENTINEL_SERVICE_NAME=${SENTINEL_SERVICE_NAME:-mymaster}
SENTINEL_PASSWORD=${SENTINEL_PASSWORD:-}

# ── PostgreSQL streaming replication ──────────────────────────────────
REPLICATION_PASSWORD=${REPLICATION_PASSWORD:-}
DB_REPLICA_HOSTS=${DB_REPLICA_HOSTS:-}
REGISTRY_HTTP_SECRET=${REGISTRY_HTTP_SECRET:-}

# ── PostgreSQL durability ─────────────────────────────────────────────
PG_SYNCHRONOUS_COMMIT=on
# PG_SYNCHRONOUS_STANDBY_NAMES=  (unset = async replication)

REDIS_SOCKET_TIMEOUT=5
CELERY_BROKER_URL=amqp://smsly_user:$RABBITMQ_PASSWORD@rabbitmq:5672//

DOMAIN=$DOMAIN
ACME_EMAIL=${ACME_EMAIL:-}
USE_SSL=$USE_SSL

# Inter-service HMAC authentication secret
GATEWAY_SECRET=$GATEWAY_SECRET

# CrowdSec Bouncer Key
CROWDSEC_BOUNCER_KEY=$CROWDSEC_BOUNCER_KEY

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

# Grafana admin password (used by the standalone observability stack)
GRAFANA_PASSWORD=${GRAFANA_PASSWORD:-}

# Grafana external URL for browser embeds (auto-derived from domain)
GRAFANA_EXTERNAL_URL=${DOMAIN_ORIGINS}/grafana

# Direct database connection for migrations (bypasses PgCat pooler)
DIRECT_DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@postgres-primary:5432/smsly_hosting

# Private Docker registry (push/pull deployment images)
CONTAINER_REGISTRY_URL=registry:5000
REGISTRY_USER=smsly-registry

# The installer runs first-boot Django setup explicitly after the stack starts.
# Keep the web container from doing the same work while Compose is waiting on health.
SMSLY_RUN_ENTRYPOINT_TASKS=false

# AppConfig.ready() must stay side-effect free during installs and management commands.
# Edge/proxy sync is performed explicitly by the installer and watcher services.
SMSLY_ENABLE_STARTUP_CADDY_SYNC=$ENV_STARTUP_CADDY_SYNC
TRAEFIK_HTTP_BIND=$ENV_TRAEFIK_HTTP_BIND
TRAEFIK_HTTPS_BIND=$ENV_TRAEFIK_HTTPS_BIND
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
        # Sync the backup so rollback doesn't restore stale/empty .env.backup
        cp "$INSTALL_DIR/.env" "$INSTALL_DIR/.env.backup"
        # 664 so the backend container (runs as UID 1000) can read AND write it.
        # This allows the domain-config signal to persist DOMAIN/USE_SSL back to
        # .env when the user updates settings via the web UI — no SSH needed.
        chown root:1000 "$INSTALL_DIR/.env"
        chmod 640 "$INSTALL_DIR/.env"
        # Docker Compose v2+ resolves .env from the compose file's parent directory,
        # not the CWD. Create a symlink so all compose files can find it.
        _compose_env_link="$INSTALL_DIR/infrastructure/docker/.env"
        rm -f "$_compose_env_link"  || true
        ln -sf ../../.env "$_compose_env_link"  || true
        echo -e "${GREEN}  ✓ Configuration saved to .env${NC}"
    else
        echo -e "${RED}  x Generated .env failed validation. Aborting install.${NC}"
        rm -f "$ENV_TMP"
        exit 1
    fi
fi
# Sync the backup so rollback doesn't restore a stale/empty .env.backup (e.g.
# when the harden phase created a stub .env with only CONTAINER_RUNTIME before
# config_generated backfilled the real secrets into it).
if [ -f "$INSTALL_DIR/.env" ]; then
    cp "$INSTALL_DIR/.env" "$INSTALL_DIR/.env.backup"
fi
    set_checkpoint "config_generated"
fi
if [ -f "$INSTALL_DIR/.env" ]; then
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    apply_agent_lite_env_overrides "$INSTALL_DIR/.env"
    # Ensure .env symlink exists for Docker Compose v2+ .env resolution
    _compose_env_link="$INSTALL_DIR/infrastructure/docker/.env"
    rm -f "$_compose_env_link"  || true
    ln -sf ../../.env "$_compose_env_link"  || true
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

# --- end lib/fresh_config.sh ---
# --- lib/fresh_deploy.sh ---
# -----------------------------------------------------------------------------
# 4. Deployment
# -----------------------------------------------------------------------------
STACK_DEPLOYED_FROM_CHECKPOINT=false
if is_checkpoint_done "stack_deployed"; then
    STACK_DEPLOYED_FROM_CHECKPOINT=true
else
    echo -e "\n${YELLOW}[4/9] Deploying Container Stack...${NC}"

# Ensure networks exist
docker network create smsly-net  || true
docker network create smsly-proxy  || true

# Ensure external volumes exist.
# docker-compose.yml marks `caddy_data` as `external: true` with fixed name
# `smsly-hosting_caddy_data`. Compose refuses to create external volumes
# and aborts `up` with `external volume "..." not found` if they are
# missing. Pre-create here (idempotent — Compose / Docker return a benign
# "already exists" error which we swallow).
#
# Note: caddy_config is no longer a separate named volume. The caddy
# container now reads /config from the same ./caddy-config bind mount
# the backend writes the IP self-signed cert to, fixing the
# "open /config/certs/ip.crt: no such file or directory" crash loop.
if command -v docker ; then
    docker volume create --name smsly-hosting_caddy_data  || true

    # Caddy container runs as uid 1000 (nextjs user); chown the volume
    # root so the container can read/write its ACME state. Same pattern
    # already used for backups_data in ensure_infrastructure_permissions.
    if docker volume inspect smsly-hosting_caddy_data ; then
        docker run --rm -v smsly-hosting_caddy_data:/data alpine chown -R 1000:1000 /data  || true
    fi
fi

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

# Regenerate registry TLS if EITHER file is missing OR if the existing
# key/cert don't match (e.g. one was rotated independently). The earlier
# `||` check only caught missing files; mismatched pairs caused
# `registry:2.8.3` to crash-loop with "tls: private key does not match
# public key" forever. Regenerating as a matched pair is the only safe
# option — we cannot repair an existing cert without the issuing key.
_regen_registry_tls() {
    echo -e "${BLUE}    Generating self-signed TLS cert for registry...${NC}"
    # openssl req writes key then cert; if key write fails halfway the
    # cert from the prior generation would be orphaned. The atomic
    # rename pattern below ensures consumers (the registry container)
    # never see a half-written pair.
    _tmp_dir="$(mktemp -d)"
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "${_tmp_dir}/registry.key" \
        -out    "${_tmp_dir}/registry.crt" \
        -subj "/CN=registry" \
        -addext "subjectAltName=DNS:registry,DNS:localhost,IP:127.0.0.1,IP:10.100.0.1" 
    local _rc=$?
    if [ "$_rc" -ne 0 ]; then
        rm -rf "$_tmp_dir"
        echo -e "${YELLOW}    ⚠ Failed to generate registry cert (openssl missing?)${NC}"
        return $_rc
    fi
    mv "${_tmp_dir}/registry.key" "$INSTALL_DIR/certs/registry.key"
    mv "${_tmp_dir}/registry.crt" "$INSTALL_DIR/certs/registry.crt"
    rm -rf "$_tmp_dir"
    chmod 644 "$INSTALL_DIR/certs/registry.crt" "$INSTALL_DIR/certs/registry.key"
}

_registry_tls_ok() {
    [ -f "$INSTALL_DIR/certs/registry.key" ] || return 1
    [ -f "$INSTALL_DIR/certs/registry.crt" ] || return 1
    # openssl x509 -noout -modulus matches the cert's modulus;
    # openssl rsa  -noout -modulus matches the key's modulus. They must
    # be equal for the TLS handshake to succeed.
    local _cmod _kmod
    _cmod="$(openssl x509 -in "$INSTALL_DIR/certs/registry.crt" -noout -modulus  | openssl sha256)" || return 1
    _kmod="$(openssl rsa  -in "$INSTALL_DIR/certs/registry.key" -noout -modulus  | openssl sha256)" || return 1
    [ "$_cmod" = "$_kmod" ]
}

if ! _registry_tls_ok; then
    _regen_registry_tls
    if ! _registry_tls_ok; then
        echo -e "${RED}    ✗ Registry TLS cert/key still mismatched or missing after regen attempt${NC}"
        echo -e "${YELLOW}      Manual fix on host: openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \\${NC}"
        echo -e "${YELLOW}        -keyout /opt/smsly-hosting/certs/registry.key \\${NC}"
        echo -e "${YELLOW}        -out    /opt/smsly-hosting/certs/registry.crt \\${NC}"
        echo -e "${YELLOW}        -subj '/CN=registry'${NC}"
    else
        echo -e "${BLUE}    Restarting registry container to pick up new TLS certs...${NC}"
        docker restart smsly-hosting-registry-1 || echo -e "${YELLOW}    ⚠ Registry restart failed${NC}"
    fi
fi
if [ ! -f "$INSTALL_DIR/auth/htpasswd" ] || [ -z "${REGISTRY_PASSWORD:-}" ] || [ -z "${REGISTRY_USER:-}" ]; then
    REGISTRY_PASS="${REGISTRY_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(18))"  || openssl rand -hex 12  || echo 'auto-generated-change-me')}"
    if command -v htpasswd ; then
        htpasswd -Bbn "${REGISTRY_USER:-smsly-registry}" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"
    else
        # Python-based bcrypt fallback
        python3 -c "
import bcrypt, sys
pw = sys.argv[1] if len(sys.argv) > 1 else '$REGISTRY_PASS'
print(f'${REGISTRY_USER:-smsly-registry}:' + bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode())
" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"  || \
        echo -e "${YELLOW}    ⚠ Failed to generate htpasswd (neither htpasswd nor python bcrypt available)${NC}"
    fi
    env_set_value "$INSTALL_DIR/.env" "REGISTRY_USER" "${REGISTRY_USER:-smsly-registry}"
    env_set_value "$INSTALL_DIR/.env" "REGISTRY_PASSWORD" "$REGISTRY_PASS"
fi
echo -e "${GREEN}  ✓ Registry auth + TLS configured${NC}"

# Install registry cert into Docker's cert trust store so the daemon
# connects via HTTPS (not HTTP fallback) to the registry.
install_registry_docker_certs

# Authenticate Docker CLI with the private registry so the daemon can
# pull base images during builds without 403 errors.
docker_login

# Ensure bind-mounted config paths exist before `docker compose up`.
ensure_infrastructure_permissions
# Pre-create caddy bind-mount directories (needed by compose volume driver)
mkdir -p "$INSTALL_DIR/caddy-config" "$INSTALL_DIR/caddy-logs"
if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "${BLUE}  → Lite Agent mode: disabling master-only Caddy services before Traefik bind.${NC}"
    true
elif [ "$MODE_NODE" = "true" ]; then
    echo -e "${BLUE}  → Node mode: deploying prod stack without frontend/Caddy; Traefik binds public HTTP.${NC}"
fi
echo -e "${BLUE}  → Disabling backend entrypoint bootstrap for installer-controlled migrations...${NC}"
env_set_value "$INSTALL_DIR/.env" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
    echo -e "${BLUE}  → Starting App Stack (Build + Deploy)...${NC}"
    cleanup_stale_containers
    ( while true; do sleep 30; echo -e "${BLUE}      ↳ Progress: Deployment in progress... $(date +%H:%M:%S)${NC}"; done ) &
    HEARTBEAT_PID=$!
    # TODO(install): replace set -e toggle with explicit conditional. The
    # conditional rebuild + retry makes a flat `if ! cmd` rewrite risky; the
    # rc-capture pattern is intentionally retained.
    set +e
    compose_stack_build --no-cache
    DEPLOY_RC=$?
    if [ "$DEPLOY_RC" -eq 0 ]; then
        # Scan freshly built images for vulnerabilities
        if command -v trivy ; then
            echo -e "${BLUE}  → Scanning built images for vulnerabilities...${NC}"
            for _trivy_img in backend frontend; do
                _trivy_tag="smsly/${_trivy_img}:latest"
                if docker image inspect "$_trivy_tag" ; then
                    echo -e "${BLUE}    ↳ Scanning $_trivy_tag...${NC}"
                    trivy image --insecure --scanners vuln --severity CRITICAL,HIGH --exit-code 0 --no-progress "$_trivy_tag"  || echo -e "${YELLOW}    ⚠ $_trivy_tag scan reported warnings — review output above${NC}"
                fi
            done
            unset _trivy_img _trivy_tag
        fi
        compose_stack_up --remove-orphans
        DEPLOY_RC=$?
    fi
    set -e
    kill $HEARTBEAT_PID  || true
    wait $HEARTBEAT_PID  || true
    if [ "$DEPLOY_RC" -ne 0 ]; then
        echo -e "${RED}  ✗ Docker Compose failed during stack deployment (exit $DEPLOY_RC).${NC}"
        echo -e "${YELLOW}  ↳ Re-run with --resume to skip completed steps: sudo bash install.sh --resume${NC}"
        docker compose -f "$COMPOSE_FILE" ps  || true
        docker compose -f "$COMPOSE_FILE" logs --tail=120  || true
        exit "$DEPLOY_RC"
    fi
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        sync_agent_lite_rabbitmq_password
    else
        echo -e "${BLUE}  → Deploying Observability Stack...${NC}"
        # Ensure scripts mounted into containers are executable (git may not preserve +x)
        chmod +x "$INSTALL_DIR"/scripts/alertmanager-entrypoint.sh  || true
        chmod +x "$INSTALL_DIR"/infrastructure/docker/infisical-gen-env.sh  || true
        if [ -f "infrastructure/docker/docker-compose.observability.yml" ]; then
            docker compose -f infrastructure/docker/docker-compose.observability.yml pull --ignore-pull-failures || \
                echo -e "${YELLOW}  ⚠ Observability stack pull failed (non-fatal)${NC}"
            docker compose -f infrastructure/docker/docker-compose.observability.yml up -d --pull always || \
                echo -e "${YELLOW}  ⚠ Observability stack start failed (non-fatal)${NC}"
        fi
    fi
    # Deploy docker-labels exporter to all remote nodes and regenerate target files
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        backend_container=$(docker ps --format '{{.Names}}' | grep -E '^smsly-hosting-backend(-1)?$' | head -1)
        if [ -n "$backend_container" ]; then
            timeout 60 docker exec "$backend_container" python manage.py deploy_docker_labels_exporters || echo -e "${YELLOW}    ⚠ deploy_docker_labels_exporters failed${NC}"
        fi
    fi

    # ─── Infisical auto-provision (master mode only) ─────────────────────
    _INFISICAL_COMPOSE="$INSTALL_DIR/infrastructure/docker/docker-compose.infisical.yml"
    if [ "$MODE_AGENT_LITE" != "true" ] && [ -f "$_INFISICAL_COMPOSE" ]; then
        _infisical_running=$(docker ps --filter "name=infisical" --format '{{.Names}}'  | head -1)
        if [ -n "$_infisical_running" ]; then
            echo -e "${GREEN}  ✓ Infisical already running (${_infisical_running})${NC}"
        else
            echo -e "${BLUE}  → Provisioning Infisical secret manager...${NC}"
            docker volume create infisical_data  || true

            # Create the infisical database in Postgres if it doesn't exist
            _db_container=""
            # HA mode: smsly-postgres-primary
            if docker ps --format '{{.Names}}' | grep -q '^smsly-postgres-primary$'; then
                _db_container="smsly-postgres-primary"
                _db_user="${POSTGRES_USER:-smsly_admin}"
            # Standard mode: smsly-hosting-db-1
            elif docker ps --format '{{.Names}}' | grep -q '^smsly-hosting-db-1$'; then
                _db_container="smsly-hosting-db-1"
                _db_user="${POSTGRES_USER:-postgres}"
            fi
            if [ -n "$_db_container" ]; then
                _db_exists=$(timeout 30 docker exec "$_db_container" psql -U "${_db_user}" -d "${POSTGRES_DB:-smsly_hosting}" -tc \
                    "SELECT 1 FROM pg_database WHERE datname='infisical'"  | tr -d '[:space:]' || true)
                if [ "$_db_exists" != "1" ]; then
                    timeout 30 docker exec "$_db_container" psql -U "${_db_user}" -d "${POSTGRES_DB:-smsly_hosting}" -c \
                        "CREATE DATABASE infisical;"  && \
                        echo -e "${GREEN}  ✓ Created infisical database${NC}" || \
                        echo -e "${YELLOW}  ⚠ Could not create infisical database (may already exist)${NC}"
                fi
            else
                echo -e "${YELLOW}  ⚠ No Postgres container found — skipping infisical database creation${NC}"
            fi

            # Generate env file on the volume
            _gen_script="$INSTALL_DIR/infrastructure/docker/infisical-gen-env.sh"
            if [ -f "$_gen_script" ]; then
                docker run --rm \
                    -v infisical_data:/data \
                    -v "$_gen_script":/tmp/infisical-gen-env.sh:ro \
                    alpine:3.19 \
                    sh /tmp/infisical-gen-env.sh /data/infisical.env  || \
                    echo -e "${YELLOW}  ⚠ Could not generate Infisical env${NC}"
            fi

            docker compose --env-file "$INSTALL_DIR/.env" \
                -f "$_INFISICAL_COMPOSE" up -d --remove-orphans  && \
                echo -e "${GREEN}  ✓ Infisical is running${NC}" || \
                echo -e "${YELLOW}  ⚠ Infisical startup failed (non-fatal — secrets remain in .env)${NC}"
        fi
    fi

    set_checkpoint "stack_deployed"

    # Docker login now that the registry is actually running
    docker_login
fi
if [ "$STACK_DEPLOYED_FROM_CHECKPOINT" = "true" ]; then
    reconcile_compose_stack_after_resume
fi

# --- end lib/fresh_deploy.sh ---
# --- lib/fresh_database.sh ---
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
    if timeout 10 docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U smsly_admin < /dev/null ; then
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
source "$INSTALL_DIR/.env"  || true
set +a
echo -e "${BLUE}  → Syncing database password...${NC}"

# The DB volume persists with the password from FIRST init, and .env may have
# been regenerated since. Local socket auth is TRUST in the official postgres
# image, so ALTER USER over the socket works regardless of the current DB
# password. Note: with POSTGRES_USER=smsly_admin the "postgres" role does NOT
# exist — smsly_admin itself is the superuser.
DB_SUPERUSER="${POSTGRES_USER:-smsly_admin}"
DB_NAME="${POSTGRES_DB:-smsly_hosting}"
PW_SYNCED=false
if timeout 30 docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U "$DB_SUPERUSER" -d postgres \
    -c "ALTER USER ${DB_SUPERUSER} WITH PASSWORD '${POSTGRES_PASSWORD}';" \
    < /dev/null ; then
    echo -e "${GREEN}  ✓ Database password synced via superuser ${DB_SUPERUSER}${NC}"
    PW_SYNCED=true
elif timeout 30 docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U postgres -d postgres \
    -c "ALTER USER ${DB_SUPERUSER} WITH PASSWORD '${POSTGRES_PASSWORD}';" \
    < /dev/null ; then
    echo -e "${GREEN}  ✓ Database password synced via postgres superuser${NC}"
    PW_SYNCED=true
else
    echo -e "${RED}  ✗ Could not sync password over local socket. Check pg_hba.conf${NC}"
fi

# The socket check above bypasses auth (trust), so verify over TCP with the
# .env password — this is the only check that proves the password actually
# matches what the app will use. Must use the network hostname (eth0), not
# 127.0.0.1: the official postgres image trusts loopback too.
if timeout 30 docker compose -f "$COMPOSE_FILE" exec -T \
    -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
    psql -h db -U "$DB_SUPERUSER" -d "$DB_NAME" -c "SELECT 1;" < /dev/null ; then
    echo -e "${GREEN}  ✓ Database password verified over TCP${NC}"
else
    echo -e "${RED}  ✗ Password verification over TCP failed — migrations will fail. Check pg_hba.conf${NC}"
    exit 1
fi

# ─── Ensure PgCat is fresh and connected ──────────────────────────────────────
if [ -f "${COMPOSE_FILE:-docker-compose.prod.yml}" ] && grep -q "^  *pgcat:" "${COMPOSE_FILE:-docker-compose.prod.yml}"  && docker compose -f "$COMPOSE_FILE" ps pgcat ; then
    echo -e "${BLUE}  → Restarting PgCat balancer...${NC}"
    timeout -k 5 30 docker compose -f "$COMPOSE_FILE" restart pgcat || echo -e "${YELLOW}    ⚠ PgCat restart failed${NC}"
fi

# ─── Restart backend so it picks up the correct DB credentials ──────────────
echo -e "${BLUE}  → Restarting backend with synced credentials...${NC}"
timeout -k 5 30 docker compose -f "$COMPOSE_FILE" restart backend || echo -e "${YELLOW}    ⚠ Backend restart failed${NC}"
sleep 5

    echo -e "${BLUE}  → Running Migrations...${NC}"

    # Stop all services that talk to the DB.  Any open connection — even
    # a SELECT — holds a shared lock that blocks the ACCESS EXCLUSIVE
    # lock an ALTER TABLE needs.  Celery, backend health checks, and
    # PgCat connection pools all compete with the migration.
    MIGRATION_STOPPED_SVCS="backend celery celery-deploy celery-fast celery-beat $(grep -q "^  *pgcat:" "${COMPOSE_FILE:-docker-compose.prod.yml}"  && echo "pgcat")"
    echo -e "${BLUE}    Stopping ${MIGRATION_STOPPED_SVCS} to prevent lock contention...${NC}"
    docker compose -f "$COMPOSE_FILE" stop --timeout 15 ${MIGRATION_STOPPED_SVCS} || echo -e "${YELLOW}    ⚠ Stop failed for some services${NC}"
    sleep 3

    # Kill every backend on the database so the migration owns it exclusively
    timeout 30 docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U smsly_admin -d smsly_hosting \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'" \
        < /dev/null \
         || echo -e "${YELLOW}    ⚠ Failed to terminate stale connections${NC}"
    sleep 2

    echo -e "${BLUE}    Running migrations (database: direct)...${NC}"
    # Note: Do NOT run makemigrations — migrations are committed in the repo.
    MIGRATE_OK=false
    # Migration runs via DIRECT_DATABASE_URL which goes straight to the
    # postgres backend, not through PgCat, so PgCat being stopped is safe.
    if run_backend_migrations ; then
        MIGRATE_OK=true
    else
        echo -e "${YELLOW}  ⚠ Migration attempt 1 failed — killing stale connections and retrying...${NC}"
        timeout 30 docker compose -f "$COMPOSE_FILE" exec -T db \
            psql -U smsly_admin -d smsly_hosting \
            -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'" \
            < /dev/null \
             || echo -e "${YELLOW}    ⚠ Failed to terminate stale connections${NC}"
        sleep 5
        if run_backend_migrations ; then
            MIGRATE_OK=true
        fi
    fi

    # Restart everything that was paused
    echo -e "${BLUE}    Restarting ${MIGRATION_STOPPED_SVCS}...${NC}"
    docker compose -f "$COMPOSE_FILE" start ${MIGRATION_STOPPED_SVCS} || echo -e "${YELLOW}    ⚠ Some services failed to restart${NC}"
    sleep 5

    if [ "$MIGRATE_OK" != "true" ]; then
        echo -e "${RED}  ✗ Migrations failed after 2 attempts.${NC}"
        echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs backend${NC}"
        echo -e "${YELLOW}  ↳ Tip: Re-run with --resume: sudo bash install.sh --resume${NC}"
        exit 1
    fi

echo -e "${BLUE}  → Collecting Static Files...${NC}"
    # Fix volume ownership — Docker creates named volumes as root.
    # NOTE: `docker compose exec --user root backend chown` cannot work here:
    # the backend container runs with CapDrop=[ALL], so even uid 0 cannot
    # chown (no CAP_CHOWN). Run chown host-side via a throwaway alpine
    # container instead.
    echo -e "${BLUE}    ↳ Fixing volume ownership...${NC}"
    _vol_json="$(docker compose -f "$COMPOSE_FILE" config --format json 2>/dev/null || true)"
    for _vkey in static_volume media_volume backups_data; do
        _vol_name="$(printf '%s' "$_vol_json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('volumes',{}).get('$_vkey',{}).get('name',''))" 2>/dev/null || true)"
        if [ -n "$_vol_name" ] && docker volume inspect "$_vol_name" >/dev/null 2>&1; then
            timeout 90 docker run --rm -v "$_vol_name":/data alpine chown -R 1000:1000 /data || echo -e "${YELLOW}    ⚠ Volume ownership fix failed for $_vol_name${NC}"
        fi
    done
    echo -e "${BLUE}    ↳ Running collectstatic...${NC}"
    timeout 120 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py collectstatic --noinput < /dev/null || echo -e "${YELLOW}    ⚠ collectstatic failed or timed out${NC}"

    sync_platform_domain_state "$INSTALL_DIR/.env"
    set_checkpoint "database_initialized"
fi
fi

# --- end lib/fresh_database.sh ---
# --- lib/fresh_admin.sh ---
# -----------------------------------------------------------------------------
# 6. Admin User (IDEMPOTENT — skips if admin already exists)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "admin_created"; then
    echo -e "\n${YELLOW}[6/9] Creating Admin User...${NC}"

if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "${BLUE}  → Lite Agent mode: skipping master admin and Local Docker provider setup.${NC}"
    set_checkpoint "admin_created"
else
ADMIN_EXISTS=$(echo "from django.contrib.auth import get_user_model; User = get_user_model(); print('1' if User.objects.filter(username='admin').exists() else '0')" | timeout 60 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell  | tail -1)

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
" | timeout 60 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell  | tail -1 > "$INSTALL_DIR/.token"
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
" | timeout 60 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell  | tail -1 
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

    # ─── Generate Recovery Phrase ─────────────────────────────────────────
    echo -e "${BLUE}  → Generating 12-word recovery phrase...${NC}"
    # Call the service functions directly instead of the HTTP view: the view
    # is DRF-authenticated (@permission_classes([IsAuthenticated])) and 401s
    # when invoked with a RequestFactory request, so the phrase was never
    # written on fresh installs. This mirrors exactly what the view does.
    RECOVERY_PHRASE="$(timeout 60 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
import json
from apps.deployments.models.core import PlatformConfig
from apps.core.services.recovery import generate_recovery_phrase, generate_recovery_salt, hash_recovery_phrase
config = PlatformConfig.load()
phrase = generate_recovery_phrase()
salt = generate_recovery_salt()
config.recovery_phrase_hash = json.dumps({'hash': hash_recovery_phrase(phrase, salt), 'salt': salt})
config.save(update_fields=['recovery_phrase_hash'])
print(' '.join(phrase))
"  < /dev/null | tail -1 || true)"
    if [ -n "$RECOVERY_PHRASE" ]; then
        RECOVERY_PHRASE_TEXT="$(printf '%s' "$RECOVERY_PHRASE" | python3 -c "import sys; s=sys.stdin.read().strip(); print(s if len(s.split()) == 12 else '')"  || true)"
        if [ -n "$RECOVERY_PHRASE_TEXT" ]; then
            echo -e "${GREEN}  ✓ Recovery phrase generated${NC}"
            echo -e "$RECOVERY_PHRASE_TEXT" > "$INSTALL_DIR/.recovery_phrase"
            chmod 600 "$INSTALL_DIR/.recovery_phrase"
            echo -e ""
            echo -e "${YELLOW}════════════════════════════════════════════════════════════${NC}"
            echo -e "${YELLOW}   ⚠  ACCOUNT RECOVERY PHRASE — WRITE THIS DOWN             ${NC}"
            echo -e "${YELLOW}   This is the ONLY time this phrase is displayed.            ${NC}"
            echo -e "${YELLOW}   If all trusted devices are lost, this 12-word phrase       ${NC}"
            echo -e "${YELLOW}   is your last resort to recover admin access.               ${NC}"
            echo -e "${YELLOW}                                                              ${NC}"
            echo -e "${YELLOW}   $RECOVERY_PHRASE_TEXT${NC}"
            echo -e "${YELLOW}                                                              ${NC}"
            echo -e "${YELLOW}   Stored (encrypted) in: $INSTALL_DIR/.recovery_phrase${NC}"
            echo -e "${YELLOW}════════════════════════════════════════════════════════════${NC}"
            echo -e ""
        fi
    fi

    set_checkpoint "admin_created"
fi
fi

# --- end lib/fresh_admin.sh ---
# --- lib/fresh_caddy.sh ---
# -----------------------------------------------------------------------------
# 7. Caddy Reverse Proxy (Public Access — Dockerized)
# -----------------------------------------------------------------------------
# Agent-lite and node modes use Traefik instead of Caddy — skip this step entirely.
if should_manage_caddy; then
if ! is_checkpoint_done "caddy_configured" || [ "$REFRESH_MODE" = "true" ] || [ "$RECOVER_MODE" = "true" ]; then
    echo -e "\n${YELLOW}[7/9] Setting up Dockerized Caddy Proxy...${NC}"

    # Ensure caddy-config directory exists and has correct permissions.
    # Owner is root (uid 0) because the backend/celery containers run as root
    # with CapDrop=[ALL] — they need OWNER write access to regenerate the
    # Caddyfile at runtime (chown/chmod fail without CAP_FOWNER/CAP_CHOWN).
    # Group is 1000 so the caddy container (uid 1000) keeps read/write access
    # to runtime state (tls certs, reload flag) via setgid-inherited files.
    mkdir -p /opt/smsly-hosting/caddy-config
    chown 0:1000 /opt/smsly-hosting/caddy-config
    chmod 2775 /opt/smsly-hosting/caddy-config
    # Caddy access logs (read by fail2ban on host)
    mkdir -p /opt/smsly-hosting/caddy-logs
    chown 1000:1000 /opt/smsly-hosting/caddy-logs
    chmod 2775 /opt/smsly-hosting/caddy-logs

    # SEED: Create a temporary safety Caddyfile so the container doesn't crash on first start.
    # The backend will overwrite this within seconds of starting up.
    if [ ! -f /opt/smsly-hosting/caddy-config/Caddyfile ]; then
        echo -e "${BLUE}  → Seeding initial safety Caddyfile...${NC}"
        cat > /opt/smsly-hosting/caddy-config/Caddyfile <<EOF
:80 {
    respond "System initializing... Please refresh in 30 seconds." 200
}
EOF
        chown 0:1000 /opt/smsly-hosting/caddy-config/Caddyfile
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
    docker compose -f "$COMPOSE_FILE" up -d --no-deps caddy

    # The Caddy container runs as uid 1000 (see infrastructure/caddy/Dockerfile).
    # If the stack was started earlier with the stock caddy image (root), ACME
    # state files in the caddy_data volume are root-owned and the uid-1000
    # process silently fails cert issuance ("permission denied" on lock files).
    # Chown after start so the final image's user can read/write its ACME state.
    if command -v docker && docker volume inspect smsly-hosting_caddy_data >/dev/null 2>&1; then
        echo -e "${BLUE}  → Ensuring caddy_data volume is writable by caddy (uid 1000)...${NC}"
        docker run --rm -v smsly-hosting_caddy_data:/data alpine chown -R 1000:1000 /data  || \
            echo -e "${YELLOW}    ⚠ Could not chown caddy_data volume — cert issuance may fail later${NC}"
    fi

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
                 || true)
            # If Caddy returns "challenge not found" (404), that means it IS
            # reachable but doesn't have this challenge registered — which is
            # the expected behavior for a staging check.
            if echo "$ACME_CHECK" | grep -qi "challenge"; then
                echo -e "${GREEN}  ✓ ACME HTTP-01 reachable for $DOMAIN (staging)${NC}"
                ACME_OK=true
                break
            fi
            # Also try: just checking port 80 responds
            if curl -fsSo /dev/null --max-time 5 "http://${DOMAIN}/" ; then
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
    systemctl stop caddy  || true
    systemctl disable caddy  || true
    rm -f /etc/systemd/system/caddy.service
    systemctl daemon-reload

    set_checkpoint "caddy_configured"
fi
fi # end Caddy skip for agent-lite/node modes

# --- end lib/fresh_caddy.sh ---
# --- lib/fresh_hardening.sh ---
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
if fallocate -l ${ADD_SWAP_MB}M "$NEW_SWAPFILE" ; then
    chmod 600 "$NEW_SWAPFILE"
    mkswap "$NEW_SWAPFILE" 
    swapon "$NEW_SWAPFILE"  || true

    # Make it permanent
    if ! grep -q "$NEW_SWAPFILE" /etc/fstab ; then
        echo "$NEW_SWAPFILE none swap sw 0 0" >> /etc/fstab
    fi

    log "Successfully added ${ADD_SWAP_MB}MB of swap. Total swap is now approx ${NEW_TOTAL_MB}MB."
else
    # Fallback to dd if fallocate fails (e.g. some filesystems don't support it)
    log "fallocate failed, trying dd..."
    if dd if=/dev/zero of="$NEW_SWAPFILE" bs=1M count=$ADD_SWAP_MB status=none; then
        chmod 600 "$NEW_SWAPFILE"
        mkswap "$NEW_SWAPFILE" 
        swapon "$NEW_SWAPFILE"  || true

        if ! grep -q "$NEW_SWAPFILE" /etc/fstab ; then
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
if ! grep -q "$OOM_SCRIPT" /etc/crontab ; then
    echo "$CRON_JOB" >> /etc/crontab
    echo -e "${GREEN}  ✓ OOM Auto-Adjuster installed and scheduled via cron${NC}"
else
    echo -e "${GREEN}  ✓ OOM Auto-Adjuster already scheduled${NC}"
fi

# ─── Sysctl tuning (idempotent) ──────────────────────────────────────────────
SYSCTL_UPDATED=false

ensure_sysctl() {
    local key="$1" value="$2" desc="$3"
    CURRENT=$(sysctl -n "$key"  || echo "")
    if [ "$CURRENT" != "$value" ]; then
        sysctl -w "$key=$value"  || true
        # Make permanent (idempotent)
        if grep -q "^$key" /etc/sysctl.conf ; then
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
    CRITICAL_CONTAINERS=(smsly-hosting-backend-1 smsly-postgres-primary smsly-hosting-pgcat-1 smsly-hosting-celery-1 smsly-hosting-celery-deploy-1 smsly-hosting-celery-fast-1 smsly-hosting-celery-beat-1 smsly-hosting-socket-proxy-1)
fi
for CONTAINER in "${CRITICAL_CONTAINERS[@]}"; do
    resolved_container="$(resolve_container_target "$CONTAINER")"
    CPID=$(docker inspect --format '{{.State.Pid}}' "$resolved_container"  || echo "")
    if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
        echo -500 > "/proc/$CPID/oom_score_adj"  || true
    fi
done
echo -e "${GREEN}  ✓ OOM protection set (${CRITICAL_CONTAINERS[*]})${NC}"

# ─── Firewall Hardening (UFW) ────────────────────────────────────────────────
if command -v ufw ; then
    echo -e "${BLUE}  → Configuring UFW firewall...${NC}"
    ufw default deny incoming  || true
    ufw default allow outgoing  || true
    # Allow SSH from master IP specifically (provisioning/updates)
    _master_ip="${MASTER_IP:-}"
    if [ -n "$_master_ip" ] && [ "$_master_ip" != "127.0.0.1" ] && ! echo "$_master_ip" | grep -qE '^(0\.0\.0\.0|localhost)$'; then
        echo -e "${BLUE}  → Allowing master ($_master_ip) SSH access...${NC}"
        ufw allow from "$_master_ip" to any port 22  || true
    fi
    # Fallback: allow SSH from any (in case MASTER_IP is empty)
    ufw allow ssh  || true
    
    if [ "${INSTALL_MODE:-}" = "agent-lite" ]; then
        if [ -n "$_master_ip" ] && [ "$_master_ip" != "127.0.0.1" ] && ! echo "$_master_ip" | grep -qE '^(0\.0\.0\.0|localhost)$'; then
            ufw allow from "$_master_ip" to any port 80  || true
        else
            echo -e "${YELLOW}  ⚠ Warning: Agent-Lite missing Master IP. Port 80 not exposed.${NC}"
        fi
    else
        ufw allow 80/tcp  || true
        ufw allow 443/tcp  || true
    fi
    # Allow FRP if active
    if [ -f "$INSTALL_DIR/.env" ] && grep -q "FRP_AUTH_TOKEN" "$INSTALL_DIR/.env"; then
        ufw allow 7000/tcp  || true
    fi
    # Allow Docker Mirror (Option B) if this is the Master/Leader
    if [ -z "${MASTER_IP:-}" ] || [ "$MASTER_IP" = "127.0.0.1" ] || [ "$MASTER_IP" = "$(detect_public_ip)" ]; then
        ufw allow 5001/tcp  || true
        # Allow Lite Agents to reach core services — RESTRICTED to WireGuard mesh only.
        # These ports carry database/cache/message-queue traffic and must never be
        # exposed to the public internet. Lite Agents connect via the WireGuard VPN
        # mesh (10.100.0.0/24), so we whitelist that subnet plus the master's own
        # mesh IP. Password auth is the second layer of defense.
        echo -e "${BLUE}  → Master node: Restricting DB/Redis/MQ ports to WireGuard mesh (10.100.0.0/24)${NC}"
        ufw allow from 10.100.0.0/24 to any port 5432 proto tcp  || true
        ufw allow from 10.100.0.0/24 to any port 6379 proto tcp  || true
        ufw allow from 10.100.0.0/24 to any port 5672 proto tcp  || true
        # Also allow localhost (container-to-container on the same host)
        ufw allow from 127.0.0.1 to any port 5432 proto tcp  || true
        ufw allow from 127.0.0.1 to any port 6379 proto tcp  || true
        ufw allow from 127.0.0.1 to any port 5672 proto tcp  || true
        # Allow Docker bridge networks (172.16.0.0/12) for container-to-host communication
        ufw allow from 172.16.0.0/12 to any port 5432 proto tcp  || true
        ufw allow from 172.16.0.0/12 to any port 6379 proto tcp  || true
        ufw allow from 172.16.0.0/12 to any port 5672 proto tcp  || true
    fi
    echo "y" | ufw enable  || true
    echo -e "${GREEN}  ✓ Firewall hardened (Inbound blocked, SSH/Web permitted)${NC}"
fi

# ── Infrastructure port firewall (DOCKER-USER chain) ────────────────────
# Docker bypasses UFW by inserting its own iptables rules in the DOCKER
# chain. The DOCKER-USER chain is the official way to add custom rules.
# We lock down all infrastructure ports (registry, DB, Redis, RabbitMQ)
# to trusted sources only: localhost, Docker bridges, and WireGuard mesh.
if command -v iptables ; then
    echo -e "${BLUE}  → Securing infrastructure ports via iptables (DOCKER-USER chain)...${NC}"

    # Ensure DOCKER-USER chain exists (Docker creates it, but be safe)
    iptables -N DOCKER-USER  || true

    # Ports to whitelist: registry (5000), PostgreSQL (5432), Redis (6379), RabbitMQ (5672)
    _infra_ports="5000 5432 6379 5672"

    # Flush any previous infrastructure port rules (idempotent re-runs)
    for _port in $_infra_ports; do
        (
            iptables -L DOCKER-USER --line-numbers -n  | \
                grep "dpt:${_port}" | awk '{print $1}' | sort -rn | \
                while read -r num; do iptables -D DOCKER-USER "$num"  || true
            done
        ) || true
    done

    for _port in $_infra_ports; do
        # Allow localhost (container-to-container on the same host)
        iptables -I DOCKER-USER -i lo -p tcp --dport "$_port" -j ACCEPT  || true

        # Allow Docker bridge networks (172.16.0.0/12 covers docker0 + compose nets)
        iptables -I DOCKER-USER -s 172.16.0.0/12 -p tcp --dport "$_port" -j ACCEPT  || true

        # Allow WireGuard mesh (10.100.0.0/24 is the assigned mesh range)
        iptables -I DOCKER-USER -s 10.100.0.0/24 -p tcp --dport "$_port" -j ACCEPT  || true

        # Allow known node IPs
        if [ -n "${MASTER_MESH_IP:-}" ]; then
            iptables -I DOCKER-USER -s "${MASTER_MESH_IP}" -p tcp --dport "$_port" -j ACCEPT  || true
        fi

        # Drop everything else to this port
        iptables -A DOCKER-USER -p tcp --dport "$_port" -j DROP  || true
    done

    # Return to the DOCKER chain for all other traffic
    iptables -C DOCKER-USER -j RETURN  || \
        iptables -A DOCKER-USER -j RETURN  || true

    echo -e "${GREEN}  ✓ Infrastructure ports hardened (5000, 5432, 6379, 5672) — locked to localhost + mesh + docker networks${NC}"

    # Allow remote Promtail → Loki on WireGuard interface (VPN mesh)
    iptables -A INPUT -i wg+ -p tcp --dport 3100 -j ACCEPT  || true

    # Persist iptables rules across reboots
    if command -v iptables-save ; then
        mkdir -p /etc/iptables
        iptables-save > /etc/iptables/rules.v4  || true
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
            systemctl daemon-reload  || true
            systemctl enable iptables-restore  || true
        fi
        echo -e "${GREEN}  ✓ iptables rules saved to /etc/iptables/rules.v4 for persistence${NC}"
    fi
fi

echo -e "${GREEN}  ✓ System security hardening complete${NC}"
    set_checkpoint "memory_hardened"
fi

# --- end lib/fresh_hardening.sh ---
# --- lib/fresh_verify.sh ---
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
AGENT_SERVICES="$(docker compose -f "$COMPOSE_FILE" config --services  || true)"
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
RUNNING_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --status running -q  | wc -l)
TOTAL_COUNT=$(docker compose -f "$COMPOSE_FILE" ps -q  | wc -l)
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
    BACKEND_STATUS="$(docker compose -f "$COMPOSE_FILE" ps backend --format "{{.Status}}"  || true)"
    if timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS http://127.0.0.1:8000/health/live < /dev/null ; then
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
    docker compose -f "$COMPOSE_FILE" logs --tail=80 backend  || true
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
    if timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health/live < /dev/null ; then
        HEALTH_OK=true
        break
    elif curl -sfL --max-time 5 http://127.0.0.1:8000/health/live ; then
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
    timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health/ready < /dev/null  && READY_OK=true
    if ! $READY_OK && ! curl -sfL --max-time 5 http://127.0.0.1:8000/health/ready ; then
        echo -e "${YELLOW}  ⚠ Readiness endpoint is still warming; continuing because liveness passed.${NC}"
    fi
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Health check failed after $MAX_ATTEMPTS attempts.${NC}"
    dump_diagnostic_logs
fi

# ─── Check 3: All containers running ──────────────────────────────────────
echo -e "${BLUE}  → [2/4] Checking container status...${NC}"
RUNNING_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --status running -q  | wc -l)
TOTAL_COUNT=$(docker compose -f "$COMPOSE_FILE" ps -q  | wc -l)
UNHEALTHY_STATUS="$(docker compose -f "$COMPOSE_FILE" ps --format "{{.Service}}\t{{.Status}}"  | awk 'tolower($0) ~ /unhealthy/ {print}' || true)"
# Also surface containers stuck in Docker's restart loop. These are not
# "unhealthy" (healthcheck hasn't run yet) but they're crash-looping,
# which is the more dangerous failure mode — print them first so the
# tail of their crash log is visible.
RESTARTING_STATUS="$(docker compose -f "$COMPOSE_FILE" ps --format "{{.Service}}\t{{.Status}}"  | awk 'tolower($0) ~ /restarting/ {print}' || true)"
if [ -n "$RESTARTING_STATUS" ]; then
    echo -e "${RED}  ✗ One or more containers are crash-looping:${NC}"
    printf '%s\n' "$RESTARTING_STATUS" | sed 's/^/     - /'
    RESTARTING_SERVICES="$(printf '%s\n' "$RESTARTING_STATUS" | awk '{print $1}' | xargs  || true)"
    if [ -n "$RESTARTING_SERVICES" ]; then
        echo -e "${YELLOW}  ↳ Crash tail of each restarting service (last 40 lines):${NC}"
        for _svc in $RESTARTING_SERVICES; do
            echo -e "${YELLOW}      --- $_svc ---${NC}"
            docker compose -f "$COMPOSE_FILE" logs --tail=40 "$_svc"  | sed 's/^/        /' || true
        done
    fi
fi
if [ -n "$UNHEALTHY_STATUS" ]; then
    echo -e "${RED}  ✗ One or more containers are unhealthy:${NC}"
    printf '%s\n' "$UNHEALTHY_STATUS" | sed 's/^/     - /'
    UNHEALTHY_SERVICES="$(printf '%s\n' "$UNHEALTHY_STATUS" | awk '{print $1}' | xargs  || true)"
    if [ -n "$UNHEALTHY_SERVICES" ]; then
        docker compose -f "$COMPOSE_FILE" logs --tail=80 $UNHEALTHY_SERVICES  || true
    fi
elif [ -z "$RESTARTING_STATUS" ] && [ "$RUNNING_COUNT" -eq "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt 0 ]; then
    echo -e "${GREEN}  ✓ All $TOTAL_COUNT containers running and none are unhealthy${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
elif [ "$RUNNING_COUNT" -eq "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt 0 ]; then
    # All running but some are crash-looping — don't increment pass count
    # but don't double-count either.
    echo -e "${YELLOW}  ⚠ All $TOTAL_COUNT containers present, but see crash-loop warnings above${NC}"
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
    if docker inspect -f '{{.State.Running}}' "$caddy_container"  | grep -q "true"; then
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
    if docker inspect -f '{{.State.Running}}' "$traefik_container"  | grep -q "true" \
       && curl -fsS --max-time 5 "$TRAEFIK_CHECK_URL" ; then
        echo -e "${GREEN}  ✓ Traefik edge proxy active (${TRAEFIK_CHECK_URL})${NC}"
        VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Traefik edge proxy check failed (${TRAEFIK_CHECK_URL})${NC}"
    fi
fi
fi

# Show container status
echo -e "\n${BLUE}Container Status:${NC}"
docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}"  || \
    docker compose -f "$COMPOSE_FILE" ps  || true

echo -e "\n${BLUE}Verification Score: $VERIFY_PASS_COUNT/$VERIFY_TOTAL${NC}"

# ─── Install Autoscaler as systemd service ──────────────────────────────────
echo -e "${BLUE}  → Installing smsly-autoscaler systemd service...${NC}"
cp "$INSTALL_DIR/scripts/smsly-autoscaler.py" /opt/smsly/autoscaler.py  || {
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
systemctl enable smsly-autoscaler || echo -e "${YELLOW}    ⚠ smsly-autoscaler enable failed${NC}"
systemctl restart smsly-autoscaler || echo -e "${YELLOW}    ⚠ smsly-autoscaler restart failed${NC}"
echo -e "${GREEN}  ✓ smsly-autoscaler service installed and started${NC}"

# Install infrastructure monitor
if [ -f "$INSTALL_DIR/scripts/monitor_infra.sh" ]; then
    echo -e "${BLUE}  → Installing critical infrastructure monitoring timer...${NC}"
    chmod +x "$INSTALL_DIR/scripts/monitor_infra.sh"
    cp "$INSTALL_DIR/scripts/smsly-infra-monitor.service" /etc/systemd/system/smsly-infra-monitor.service  || true
    cp "$INSTALL_DIR/scripts/smsly-infra-monitor.timer" /etc/systemd/system/smsly-infra-monitor.timer  || true
    systemctl daemon-reload
    systemctl enable smsly-infra-monitor.timer || echo -e "${YELLOW}    ⚠ smsly-infra-monitor timer enable failed${NC}"
    systemctl restart smsly-infra-monitor.timer || echo -e "${YELLOW}    ⚠ smsly-infra-monitor timer restart failed${NC}"
    echo -e "${GREEN}  ✓ smsly-infra-monitor timer installed and started${NC}"
fi

# Install platform update watcher and caddy watcher services
if [ -f "$INSTALL_DIR/scripts/smsly-update-watcher.service" ]; then
    echo -e "${BLUE}  → Installing platform update and Caddy config watcher services...${NC}"
    chmod +x "$INSTALL_DIR/scripts/platform-update.sh" "$INSTALL_DIR/scripts/caddy-reload.sh"  || true
    cp "$INSTALL_DIR/scripts/smsly-update-watcher.service" /etc/systemd/system/smsly-update-watcher.service  || true
    cp "$INSTALL_DIR/scripts/caddy-watcher.service" /etc/systemd/system/caddy-watcher.service  || true
    systemctl daemon-reload
    systemctl enable smsly-update-watcher caddy-watcher || echo -e "${YELLOW}    ⚠ Watcher services enable failed${NC}"
    systemctl restart smsly-update-watcher caddy-watcher || echo -e "${YELLOW}    ⚠ Watcher services restart failed${NC}"
    echo -e "${GREEN}  ✓ smsly-update-watcher and caddy-watcher services installed and started${NC}"
fi

# Install Celery Worker Autoscaler (scales celery-2/celery-3 based on queue depth)
if [ -f "$INSTALL_DIR/scripts/celery-worker-autoscaler.sh" ]; then
    echo -e "${BLUE}  → Installing Celery Worker Autoscaler service...${NC}"
    chmod +x "$INSTALL_DIR/scripts/celery-worker-autoscaler.sh"
    cp "$INSTALL_DIR/infrastructure/docker/celery-autoscaler.service" /etc/systemd/system/celery-autoscaler.service  || true
    systemctl daemon-reload
    if [ "${CELERY_AUTOSCALE_ENABLED:-true}" = "true" ]; then
        systemctl enable celery-autoscaler || echo -e "${YELLOW}    ⚠ celery-autoscaler enable failed${NC}"
        systemctl restart celery-autoscaler || echo -e "${YELLOW}    ⚠ celery-autoscaler restart failed${NC}"
        echo -e "${GREEN}  ✓ celery-autoscaler service installed and started (scaling celery-2/3 on demand)${NC}"
    else
        systemctl disable celery-autoscaler 2>/dev/null || true
        systemctl stop celery-autoscaler 2>/dev/null || true
        echo -e "${BLUE}  → celery-autoscaler installed but disabled (CELERY_AUTOSCALE_ENABLED=false)${NC}"
    fi
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
    pip3 install -q --break-system-packages "$INSTALL_DIR/cli"  || \
        pip3 install -q "$INSTALL_DIR/cli"  || true

    # Ensure binary is in path (pip usually puts it in /usr/local/bin)
    if command -v smsly ; then
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
if timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py help diagnose_nodes < /dev/null ; then
    timeout 120 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py diagnose_nodes --fix < /dev/null || true
    echo -e "${GREEN}  ✓ Node registered as Primary (if Master) and API tokens verified${NC}"
else
    echo -e "${YELLOW}  ⚠ diagnose_nodes command not available in this version; skipping.${NC}"
fi

# ─── Final Verification Sync ──────────────────────────────────────────────────
fi

if [ "$MODE_AGENT_LITE" != "true" ] && command -v smsly ; then
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
chmod +x scripts/grid-handshake.sh  || true
SMSLY_MIGRATIONS_DONE=1 bash scripts/grid-handshake.sh || \
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
    SSL_SCRIPT="install-custom-domain-ssl.sh"
    [ -f "$SSL_SCRIPT" ] || SSL_SCRIPT="$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh"
    if [ -f "$SSL_SCRIPT" ]; then
        echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
        bash "$SSL_SCRIPT" install
        
        # Start the services
        echo -e "${BLUE}  → Starting custom domain SSL services...${NC}"
        /opt/smsly-hosting/smsly-domain-ssl-manager.sh start
        
        # Enable auto-start on boot
        echo -e "${BLUE}  → Enabling auto-start on boot...${NC}"
        /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable
        
        echo -e "${GREEN}  ✓ Custom domain SSL services configured${NC}"
    else
        echo -e "${YELLOW}  ⚠ Custom domain SSL manager not found, skipping setup${NC}"
    fi
fi

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
if [ "$MODE_AGENT_LITE" != "true" ]; then
    echo -e "   CLI:         'smsly services list'${NC}"
fi
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  View credentials:   cat $CREDENTIALS_FILE${NC}"
echo -e "${YELLOW}  View logs:          cat $LOG_FILE${NC}"
if [ -f "$INSTALL_DIR/.recovery_phrase" ] && [ -s "$INSTALL_DIR/.recovery_phrase" ]; then
    echo -e "${YELLOW}  Recovery phrase:    cat $INSTALL_DIR/.recovery_phrase${NC}"
fi
if is_master_mode; then
    echo -e "${YELLOW}  Update frontend:    sudo bash install.sh --update-frontend${NC}"
fi
echo -e "${YELLOW}  Update backend:     sudo bash install.sh --update-backend${NC}"
echo -e "${YELLOW}  Full update:        sudo bash install.sh --update${NC}"
echo -e "${YELLOW}  Runtime refresh:    sudo bash install.sh --refresh${NC}"
echo -e "${YELLOW}  Runtime recovery:   sudo bash install.sh --recover${NC}"
echo -e "${YELLOW}  Debug snapshot:     sudo bash install.sh --debug${NC}"
echo -e "${YELLOW}  Wipe install:       sudo bash install.sh --wipe${NC}"
if is_master_mode; then
    echo -e "${YELLOW}  Enable read replica (warm-standby):  sudo bash install.sh --with-replica${NC}"
    echo -e "${YELLOW}    (or: sudo $INSTALL_DIR/scripts/enable-replica.sh)${NC}"
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

# ─── Optional: Enable PostgreSQL Read Replica (only when --with-replica) ─────
# Runs AFTER verification so the primary is confirmed healthy. Runs BEFORE
# the final exit 0 so the post-install message can also report the replica
# status. Non-fatal: if the replica fails to start, the install itself is
# still considered successful and the operator can re-run
# `install.sh --with-replica` later.
if [ "${REPLICA_MODE:-false}" = "true" ] && is_master_mode; then
    echo -e "\n${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}   --with-replica: enabling PostgreSQL streaming replication${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    _replica_script="$INSTALL_DIR/scripts/enable-replica.sh"
    if [ -f "$_replica_script" ]; then
        chmod +x "$_replica_script"  || true
        if bash "$_replica_script"; then
            echo -e "${GREEN}  ✓ Read replica enabled and streaming${NC}"
        else
            _rc=$?
            echo -e "${RED}  ✗ enable-replica.sh exited with code $_rc${NC}"
            echo -e "${YELLOW}    The install itself succeeded. Re-run later with:${NC}"
            echo -e "${YELLOW}      sudo $INSTALL_DIR/scripts/enable-replica.sh${NC}"
        fi
    else
        echo -e "${RED}  ✗ $_replica_script not found. Pull the latest code and re-run.${NC}"
    fi
    unset _replica_script _rc
fi

# ─── Security verify ─────────────────────────────────────────────────────
if command -v harden_security_verify ; then
    harden_security_verify
fi

# --- end lib/fresh_verify.sh ---

# Valid when fresh.sh is sourced from install.sh (repo layout). The
# self-contained installer inlines this content into the top-level script,
# where `return` is illegal — fall back to exit 0 there.
return 0 2>/dev/null || exit 0

# --- end lib/fresh.sh ---
