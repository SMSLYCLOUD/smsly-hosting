#!/bin/bash
# install_tier.sh — Stack size selector for SMSLY Hosting
# Usage: ./scripts/install_tier.sh [auto|lite|medium|full]
#   auto   : Select from host RAM (<6GB=lite, 6-12GB=medium, >=12GB=full)
#   lite   : <6GB RAM (single-node core only, no replicas/sentinels/observability/WAF)
#   medium : 6-12GB RAM (+ HA replicas, Sentinels, CrowdSec, observability)
#   full   : >=12GB RAM (everything + Falco, Spire, Apt-Cacher, Verdaccio, open-appsec)
set -euo pipefail

TIER="${1:-auto}"
case "$TIER" in
  auto|lite|medium|full) ;;
  *) echo "Usage: $0 [auto|lite|medium|full]"; echo "  auto   = size from host RAM"; echo "  lite   = core only (<6GB)"; echo "  medium = core + HA replicas + observability (6-12GB)"; echo "  full   = all services (>=12GB)"; exit 1;;
esac

ENV_FILE="${ENV_FILE:-.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "No $ENV_FILE found — run install.sh first or create one from .env.example"
  exit 1
fi

if [ "$TIER" = "auto" ]; then
  RAM_MB="$(free -m 2>/dev/null | awk '/^Mem:/{print $2}')"
  [ -n "$RAM_MB" ] || RAM_MB=8192
  if [ "$RAM_MB" -lt 6144 ]; then
    TIER="lite"
  elif [ "$RAM_MB" -lt 12288 ]; then
    TIER="medium"
  else
    TIER="full"
  fi
fi

# Read existing HA profile (set by install.sh fresh-config heredoc)
DB_HA="$(grep -E '^DB_HA_ENABLED=' "$ENV_FILE" | cut -d= -f2 | tail -1 | tr -d '[:space:]' || true)"
DB_HA="${DB_HA:-local-ha}"

if [ -n "${OPENAPPSEC_ENABLED:-}" ]; then
  WAF_ENABLED="$OPENAPPSEC_ENABLED"
elif [ "$TIER" = "full" ]; then
  WAF_ENABLED="1"
else
  WAF_ENABLED="0"
fi

case "$TIER" in
  lite)
    PROFILES="$DB_HA"
    ;;
  medium)
    PROFILES="$DB_HA"
    [ "$DB_HA" = "local-ha" ] && PROFILES="${PROFILES},local-ha-replica"
    PROFILES="${PROFILES},redis-ha,medium"
    ;;
  full)
    PROFILES="$DB_HA"
    [ "$DB_HA" = "local-ha" ] && PROFILES="${PROFILES},local-ha-replica"
    PROFILES="${PROFILES},redis-ha,medium,full"
    ;;
esac
[ "$WAF_ENABLED" = "1" ] && PROFILES="${PROFILES},appsec"
BROKER_MODE="$(grep -E '^CELERY_BROKER_MODE=' "$ENV_FILE" | cut -d= -f2 | tail -1 | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]' || true)"
[ "$BROKER_MODE" = "amqp" ] && PROFILES="${PROFILES},amqp"

echo "==> Stack tier: $TIER  (COMPOSE_PROFILES=$PROFILES)"
echo "    DB HA: $DB_HA  |  OpenAppSec: $WAF_ENABLED"

_upsert_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

_upsert_env "STACK_TIER" "$TIER"
_upsert_env "COMPOSE_PROFILES" "$PROFILES"
_upsert_env "OPENAPPSEC_ENABLED" "$WAF_ENABLED"

if [ "$TIER" = "lite" ]; then
  [ "$DB_HA" = "local-ha" ] && _upsert_env "DB_REPLICA_HOSTS" ""
  _upsert_env "SENTINEL_HOSTS" ""
else
  if [ "$DB_HA" = "local-ha" ]; then
    _upsert_env "DB_REPLICA_HOSTS" "postgres-replica:5432"
    _upsert_env "SENTINEL_HOSTS" "redis-sentinel-1:26379,redis-sentinel-2:26379,redis-sentinel-3:26379"
  elif [ "$DB_HA" = "patroni" ]; then
    _upsert_env "DB_REPLICA_HOSTS" "haproxy:5001"
  fi
fi

export COMPOSE_PROFILES="$PROFILES"
export STACK_TIER="$TIER"

echo "==> Validating compose..."
if ! docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml config --quiet 2>/dev/null; then
  echo "ERROR: compose validation failed for tier $TIER"
  exit 1
fi

echo "==> Bringing up stack ($TIER)..."
docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml up -d

if [ "$WAF_ENABLED" != "1" ]; then
  echo "==> Stopping open-appsec WAF containers (OPENAPPSEC_ENABLED=$WAF_ENABLED)..."
  docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml --profile appsec stop --timeout 15 \
    appsec-agent appsec-envoy appsec-shared-storage appsec-smartsync appsec-tuning-svc appsec-db 2>/dev/null || true
  docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml --profile appsec rm -f \
    appsec-agent appsec-envoy appsec-shared-storage appsec-smartsync appsec-tuning-svc appsec-db 2>/dev/null || true
fi

if [ "$TIER" != "full" ]; then
  echo "==> Stopping full-tier extras (falco, spire, build caches)..."
  docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml --profile full stop --timeout 15 \
    falco spire-server spire-server-ecosystem apt-cacher verdaccio 2>/dev/null || true
  docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml --profile full rm -f \
    falco spire-server spire-server-ecosystem apt-cacher verdaccio 2>/dev/null || true
  docker rm -f smsly-spire-agent smsly-spire-agent-ecosystem 2>/dev/null || true
fi

if [ "$TIER" = "lite" ]; then
  echo "==> Stopping medium/HA containers for lite single-node mode..."
  docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml --profile local-ha-replica --profile redis-ha --profile medium stop --timeout 15 \
    postgres-replica redis-replica redis-sentinel-1 redis-sentinel-2 redis-sentinel-3 crowdsec crowdsec-cloudflare-bouncer \
    loki promtail grafana cadvisor docker-labels alertmanager prometheus node-exporter 2>/dev/null || true
  docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml --profile local-ha-replica --profile redis-ha --profile medium rm -f \
    postgres-replica redis-replica redis-sentinel-1 redis-sentinel-2 redis-sentinel-3 crowdsec crowdsec-cloudflare-bouncer \
    loki promtail grafana cadvisor docker-labels alertmanager prometheus node-exporter 2>/dev/null || true
  if [ -f infrastructure/docker/docker-compose.observability.yml ]; then
    docker compose --env-file "$ENV_FILE" -f infrastructure/docker/docker-compose.observability.yml down --timeout 15 2>/dev/null || true
  fi
  docker compose --env-file "$ENV_FILE" -f docker-compose.prod.yml restart pgcat 2>/dev/null || true
fi

echo ""
echo "Done. Tier $TIER is running."
echo "  Switch tier anytime: ./scripts/install_tier.sh [auto|lite|medium|full]"
echo "  Current: STACK_TIER=$TIER  COMPOSE_PROFILES=$PROFILES"
