#!/usr/bin/env bash
# Pre-commit / CI gate for smsly-hosting platform changes.
# Catches the failure classes that took prod down on 2026-08:
#   * Python SyntaxError / NameError-at-import  (py_compile)
#   * Shell syntax errors                       (bash -n)
#   * Compose interpolation/profile breakage    (docker compose config)
# Usage: bash scripts/precommit_check.sh [--quick]
#   --quick: only compile files changed vs origin/master
set -uo pipefail

FAIL=0
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "── Python compile ─────────────────────────────────────────"
if [[ "${1:-}" == "--quick" ]]; then
    MAPFILE=( $(git diff --name-only origin/master...HEAD 2>/dev/null | grep '\.py$' || true) )
else
    MAPFILE=( $(find backend -name '*.py' -not -path '*/node_modules/*' -not -path '*/__pycache__/*') )
fi
for f in "${MAPFILE[@]}"; do
    if ! python3 -m py_compile "$f" 2>/tmp/pc.err; then
        echo "FAIL: $f"; cat /tmp/pc.err; FAIL=1
    fi
done
echo "  compiled: ${#MAPFILE[@]} file(s)"

echo "── Shell syntax ───────────────────────────────────────────"
SH_FILES=( $(find backend lib scripts -name '*.sh' 2>/dev/null || true) )
for f in "${SH_FILES[@]}"; do
    if ! bash -n "$f" 2>/tmp/sh.err; then
        echo "FAIL: $f"; cat /tmp/sh.err; FAIL=1
    fi
done
echo "  checked: ${#SH_FILES[@]} file(s)"

echo "── Compose profiles ───────────────────────────────────────"
export POSTGRES_PASSWORD=d REPLICATION_PASSWORD=d PGCAT_ADMIN_PASSWORD=d \
       PATRONI_SUPERUSER_PASSWORD=d REDIS_PASSWORD=d RABBITMQ_PASSWORD=d \
       FRP_AUTH_TOKEN=d GRAFANA_PASSWORD=d
for PROFILE in "" "local-ha" "patroni"; do
    if [[ -n "$PROFILE" ]]; then
        OUT=$(docker compose --profile "$PROFILE" -f docker-compose.prod.yml config --quiet 2>&1)
    else
        OUT=$(docker compose -f docker-compose.prod.yml config --quiet 2>&1)
    fi
    if [[ $? -ne 0 ]]; then
        echo "FAIL: profile '${PROFILE:-external}'"; echo "$OUT" | head -3; FAIL=1
    else
        echo "  ok: ${PROFILE:-external}"
    fi
done

if [[ $FAIL -eq 0 ]]; then
    echo "✅ precommit gate passed"
else
    echo "❌ precommit gate FAILED"
fi
exit $FAIL
