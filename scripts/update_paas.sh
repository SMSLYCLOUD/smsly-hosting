#!/bin/bash
set -Eeuo pipefail
IFS=$'\n\t'

cd "$(dirname "$0")/.."

handle_error() {
  echo "[ERROR] Update failed at line $1"
  exit 1
}
trap 'handle_error $LINENO' ERR

echo "========================================"
echo " Grid PaaS - Core Update Script"
echo "========================================"

# Lock file check (mkdir is atomic and immune to symlink attacks)
LOCK_FILE="/tmp/paas_update.lock"
if ! mkdir "$LOCK_FILE" ; then
    echo "[ERROR] Update already in progress (lock exists: $LOCK_FILE)."
    exit 1
fi
trap 'rm -rf $LOCK_FILE' EXIT

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
if [ ! -f "$COMPOSE_FILE" ]; then
  COMPOSE_FILE="docker-compose.yml"
fi
COMPOSE_CMD=(docker compose -f "$COMPOSE_FILE")

echo "--> Preflight Checks..."
if ! command -v docker ; then
    echo "[ERROR] Docker not found."
    exit 1
fi

echo "--> Pulling latest images for all services..."
if ! "${COMPOSE_CMD[@]}" pull; then
    echo "[ERROR] Failed to pull latest images."
    exit 1
fi

echo "--> Restarting all services safely..."
if ! "${COMPOSE_CMD[@]}" up -d; then
    echo "[ERROR] Failed to restart services."
    exit 1
fi

echo "--> Running post-update health check..."
sleep 10
if ! curl -fsS --retry 3 --max-time 10 http://localhost/api/v1/system/ready/ ; then
    echo "[ERROR] Health check failed after update."
    exit 1
fi

echo "--> Update script finished."
