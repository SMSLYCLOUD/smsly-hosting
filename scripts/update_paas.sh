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
echo " CloudNeuron PaaS - Core Update Script"
echo "========================================"

# Lock file check
LOCK_FILE="/tmp/paas_update.lock"
if [ -f "$LOCK_FILE" ]; then
    echo "[ERROR] Update already in progress (lock file exists: $LOCK_FILE)."
    exit 1
fi
touch "$LOCK_FILE"
trap 'rm -f $LOCK_FILE' EXIT

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
if [ ! -f "$COMPOSE_FILE" ]; then
  COMPOSE_FILE="docker-compose.yml"
fi
COMPOSE_CMD=(docker compose -f "$COMPOSE_FILE")

echo "--> Preflight Checks..."
if ! command -v docker &> /dev/null; then
    echo "[ERROR] Docker not found."
    exit 1
fi

echo "--> Pulling latest images..."
"${COMPOSE_CMD[@]}" pull backend frontend celery celery-beat || true

echo "--> Restarting services safely..."
"${COMPOSE_CMD[@]}" up -d backend frontend celery celery-beat || true

echo "--> Running post-update health check..."
sleep 10
curl -s http://localhost/api/v1/system/ready/ || echo "[WARNING] Health check not responding immediately."

echo "--> Update script finished."
