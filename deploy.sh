#!/bin/bash
# ============================================================
# SMSLY Hosting — One-Command Deploy
# Pulls, builds, restarts EVERYTHING in the correct order.
# Usage: bash deploy.sh [--no-pull] [--no-build]
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"

NO_PULL=false
NO_BUILD=false
for arg in "$@"; do
  case $arg in
    --no-pull)  NO_PULL=true ;;
    --no-build) NO_BUILD=true ;;
  esac
done

echo "╔══════════════════════════════════════╗"
echo "║   SMSLY Hosting — Full Deploy        ║"
echo "╚══════════════════════════════════════╝"

# 1. Pull latest code
if [ "$NO_PULL" = false ]; then
  echo ""
  echo "▸ Pulling latest code..."
  git pull origin main
fi

# 2. Build backend + frontend images
if [ "$NO_BUILD" = false ]; then
  echo ""
  echo "▸ Building images (backend + frontend)..."
  docker compose build backend frontend
fi

# 3. Bring up infrastructure first (db, redis, socket-proxy, registry)
echo ""
echo "▸ Starting infrastructure (db, redis, socket-proxy)..."
docker compose up -d db redis socket-proxy registry

# 4. Wait for health checks
echo "▸ Waiting for DB + Redis to be healthy..."
sleep 5

# 5. Bring up backend (needs db + redis healthy)
echo ""
echo "▸ Starting backend..."
docker compose up -d backend
echo "▸ Waiting for backend health check..."
sleep 10

# 6. Restart celery + celery-beat (fixes KeyError:9 / stale Redis FD)
echo ""
echo "▸ Restarting Celery worker + beat..."
docker compose up -d celery celery-beat
docker compose restart celery celery-beat
sleep 3

# 7. Start frontend
echo ""
echo "▸ Starting frontend..."
docker compose up -d frontend
sleep 5

# 8. Restart reverse proxies (nginx, traefik, caddy — whichever exist)
echo ""
echo "▸ Restarting reverse proxies..."
for proxy in nginx traefik caddy; do
  container="smsly-hosting-${proxy}-1"
  if docker ps -a --format '{{.Names}}' | grep -q "$container"; then
    echo "  ↳ Restarting $container"
    docker restart "$container"
  fi
done
sleep 2

# 9. Final health check
echo ""
echo "▸ Final status:"
docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

echo ""
echo "✅ Deploy complete!"
