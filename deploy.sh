#!/bin/bash
# ============================================================
# SMSLY Hosting - One-command deploy
# Pulls, builds, restarts platform services in safe order.
# Usage: bash deploy.sh [--no-pull] [--no-build]
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"

NO_PULL=false
NO_BUILD=false
for arg in "$@"; do
  case "$arg" in
    --no-pull)  NO_PULL=true ;;
    --no-build) NO_BUILD=true ;;
  esac
done

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
if [ ! -f "$COMPOSE_FILE" ]; then
  COMPOSE_FILE="docker-compose.yml"
fi
COMPOSE_CMD=(docker compose -f "$COMPOSE_FILE")

echo "========================================"
echo " SMSLY Hosting - Full Deploy"
echo "========================================"

if [ "$NO_PULL" = false ]; then
  echo ""
  echo "[1/9] Pulling latest code..."
  git pull origin main
fi

if [ "$NO_BUILD" = false ]; then
  echo ""
  echo "[2/9] Building backend + frontend images (using $COMPOSE_FILE)..."
  "${COMPOSE_CMD[@]}" build backend frontend
fi

echo ""
echo "[3/9] Starting core infrastructure (db, redis, socket-proxy, registry)..."
# Do not use --remove-orphans here; it can unintentionally remove routing services.
"${COMPOSE_CMD[@]}" up -d db redis socket-proxy registry

echo "[4/9] Waiting for db/redis health..."
sleep 5

echo ""
echo "[5/9] Starting backend..."
"${COMPOSE_CMD[@]}" up -d backend
echo "Waiting for backend health..."
sleep 10

echo ""
echo "[6/9] Starting and recycling celery worker + beat..."
"${COMPOSE_CMD[@]}" up -d celery celery-beat
"${COMPOSE_CMD[@]}" restart celery celery-beat || true
sleep 3

echo ""
echo "[7/9] Starting frontend..."
"${COMPOSE_CMD[@]}" up -d frontend
sleep 5

echo ""
echo "[8/9] Starting platform reverse-proxy (nginx)..."
"${COMPOSE_CMD[@]}" up -d nginx

# In production, app domain routing depends on traefik/route-fallback (127.0.0.1:8081).
# These services live in docker-compose.prod.yml in many deployments.
if [ "$COMPOSE_FILE" = "docker-compose.prod.yml" ] && [ -f docker-compose.prod.yml ]; then
  echo "Ensuring app routing layer (traefik + route-fallback) is running..."

  # Ensure socket-proxy is reachable from smsly-net so traefik can query Docker.
  if docker network ls --format '{{.Name}}' | grep -q '^smsly-net$'; then
    if ! docker network inspect smsly-net --format '{{json .Containers}}' | grep -q 'smsly-hosting-socket-proxy-1'; then
      docker network connect --alias socket-proxy smsly-net smsly-hosting-socket-proxy-1 || true
    fi
  fi

  docker compose -f docker-compose.prod.yml up -d --no-deps traefik route-fallback || true
fi

# Caddy is often managed by systemd, not docker compose.
if systemctl list-unit-files | grep -q '^caddy.service'; then
  echo "Reloading Caddy..."
  systemctl reload caddy || systemctl restart caddy
fi

sleep 2

echo ""
echo "[9/9] Final status:"
echo "Compose file: $COMPOSE_FILE"
"${COMPOSE_CMD[@]}" ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
if [ "$COMPOSE_FILE" = "docker-compose.prod.yml" ] && [ -f docker-compose.prod.yml ]; then
  docker compose -f docker-compose.prod.yml ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" | grep -E 'traefik|route-fallback' || true
fi

echo ""
echo "Deploy complete."
