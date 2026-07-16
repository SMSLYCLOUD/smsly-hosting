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

# Ensure repo cache directory exists for user service builds
mkdir -p /opt/smsly-cache/repos
chmod 775 /opt/smsly-cache
chown -R 1000:1000 /opt/smsly-cache 2>/dev/null || true

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

# ── Health-check polling (replaces fragile sleep) ──
wait_for_healthy() {
  local service="$1" timeout="${2:-60}" elapsed=0
  echo "  Waiting for $service to become healthy (timeout ${timeout}s)..."
  while [ $elapsed -lt $timeout ]; do
    health=$("${COMPOSE_CMD[@]}" ps "$service" --format '{{.Health}}' 2>/dev/null || echo "")
    if [ "$health" = "healthy" ]; then
      echo "  ✓ $service is healthy"
      return 0
    fi
    status=$("${COMPOSE_CMD[@]}" ps "$service" --format '{{.Status}}' 2>/dev/null || echo "")
    if echo "$status" | grep -qi "exit"; then
      echo "  ✗ $service exited unexpectedly"
      return 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "  ⚠ $service not healthy after ${timeout}s (continuing)"
  return 1
}

wait_for_endpoint() {
  local url="$1" timeout="${2:-60}" elapsed=0
  echo "  Waiting for $url (timeout ${timeout}s)..."
  while [ $elapsed -lt $timeout ]; do
    if curl -sf "$url" > /dev/null 2>&1; then
      echo "  ✓ $url responding"
      return 0
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done
  echo "  ⚠ $url not responding after ${timeout}s (continuing)"
  return 1
}

echo "[4/9] Waiting for db/redis health..."
wait_for_healthy db 60 || { echo "FATAL: Database failed to start. Aborting."; exit 1; }
wait_for_healthy redis 30 || { echo "FATAL: Redis failed to start. Aborting."; exit 1; }

echo ""
echo "[5/9] Starting backend..."
"${COMPOSE_CMD[@]}" up -d backend
echo "Waiting for backend health..."
wait_for_healthy backend 120 || { echo "ERROR: backend failed health check"; exit 1; }

echo ""
echo "[6/9] Starting and recycling celery worker + beat..."
"${COMPOSE_CMD[@]}" up -d celery celery-beat
"${COMPOSE_CMD[@]}" restart celery celery-beat || true
sleep 3

echo ""
echo "[7/9] Starting frontend..."
"${COMPOSE_CMD[@]}" up -d frontend
wait_for_healthy frontend 60 || { echo "ERROR: frontend failed health check"; exit 1; }

echo ""
echo "[8/9] Starting platform reverse-proxy (caddy)..."
"${COMPOSE_CMD[@]}" up -d caddy

# In production, app domain routing depends on traefik/route-fallback (127.0.0.1:8081).
# These services live in docker-compose.prod.yml in many deployments.
if [ "$COMPOSE_FILE" = "docker-compose.prod.yml" ] && [ -f docker-compose.prod.yml ]; then
  echo "Ensuring app routing layer (traefik + route-fallback) is running..."

  # Ensure socket-proxy is reachable from smsly-net so traefik can query Docker.
  if docker network ls --format '{{.Name}}' | grep -q '^smsly-net$'; then
    if ! docker network inspect smsly-net --format '{{json .Containers}}' | grep -q 'smsly-hosting-socket-proxy-1'; then
      docker network connect --alias socket-proxy smsly-net smsly-hosting-socket-proxy-1 || echo -e "${YELLOW}    ⚠ Socket-proxy network connect failed${NC}"
    fi
  fi

  docker compose -f docker-compose.prod.yml up -d --no-deps traefik route-fallback || echo -e "${YELLOW}    ⚠ App routing layer start failed${NC}"
fi

# Caddy is often managed by systemd, not docker compose.
if false; then
  echo "Reloading Caddy..."
    true
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
