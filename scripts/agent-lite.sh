#!/bin/bash
# =============================================================================
# Grid Agent (Lite) — Install & Update Script
# =============================================================================
# A lightweight agent node that connects to a master Grid instance.
# Runs: backend, celery-worker, socket-proxy, traefik (no Caddy, no DB)
#
# Usage:
#   Install:  sudo bash scripts/agent-lite.sh
#   Update:   sudo bash scripts/agent-lite.sh --update
#   Update half (no build): sudo bash scripts/agent-lite.sh --update-half
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; YELLOW='\033[1;33m'; NC='\033[0m'
LOG_FILE="/var/log/smsly-agent-lite.log"

INSTALL_DIR="/opt/smsly-hosting"
COMPOSE_FILE="infrastructure/docker/docker-compose.agent-lite.yml"
COMPOSE_PATH="$INSTALL_DIR/$COMPOSE_FILE"
LOCK_FILE="/tmp/smsly-agent-lite.lock"
AGENT_SCRIPT_PATH="$INSTALL_DIR/scripts/agent-lite.sh"
ORIGINAL_ARGS=("$@")

# ─── Parse args ──────────────────────────────────────────────────────────────
UPDATE_MODE=""
for arg in "$@"; do
    case "$arg" in
        --update)      UPDATE_MODE="full" ;;
        --update-half) UPDATE_MODE="half" ;;
        --skip-git)    SKIP_GIT="true" ;;
        --help|-h)
            echo "Usage: sudo bash scripts/agent-lite.sh [--update|--update-half] [--skip-git]"
            echo "  (no args)      Fresh install"
            echo "  --update       Full update (rebuild with --no-cache)"
            echo "  --update-half  Quick update (restart only, no build)"
            echo "  --skip-git     Skip git pull (use local code as-is)"
            exit 0 ;;
    esac
done

# ─── Screen session guard ────────────────────────────────────────────────────
SCREEN_NAME="smsly-agent-lite"
if [ "${SMSLY_SKIP_SCREEN:-false}" != "true" ] && [ -z "${STY:-}" ] && [ -z "${TMUX:-}" ]; then
    if command -v screen &>/dev/null; then
        echo -e "${BLUE}  → Not in a screen session. Launching inside screen '${SCREEN_NAME}'...${NC}"
        if screen -dmS "$SCREEN_NAME" bash "$0" "$@" 2>/dev/null; then
            exec screen -r "$SCREEN_NAME"
        else
            echo -e "${YELLOW}  ⚠ Could not launch screen, continuing without it${NC}"
        fi
    fi
fi

# ─── Root check ──────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ERROR: This script must be run as root.${NC}"
    exit 1
fi

# ─── Lock file (prevent concurrent runs) ─────────────────────────────────────
cleanup_lock() {
    rm -rf "$LOCK_FILE" 2>/dev/null || true
}
trap cleanup_lock EXIT
if ! mkdir "$LOCK_FILE" 2>/dev/null; then
    if [ "${SMSLY_AGENT_LITE_REEXECED:-false}" = "true" ] && [ -d "$LOCK_FILE" ]; then
        echo -e "${YELLOW}  ⚠ Re-executed script reusing existing lock${NC}"
    elif [ -d "$LOCK_FILE" ] && [ -z "$(find "$LOCK_FILE" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then
        echo -e "${RED}✗ Another instance is already running (lock: $LOCK_FILE).${NC}"
        echo -e "${YELLOW}  If no other instance is running, remove it: rm -rf $LOCK_FILE${NC}"
        exit 1
    else
        echo -e "${YELLOW}  ⚠ Stale lock found (older than 30m). Removing and re-acquiring...${NC}"
        rm -rf "$LOCK_FILE" 2>/dev/null || true
        mkdir "$LOCK_FILE" 2>/dev/null || {
            echo -e "${RED}✗ Still cannot acquire lock after clearing stale one.${NC}"
            exit 1
        }
    fi
fi

# ─── Log setup ───────────────────────────────────────────────────────────────
exec > >(tee -a "$LOG_FILE") 2>&1

# ─── Directory guard ─────────────────────────────────────────────────────────
if [ ! -d "$INSTALL_DIR" ]; then
    echo -e "${RED}✗ $INSTALL_DIR not found. Run the master installer first.${NC}"
    exit 1
fi

# ─── .env guard ──────────────────────────────────────────────────────────────
if [ ! -f "$INSTALL_DIR/.env" ]; then
    echo -e "${RED}✗ .env not found at $INSTALL_DIR/.env${NC}"
    echo -e "${YELLOW}  The master installer must populate .env before running this script.${NC}"
    exit 1
fi
for _var in DATABASE_URL CELERY_BROKER_URL REDIS_URL; do
    if ! grep -q "^${_var}=" "$INSTALL_DIR/.env" 2>/dev/null; then
        echo -e "${RED}✗ Missing $_var in .env — run the master provisioning flow first.${NC}"
        exit 1
    fi
done

# ─── Compose file guard ──────────────────────────────────────────────────────
if [ ! -f "$COMPOSE_PATH" ]; then
    echo -e "${RED}✗ Compose file not found: $COMPOSE_PATH${NC}"
    exit 1
fi

echo -e "${BLUE}═══════════════════════════════════════${NC}"
echo -e "${BLUE}  Grid Agent (Lite) — $([ -n "$UPDATE_MODE" ] && echo 'Update' || echo 'Install')${NC}"
echo -e "${BLUE}  Log: $LOG_FILE${NC}"
echo -e "${BLUE}═══════════════════════════════════════${NC}"

# =============================================================================
# HELPERS
# =============================================================================
ensure_networks() {
    docker network inspect smsly-net >/dev/null 2>&1 || {
        echo -e "${BLUE}  → Creating smsly-net...${NC}"
        docker network create smsly-net >/dev/null 2>&1 || true
    }
    docker network inspect socket-proxy >/dev/null 2>&1 || {
        echo -e "${BLUE}  → Creating socket-proxy...${NC}"
        docker network create --driver bridge --internal socket-proxy >/dev/null 2>&1 || true
    }
}

ensure_backups_volume() {
    # The lite agent's backend + celery workers need a persistent
    # backups_data volume so:
    #   1. ServiceBackup downloads write the downloaded tarballs to
    #      a path that survives container restarts.
    #   2. The /api/v1/servers/{id}/download-key/ endpoint's
    #      key file persists across reinstalls (otherwise the user
    #      has to re-prompt every time the agent rebuilds).
    if ! docker volume inspect smsly-hosting_backups_data >/dev/null 2>&1; then
        echo -e "${BLUE}  → Creating backups_data volume...${NC}"
        docker volume create --name smsly-hosting_backups_data >/dev/null 2>&1 || true
    fi
}

detect_public_ip() {
    # Public IP detection is best-effort. If the network is firewalled
    # (e.g. inside a corporate proxy), we fall back to the
    # first non-loopback interface IP. ALLOWED_HOSTS is computed
    # from this so a missing public IP is not fatal.
    local ip=""
    # Try ifconfig.me first (most reliable on consumer VPSes)
    ip="$(curl -s --max-time 3 -4 https://ifconfig.me 2>/dev/null | tr -dc '0-9.')"
    if [ -z "$ip" ]; then
        # Fall back to icanhazip
        ip="$(curl -s --max-time 3 -4 https://icanhazip.com 2>/dev/null | tr -dc '0-9.')"
    fi
    if [ -z "$ip" ]; then
        # Last resort: ask the kernel
        ip="$(hostname -I 2>/dev/null | awk '{print $1}' | tr -dc '0-9.')"
    fi
    # Validate the format. An empty / malformed value causes
    # problems later (e.g. trusted-host / iptables rules).
    if [ -n "$ip" ] && ! echo "$ip" | grep -qE '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
        ip=""
    fi
    echo "$ip"
}

# Helper: do a health probe on the master over the VPN or
# public IP. Used by the final "is the node fully functional"
# check below. Tries each candidate URL in order.
probe_master_health() {
    local timeout="${1:-5}"
    local ip="${MASTER_IP:-}"
    local mesh_ip="${MASTER_MESH_IP:-}"
    local url
    for url in \
        "http://${mesh_ip}:8000/health/live" \
        "http://${mesh_ip}/health/live" \
        "http://${ip}:8000/health/live" \
        "http://${ip}/health/live"; do
        if [ -n "$url" ] && [ -n "${url#http://}" ] && curl -fsS --max-time "$timeout" "$url" >/dev/null 2>&1; then
            echo "$url"
            return 0
        fi
    done
    return 1
}

gen_hex_secret() {
    local bytes="${1:-16}"
    python3 -c "import secrets; print(secrets.token_hex(${bytes}))" 2>/dev/null || openssl rand -hex "$bytes"
}

env_get_value() {
    local env_file="$1"
    local var_name="$2"
    [ -f "$env_file" ] || return 0
    grep -m1 "^${var_name}=" "$env_file" 2>/dev/null | cut -d= -f2- | sed "s/^\"//;s/\"$//;s/^'//;s/'$//" || true
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
        continue
    updated.append(line)

if not found:
    updated.append(f"{key}={value}")

env_path.write_text("\n".join(updated) + "\n")
PY
}

normalize_registry_host() {
    local value="${1:-}"
    value="${value#http://}"
    value="${value#https://}"
    value="${value%%/*}"
    value="${value%/}"
    printf '%s' "$value"
}

ensure_agent_env_defaults() {
    local env_file="$INSTALL_DIR/.env"
    local master_ip master_mesh_ip registry_host
    local rabbitmq_password redis_password

    master_ip="$(env_get_value "$env_file" "MASTER_IP")"
    master_mesh_ip="$(env_get_value "$env_file" "MASTER_MESH_IP")"

    if [ -n "$master_mesh_ip" ]; then
        registry_host="$master_mesh_ip"
    else
        registry_host="$master_ip"
    fi
    if [ -n "$registry_host" ]; then
        env_set_value "$env_file" "CONTAINER_REGISTRY_URL" "${registry_host}:5000"
    fi

    rabbitmq_password="$(env_get_value "$env_file" "RABBITMQ_PASSWORD")"
    if [ -z "$rabbitmq_password" ]; then
        rabbitmq_password="$(gen_hex_secret 16)"
        echo -e "${BLUE}  -> Generated missing local RABBITMQ_PASSWORD${NC}"
    fi
    env_set_value "$env_file" "RABBITMQ_PASSWORD" "$rabbitmq_password"
    env_set_value "$env_file" "RABBITMQ_DEFAULT_USER" "smsly_user"
    env_set_value "$env_file" "RABBITMQ_DEFAULT_PASS" "$rabbitmq_password"
    env_set_value "$env_file" "RABBITMQ_HOST" "rabbitmq"
    env_set_value "$env_file" "RABBITMQ_PORT" "5672"
    env_set_value "$env_file" "CELERY_BROKER_URL" "amqp://smsly_user:${rabbitmq_password}@rabbitmq:5672//"

    redis_password="$(env_get_value "$env_file" "REDIS_PASSWORD")"
    if [ -z "$redis_password" ]; then
        redis_password="$(gen_hex_secret 16)"
        echo -e "${BLUE}  -> Generated missing local REDIS_PASSWORD${NC}"
    fi
    env_set_value "$env_file" "REDIS_PASSWORD" "$redis_password"
    env_set_value "$env_file" "REDIS_HOST" "redis"
    env_set_value "$env_file" "REDIS_PORT" "6379"
    env_set_value "$env_file" "REDIS_URL" "redis://:${redis_password}@redis:6379/0"
    env_set_value "$env_file" "MODE" "agent"
    env_set_value "$env_file" "SMSLY_DISABLE_LOCAL_SERVICES" "false"

    # Sync FIELD_ENCRYPTION_KEY from master seed (critical: must match master
    # for shared-database encrypted fields to decrypt correctly on both nodes)
    local master_encryption_key
    master_encryption_key="$(env_get_value "$env_file" "MASTER_FIELD_ENCRYPTION_KEY")"
    if [ -z "$master_encryption_key" ]; then
        master_encryption_key="$(env_get_value "$env_file" "FIELD_ENCRYPTION_KEY")"
    fi
    if [ -n "$master_encryption_key" ]; then
        local current_key
        current_key="$(env_get_value "$env_file" "FIELD_ENCRYPTION_KEY")"
        if [ "$current_key" != "$master_encryption_key" ]; then
            env_set_value "$env_file" "FIELD_ENCRYPTION_KEY" "$master_encryption_key"
            echo -e "${BLUE}  -> Synced FIELD_ENCRYPTION_KEY from master${NC}"
        fi
    fi

    # Automatically set and update ALLOWED_HOSTS
    local allowed_hosts current_ips public_ip new_hosts
    allowed_hosts="$(env_get_value "$env_file" "ALLOWED_HOSTS")"
    current_ips="$(hostname -I 2>/dev/null | tr -s ' ' ',' | sed 's/,$//')"
    public_ip="$(detect_public_ip)"

    new_hosts="localhost,127.0.0.1,backend,smsly-hosting-backend-1,redis,rabbitmq,traefik,socket-proxy,agent-registrar"
    [ -n "$current_ips" ] && new_hosts="${new_hosts},${current_ips}"
    [ -n "$public_ip" ] && new_hosts="${new_hosts},${public_ip}"

    if [ -z "$allowed_hosts" ]; then
        env_set_value "$env_file" "ALLOWED_HOSTS" "$new_hosts"
        echo -e "${BLUE}  -> Set default ALLOWED_HOSTS for agent${NC}"
    else
        local merged_hosts="${allowed_hosts},${new_hosts}"
        merged_hosts="$(python3 -c 'import sys; print(",".join(dict.fromkeys([x.strip() for x in sys.argv[1].split(",") if x.strip()])))' "$merged_hosts" 2>/dev/null || echo "$merged_hosts")"
        env_set_value "$env_file" "ALLOWED_HOSTS" "$merged_hosts"
        echo -e "${BLUE}  -> Updated ALLOWED_HOSTS with current node IPs${NC}"
    fi

    # Make sure SMSLY_NODE_ID and SMSLY_NODE_QUEUE are present —
    # the celery-worker and the agent-registrar both need them.
    if [ -z "$(env_get_value "$env_file" "SMSLY_NODE_ID")" ]; then
        echo -e "${YELLOW}  ⚠ SMSLY_NODE_ID is not set in .env — the agent-registrar will not be able to report to master.${NC}"
        echo -e "${YELLOW}    Set it to the UUID of this server's ManagedServer row.${NC}"
    fi
    if [ -z "$(env_get_value "$env_file" "GATEWAY_SECRET")" ]; then
        echo -e "${YELLOW}  ⚠ GATEWAY_SECRET is not set in .env — the agent-registrar will not be able to authenticate.${NC}"
    fi
}

configure_docker_registry_trust() {
    local env_file="$INSTALL_DIR/.env"
    local daemon_json="/etc/docker/daemon.json"
    local master_ip master_mesh_ip registry_url registry_host
    local registries=()
    local mirrors=()

    command -v docker >/dev/null 2>&1 || return 0

    master_ip="$(env_get_value "$env_file" "MASTER_IP")"
    master_mesh_ip="$(env_get_value "$env_file" "MASTER_MESH_IP")"
    registry_url="$(env_get_value "$env_file" "CONTAINER_REGISTRY_URL")"

    add_registry() {
        local raw="${1:-}"
        raw="$(normalize_registry_host "$raw")"
        [ -z "$raw" ] && return 0
        if [[ "$raw" != *:* ]]; then
            raw="${raw}:5000"
        fi
        registries+=("$raw")
    }

    add_registry "$registry_url"
    [ -n "$master_mesh_ip" ] && add_registry "${master_mesh_ip}:5000"
    if [ -n "$master_ip" ]; then
        add_registry "${master_ip}:5000"
        add_registry "${master_ip}:5001"
        mirrors+=("http://${master_ip}:5001")
    fi

    [ "${#registries[@]}" -eq 0 ] && return 0

    echo -e "${BLUE}  -> Configuring Docker registry trust for agent pulls...${NC}"
    mkdir -p /etc/docker
    [ -f "$daemon_json" ] || printf '{}\n' > "$daemon_json"

    local trust_payload mirror_payload status
    trust_payload="$(printf '%s\n' "${registries[@]}")"
    mirror_payload="$(printf '%s\n' "${mirrors[@]}")"

    set +e
    SMSLY_REGISTRY_TRUST="$trust_payload" SMSLY_REGISTRY_MIRRORS="$mirror_payload" python3 - "$daemon_json" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
raw = path.read_text().strip() if path.exists() else ""
try:
    data = json.loads(raw) if raw else {}
except json.JSONDecodeError as exc:
    print(f"Invalid {path}: {exc}", file=sys.stderr)
    sys.exit(1)

if not isinstance(data, dict):
    print(f"Invalid {path}: top-level JSON must be an object", file=sys.stderr)
    sys.exit(1)

changed = False

def merge_list(key, values):
    global changed
    if not values:
        return
    current = data.get(key)
    if not isinstance(current, list):
        current = []
        data[key] = current
        changed = True
    for value in values:
        if value and value not in current:
            current.append(value)
            changed = True

trust = [item.strip() for item in os.environ.get("SMSLY_REGISTRY_TRUST", "").splitlines() if item.strip()]
mirrors = [item.strip() for item in os.environ.get("SMSLY_REGISTRY_MIRRORS", "").splitlines() if item.strip()]

merge_list("insecure-registries", trust)
merge_list("registry-mirrors", mirrors)

if changed:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    sys.exit(10)
sys.exit(0)
PY
    status=$?
    set -e

    if [ "$status" -eq 10 ]; then
        echo -e "${BLUE}  -> Docker registry trust changed; restarting Docker...${NC}"
        if command -v systemctl >/dev/null 2>&1; then
            systemctl restart docker
        else
            service docker restart
        fi
        for _ in $(seq 1 30); do
            docker info >/dev/null 2>&1 && {
                echo -e "${GREEN}  OK Docker restarted with registry trust${NC}"
                return 0
            }
            sleep 2
        done
        echo -e "${RED}ERROR: Docker did not become ready after registry trust update${NC}"
        exit 1
    elif [ "$status" -eq 0 ]; then
        registry_host="$(normalize_registry_host "$registry_url")"
        echo -e "${GREEN}  OK Docker registry trust already configured${registry_host:+ ($registry_host)}${NC}"
    else
        echo -e "${RED}ERROR: Failed to update Docker registry trust${NC}"
        exit 1
    fi
    docker_login
}

docker_login() {
    local env_file="$INSTALL_DIR/.env"
    local registry="${CONTAINER_REGISTRY_URL:-$(env_get_value "$env_file" "CONTAINER_REGISTRY_URL" 2>/dev/null || echo "")}"
    local user="${REGISTRY_USER:-$(env_get_value "$env_file" "REGISTRY_USER" 2>/dev/null || echo "smsly-registry")}"
    local pass="${REGISTRY_PASSWORD:-$(env_get_value "$env_file" "REGISTRY_PASSWORD" 2>/dev/null || echo "")}"
    [ -z "$registry" ] && registry="127.0.0.1:5000"
    [ -z "$pass" ] && return 0
    echo "$pass" | docker login "$registry" -u "$user" --password-stdin >/dev/null 2>&1 || true
}

wait_for_local_rabbitmq() {
    local timeout="${1:-120}"
    local elapsed=0

    while [ "$elapsed" -lt "$timeout" ]; do
        if docker compose -f "$COMPOSE_PATH" exec -T rabbitmq rabbitmq-diagnostics -q check_running >/dev/null 2>&1; then
            echo -e "${GREEN}  OK Local RabbitMQ is ready${NC}"
            return 0
        fi
        sleep 3
        elapsed=$((elapsed + 3))
    done

    echo -e "${RED}ERROR: Local RabbitMQ did not become ready after ${timeout}s${NC}"
    docker compose -f "$COMPOSE_PATH" logs --tail=80 rabbitmq 2>/dev/null || true
    return 1
}

sync_local_rabbitmq_password() {
    local env_file="$INSTALL_DIR/.env"
    local rabbitmq_user rabbitmq_password

    rabbitmq_user="$(env_get_value "$env_file" "RABBITMQ_DEFAULT_USER")"
    rabbitmq_user="${rabbitmq_user:-smsly_user}"
    rabbitmq_password="$(env_get_value "$env_file" "RABBITMQ_PASSWORD")"
    rabbitmq_password="${rabbitmq_password:-$(env_get_value "$env_file" "RABBITMQ_DEFAULT_PASS")}"

    if [ -z "$rabbitmq_password" ]; then
        echo -e "${RED}ERROR: RABBITMQ_PASSWORD is still empty after env repair${NC}"
        exit 1
    fi

    wait_for_local_rabbitmq 120 || exit 1

    if docker compose -f "$COMPOSE_PATH" exec -T rabbitmq rabbitmqctl authenticate_user "$rabbitmq_user" "$rabbitmq_password" >/dev/null 2>&1; then
        echo -e "${GREEN}  OK Local RabbitMQ password already matches .env${NC}"
        return 0
    fi

    echo -e "${BLUE}  -> Syncing local RabbitMQ password for ${rabbitmq_user}...${NC}"
    docker compose -f "$COMPOSE_PATH" exec -T rabbitmq rabbitmqctl add_user "$rabbitmq_user" "$rabbitmq_password" >/dev/null 2>&1 || true
    docker compose -f "$COMPOSE_PATH" exec -T rabbitmq rabbitmqctl change_password "$rabbitmq_user" "$rabbitmq_password" >/dev/null
    docker compose -f "$COMPOSE_PATH" exec -T rabbitmq rabbitmqctl set_user_tags "$rabbitmq_user" administrator >/dev/null
    docker compose -f "$COMPOSE_PATH" exec -T rabbitmq rabbitmqctl set_permissions -p / "$rabbitmq_user" ".*" ".*" ".*" >/dev/null

    if docker compose -f "$COMPOSE_PATH" exec -T rabbitmq rabbitmqctl authenticate_user "$rabbitmq_user" "$rabbitmq_password" >/dev/null 2>&1; then
        echo -e "${GREEN}  OK Local RabbitMQ password synced${NC}"
        return 0
    fi

    echo -e "${RED}ERROR: Local RabbitMQ password sync failed${NC}"
    exit 1
}

fix_permissions() {
    local env_file="$INSTALL_DIR/.env"
    [ ! -f "$env_file" ] && return 0
    chown root:1000 "$env_file" 2>/dev/null || true
    chmod 664 "$env_file" 2>/dev/null || true
    # Also fix caddy-config if it exists (for shared volumes with master)
    [ -d "$INSTALL_DIR/caddy-config" ] && chown -R 1000:1000 "$INSTALL_DIR/caddy-config" 2>/dev/null || true
}

pull_latest_code() {
    [ "${SKIP_GIT:-false}" = "true" ] && { echo -e "${BLUE}  → Skipping git pull (--skip-git)${NC}"; return 0; }
    echo -e "${BLUE}  → Pulling latest code...${NC}"
    cd "$INSTALL_DIR"
    git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true
    local script_checksum_before=""
    local script_checksum_after=""
    [ -f "$AGENT_SCRIPT_PATH" ] && script_checksum_before="$(sha256sum "$AGENT_SCRIPT_PATH" 2>/dev/null | awk '{print $1}' || true)"
    # Stash any local changes to avoid pull conflicts
    local stash_before stash_after stash_created
    stash_created="false"
    stash_before="$(git stash list 2>/dev/null | wc -l | tr -d ' ')"
    git stash push -m "smsly-agent-lite-update" >/dev/null 2>&1 || true
    stash_after="$(git stash list 2>/dev/null | wc -l | tr -d ' ')"
    [ "$stash_after" != "$stash_before" ] && stash_created="true"
    if git pull --force origin main 2>/dev/null; then
        echo -e "${GREEN}  ✓ Code updated${NC}"
    else
        echo -e "${YELLOW}  ⚠ Git pull failed, continuing with local code${NC}"
    fi
    if [ "$stash_created" = "true" ]; then
        # Pop stash — if it fails (conflicts), leave stashed and warn
        git stash pop 2>/dev/null || echo -e "${YELLOW}  ⚠ Local changes stashed (git stash list)${NC}"
    fi
    [ -f "$AGENT_SCRIPT_PATH" ] && script_checksum_after="$(sha256sum "$AGENT_SCRIPT_PATH" 2>/dev/null | awk '{print $1}' || true)"
    if [ -n "$UPDATE_MODE" ] && [ "${SMSLY_AGENT_LITE_REEXECED:-false}" != "true" ] \
       && [ -n "$script_checksum_before" ] && [ -n "$script_checksum_after" ] \
       && [ "$script_checksum_before" != "$script_checksum_after" ]; then
        echo -e "${BLUE}  -> Agent script changed during update; restarting with the new script...${NC}"
        export SMSLY_AGENT_LITE_REEXECED="true"
        export SKIP_GIT="true"
        exec bash "$AGENT_SCRIPT_PATH" "${ORIGINAL_ARGS[@]}"
    fi
}

run_migrations() {
    # Check if backend is running before attempting migrations
    if ! docker compose -f "$COMPOSE_PATH" ps --status running backend 2>/dev/null | grep -q "Up"; then
        echo -e "${YELLOW}  ⚠ Backend not running, skipping migrations${NC}"
        return 0
    fi
    echo -e "${BLUE}  → Running migrations...${NC}"
    if docker compose -f "$COMPOSE_PATH" exec -T backend python manage.py migrate --noinput 2>/dev/null; then
        echo -e "${GREEN}  ✓ Migrations complete${NC}"
    else
        echo -e "${YELLOW}  ⚠ Migration failed, retrying in 10s...${NC}"
        sleep 10
        docker compose -f "$COMPOSE_PATH" exec -T backend python manage.py migrate --noinput 2>/dev/null && \
            echo -e "${GREEN}  ✓ Migrations complete on retry${NC}" || \
            echo -e "${YELLOW}  ⚠ Migrations still failing (non-fatal, will retry on next update)${NC}"
    fi
    docker compose -f "$COMPOSE_PATH" exec -T --user root backend python manage.py collectstatic --noinput || echo -e "${YELLOW}    ⚠ collectstatic failed${NC}"
}

wait_for_backend() {
    local timeout="${1:-60}"
    local interval="${2:-3}"
    echo -e "${BLUE}  → Waiting for backend health (up to ${timeout}s)...${NC}"
    local elapsed=0
    while [ "$elapsed" -lt "$timeout" ]; do
        if docker compose -f "$COMPOSE_PATH" exec -T backend curl -sf http://localhost:8000/health/live &>/dev/null; then
            echo -e "${GREEN}  ✓ Backend healthy${NC}"
            return 0
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
        # Print a progress dot every ~12s
        [ $((elapsed % 12)) -eq 0 ] && echo -e "${BLUE}    ... still waiting (${elapsed}s)${NC}"
    done
    echo -e "${YELLOW}  ⚠ Backend health timeout after ${timeout}s (non-fatal)${NC}"
    return 1
}

wait_for_registrar() {
    local timeout="${1:-45}"
    echo -e "${BLUE}  → Waiting for agent-registrar to start (up to ${timeout}s)...${NC}"
    local elapsed=0
    local interval=3
    while [ "$elapsed" -lt "$timeout" ]; do
        if docker compose -f "$COMPOSE_PATH" ps --status running agent-registrar 2>/dev/null | grep -q "Up"; then
            # Verify the Python process is actually running inside
            # the container (it might be in start_period limbo)
            if docker compose -f "$COMPOSE_PATH" exec -T agent-registrar pgrep -f agent_registrar.py >/dev/null 2>&1; then
                echo -e "${GREEN}  ✓ Agent registrar is running${NC}"
                return 0
            fi
        fi
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done
    echo -e "${YELLOW}  ⚠ Agent registrar did not start within ${timeout}s (check: docker compose logs agent-registrar)${NC}"
    return 1
}

final_health_check() {
    # A final pass that confirms the node is fully functional.
    # Each check is non-fatal — we report the result and let
    # the operator decide whether to investigate. The point is
    # to surface "almost there" failures that would otherwise
    # only show up hours later.
    echo -e "${BLUE}  → Running final health check...${NC}"
    local all_ok=0
    local backend_ok=0
    local celery_ok=0
    local registrar_ok=0
    local master_ok=0
    local wg_ok=0

    # 1. Backend healthy?
    if docker compose -f "$COMPOSE_PATH" exec -T backend curl -fsS http://localhost:8000/health/live >/dev/null 2>&1; then
        backend_ok=1
        echo -e "${GREEN}    ✓ Backend /health/live${NC}"
    else
        echo -e "${RED}    ✗ Backend /health/live (not responding)${NC}"
    fi

    # 2. Celery worker subscribed to its queue?
    if docker compose -f "$COMPOSE_PATH" exec -T celery-worker celery -A config inspect ping -d celery@$(hostname)@%h 2>/dev/null | grep -q "pong"; then
        celery_ok=1
        echo -e "${GREEN}    ✓ Celery worker (ping pong)${NC}"
    else
        # Fall back to a queue-depth check — the worker's queue
        # list is queryable without the ping pong dance.
        if docker compose -f "$COMPOSE_PATH" exec -T celery-worker celery -A config inspect active 2>/dev/null | head -1 | grep -q ":"; then
            celery_ok=1
            echo -e "${GREEN}    ✓ Celery worker (active)${NC}"
        else
            echo -e "${RED}    ✗ Celery worker (not responding to ping)${NC}"
        fi
    fi

    # 3. Agent registrar running?
    if docker compose -f "$COMPOSE_PATH" exec -T agent-registrar pgrep -f agent_registrar.py >/dev/null 2>&1; then
        registrar_ok=1
        echo -e "${GREEN}    ✓ Agent registrar process is alive${NC}"
    else
        echo -e "${RED}    ✗ Agent registrar process is not running${NC}"
    fi

    # 4. Master reachable (any of the candidate URLs)?
    if probe_master_health 5 >/dev/null 2>&1; then
        master_ok=1
        echo -e "${GREEN}    ✓ Master is reachable${NC}"
    else
        echo -e "${YELLOW}    ⚠ Master is not yet reachable (registrar will keep retrying)${NC}"
    fi

    # 5. WireGuard interface up? (Only if wg is installed)
    if command -v wg >/dev/null 2>&1; then
        if wg show wg0 >/dev/null 2>&1; then
            wg_ok=1
            echo -e "${GREEN}    ✓ WireGuard wg0 is up${NC}"
        else
            echo -e "${YELLOW}    ⚠ WireGuard wg0 is not up (registrar will fall back to public IP)${NC}"
        fi
    fi

    all_ok=$((backend_ok + celery_ok + registrar_ok))
    if [ "$all_ok" -eq 3 ] && [ "$master_ok" -eq 1 ]; then
        echo -e "${GREEN}  ✅ Node is fully functional${NC}"
    elif [ "$all_ok" -eq 3 ]; then
        echo -e "${YELLOW}  ⚠ Node services are up but master is not yet reachable. The registrar will keep retrying.${NC}"
    else
        echo -e "${YELLOW}  ⚠ Some services are not yet ready. Check: docker compose -f $COMPOSE_FILE logs${NC}"
    fi
}

# =============================================================================
# FRESH INSTALL
# =============================================================================
do_install() {
    local step=0

    # ── Step 1: Prerequisites ──
    step=$((step + 1))
    echo -e "\n${YELLOW}[$step/5] Checking prerequisites...${NC}"
    check_internet() { ping -c1 -W3 8.8.8.8 &>/dev/null || ping -c1 -W3 1.1.1.1 &>/dev/null; }
    check_docker() { command -v docker &>/dev/null && docker info &>/dev/null; }
    check_internet && echo -e "${GREEN}  ✓ Internet OK${NC}" || { echo -e "${RED}✗ No internet${NC}"; exit 1; }
    check_docker && echo -e "${GREEN}  ✓ Docker OK${NC}" || { echo -e "${RED}✗ Docker not running${NC}"; exit 1; }
    local ram disk_free
    ram="$(free -m | awk '/^Mem:/{print $2}')"
    [ "${ram:-0}" -lt 512 ] && { echo -e "${RED}✗ Need >= 512MB RAM (have ${ram:-0}MB)${NC}"; exit 1; }
    echo -e "${GREEN}  ✓ RAM: ${ram}MB${NC}"
    disk_free="$(df -m "$INSTALL_DIR" | awk 'NR==2{print $4}')"
    [ "${disk_free:-0}" -lt 2048 ] && { echo -e "${RED}✗ Need >= 2GB free disk (have ${disk_free:-0}MB)${NC}"; exit 1; }
    echo -e "${GREEN}  ✓ Disk: ${disk_free}MB free${NC}"

    # ── Step 2: Network + images ──
    step=$((step + 1))
    echo -e "\n${YELLOW}[$step/5] Preparing infrastructure...${NC}"
    cd "$INSTALL_DIR"
    ensure_agent_env_defaults
    configure_docker_registry_trust
    ensure_networks
    ensure_backups_volume
    for _img in tecnativa/docker-socket-proxy:latest traefik:v3.6; do
        if docker image inspect "$_img" &>/dev/null; then
            echo -e "${GREEN}  ✓ $_img cached${NC}"
        elif docker pull "$_img" 2>/dev/null; then
            echo -e "${GREEN}  ✓ $_img pulled${NC}"
        else
            echo -e "${YELLOW}  ⚠ Could not pull $_img (build may fail if uncached)${NC}"
        fi
    done

    # ── Step 3: Build backend ──
    step=$((step + 1))
    echo -e "\n${YELLOW}[$step/5] Building agent image...${NC}"
    if docker compose -f "$COMPOSE_FILE" build --no-cache backend; then
        echo -e "${GREEN}  ✓ Backend image built${NC}"
    else
        echo -e "${RED}✗ Backend build failed${NC}"
        exit 1
    fi

    # ── Step 4: Start services ──
    step=$((step + 1))
    echo -e "\n${YELLOW}[$step/5] Starting services...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d socket-proxy redis rabbitmq traefik 2>/dev/null || \
        echo -e "${YELLOW}  ⚠ Some infra services may have failed (check: docker compose ps)${NC}"
    sync_local_rabbitmq_password
    docker compose -f "$COMPOSE_FILE" up -d backend
    wait_for_backend 90 3
    docker compose -f "$COMPOSE_FILE" up -d celery-worker agent-registrar

    # Verify all expected services
    echo -e "${BLUE}  → Verifying services...${NC}"
    docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || true

    # ── Step 5: Finish ──
    step=$((step + 1))
    echo -e "\n${YELLOW}[$step/5] Finalizing...${NC}"
    run_migrations
    fix_permissions
    final_health_check
    local ip
    ip="$(detect_public_ip)"
    echo -e "\n${GREEN}═══════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✅ Agent Install Complete${NC}"
    echo -e "${GREEN}  Traefik:  http://${ip:-<this-host>}:80${NC}"
    if probe_master_health 5 >/dev/null 2>&1; then
        echo -e "${GREEN}  Master:   reachable ✓${NC}"
    else
        echo -e "${YELLOW}  Master:   not yet reachable (will keep retrying from agent-registrar)${NC}"
    fi
    echo -e "${GREEN}═══════════════════════════════════════${NC}"
}

# =============================================================================
# UPDATE (full)
# =============================================================================
do_update_full() {
    echo -e "\n${BLUE}  → Full update (rebuild + restart)${NC}"
    pull_latest_code
    cd "$INSTALL_DIR"
    ensure_agent_env_defaults
    configure_docker_registry_trust
    ensure_networks
    ensure_backups_volume

    echo -e "${BLUE}  -> Updating Docker images...${NC}"
    docker compose -f "$COMPOSE_PATH" pull || echo -e "${YELLOW}    ⚠ Image pull failed${NC}"

    echo -e "${BLUE}  → Rebuilding agent images (no cache)...${NC}"
    docker compose -f "$COMPOSE_PATH" build --no-cache || {
        echo -e "${RED}✗ Build failed${NC}"; exit 1;
    }

    echo -e "${BLUE}  → Restarting all services...${NC}"
    docker compose -f "$COMPOSE_PATH" up -d --force-recreate

    sync_local_rabbitmq_password
    wait_for_backend 60 3
    wait_for_registrar 45
    run_migrations
    fix_permissions
    final_health_check
    final_health_check

    # NOTE: We do NOT unconditionally restart the Docker daemon here.
    # Doing so would cascade-fail all running containers — including Traefik
    # (the proxy) — causing a total outage for every service on the node.
    # configure_docker_registry_trust (called above) already handles restarting
    # Docker conditionally when daemon.json was actually changed.
    
    echo -e "\n${GREEN}✅ Agent update complete${NC}"
}

# =============================================================================
# UPDATE (half — no build)
# =============================================================================
do_update_half() {
    echo -e "\n${BLUE}  → Half update (restart only, no build)${NC}"
    pull_latest_code
    cd "$INSTALL_DIR"
    ensure_agent_env_defaults
    configure_docker_registry_trust
    ensure_networks
    ensure_backups_volume

    echo -e "${BLUE}  -> Updating Docker images...${NC}"
    docker compose -f "$COMPOSE_PATH" pull || echo -e "${YELLOW}    ⚠ Image pull failed${NC}"

    echo -e "${BLUE}  → Restarting all services...${NC}"
    docker compose -f "$COMPOSE_PATH" up -d --force-recreate

    sync_local_rabbitmq_password
    wait_for_backend 60 3
    wait_for_registrar 45
    run_migrations
    fix_permissions
    final_health_check

    # NOTE: We do NOT unconditionally restart the Docker daemon here.
    # Doing so would cascade-fail all running containers — including Traefik
    # (the proxy) — causing a total outage for every service on the node.
    # configure_docker_registry_trust (called above) already handles restarting
    # Docker conditionally when daemon.json was actually changed.

    echo -e "\n${GREEN}✅ Agent half-update complete${NC}"
}

# =============================================================================
# MAIN DISPATCH
# =============================================================================
case "$UPDATE_MODE" in
    full)  do_update_full ;;
    half)  do_update_half ;;
    "")    do_install ;;
    *)     echo -e "${RED}Unknown mode: $UPDATE_MODE${NC}"; exit 1 ;;
esac
