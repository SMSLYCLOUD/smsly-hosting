#!/bin/bash
# install_tier.sh — Stack size selector for SMSLY Hosting
# Usage: ./scripts/install_tier.sh [lite|medium|full]
#   lite   : 2 vCPU / 4GB / 40GB  — core only (no Grafana/Loki, no Falco/Spire)
#   medium : 4 vCPU / 8GB / 80GB  — + observability (Grafana/Loki/Promtail/Cadvisor)
#   full   : 8 vCPU /16GB /200GB  — everything (Falco, CrowdSec, Spire, Apt-Cacher, Verdaccio)
set -euo pipefail

TIER="${1:-medium}"
case "$TIER" in
  lite|medium|full) ;;
  *) echo "Usage: $0 [lite|medium|full]"; echo "  lite   = core only"; echo "  medium = core + observability"; echo "  full   = all services"; exit 1;;
esac

ENV_FILE="${ENV_FILE:-.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "No $ENV_FILE found — run install.sh first or create one from .env.example"
  exit 1
fi

# Read existing HA profile (set by install.sh fresh-config heredoc)
DB_HA="$(grep -E '^DB_HA_ENABLED=' "$ENV_FILE" | cut -d= -f2 | tail -1 || true)"
DB_HA="${DB_HA:-local-ha}"

case "$TIER" in
  lite)   PROFILES="$DB_HA" ;;
  medium) PROFILES="$DB_HA,medium" ;;
  full)   PROFILES="$DB_HA,full" ;;
esac

echo "==> Stack tier: $TIER  (COMPOSE_PROFILES=$PROFILES)"
echo "    DB HA: $DB_HA"

# Persist tier so future `docker compose up` picks it up
if grep -q '^STACK_TIER=' "$ENV_FILE"; then
  sed -i "s/^STACK_TIER=.*/STACK_TIER=$TIER/" "$ENV_FILE"
else
  echo "STACK_TIER=$TIER" >> "$ENV_FILE"
fi
if grep -q '^COMPOSE_PROFILES=' "$ENV_FILE"; then
  sed -i "s/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=$PROFILES/" "$ENV_FILE"
else
  echo "COMPOSE_PROFILES=$PROFILES" >> "$ENV_FILE"
fi

export COMPOSE_PROFILES
export STACK_TIER="$TIER"

echo "==> Validating compose..."
if ! docker compose -f docker-compose.prod.yml config --quiet 2>/dev/null; then
  echo "ERROR: compose validation failed for tier $TIER"
  exit 1
fi

echo "==> Bringing up stack ($TIER)..."
docker compose -f docker-compose.prod.yml up -d

echo ""
echo "Done. Tier $TIER is running."
echo "  Switch tier anytime: ./scripts/install_tier.sh [lite|medium|full]"
echo "  Current: STACK_TIER=$TIER  COMPOSE_PROFILES=$PROFILES"
