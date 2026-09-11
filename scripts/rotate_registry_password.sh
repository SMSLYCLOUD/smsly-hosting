#!/usr/bin/env bash
# rotate_registry_password.sh — end-to-end platform registry password rotation.
#
# Regenerates the master registry credential (REGISTRY_USER, default
# smsly-registry) and fans it out to every live consumer:
#   1. registry htpasswd (authoritative store; registry reads it per-request,
#      no restart needed),
#   2. PlatformConfig (runtime source of truth for pipelines/workers),
#   3. .env REGISTRY_PASSWORD (install-time source + provisioner fallback),
#   4. host Docker logins for BOTH registry hostnames (127.0.0.1:5000 and
#      registry:5000 — the daemon matches credentials per hostname),
#   5. verifies the old password is dead (401) and the new one works (200).
#
# Per-project proj-* users are untouched (separate htpasswd lines).
# Remote (non-primary) nodes are LISTED, not touched: re-run node
# provisioning or `docker login <mesh-ip>:5000` on each with the new
# password from .env.
#
# Usage: sudo ./scripts/rotate_registry_password.sh   (from INSTALL_DIR)
#        INSTALL_DIR=/opt/smsly-hosting COMPOSE_FILE=... sudo -E ./scripts/rotate_registry_password.sh
#
# Secrets discipline: the new password is generated and held in shell
# variables only — it is passed to containers via `docker compose exec -e`
# (token_urlsafe alphabet: no spaces/quotes) and never echoed. The old
# password is used for one negative check and never printed.

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/smsly-hosting}"
COMPOSE_FILE="${COMPOSE_FILE:-$INSTALL_DIR/docker-compose.prod.yml}"
PROFILE="${COMPOSE_PROFILE:-local-ha}"
cd "$INSTALL_DIR"

BLUE='\033[0;34m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ERROR: run as root (sudo $0)${NC}"
    exit 1
fi
command -v docker >/dev/null 2>&1 || { echo -e "${RED}ERROR: docker not found${NC}"; exit 1; }
[ -f "$INSTALL_DIR/.env" ] || { echo -e "${RED}ERROR: $INSTALL_DIR/.env not found${NC}"; exit 1; }
if ! docker compose -f "$COMPOSE_FILE" --profile "$PROFILE" ps -q backend 2>/dev/null | grep -q .; then
    echo -e "${RED}ERROR: backend container is not running — start the stack first${NC}"
    exit 1
fi

REG_USER="$(grep -m1 '^REGISTRY_USER=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
[ -n "$REG_USER" ] || REG_USER="smsly-registry"
OLD_PASS="$(grep -m1 '^REGISTRY_PASSWORD=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
NEW_PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"

echo -e "${BLUE}  → Rotating platform registry credential ($REG_USER)...${NC}"

# ── 1+2. htpasswd + PlatformConfig (authoritative stores) ──────────────
echo -e "${BLUE}  → Updating htpasswd + PlatformConfig...${NC}"
STORE_OUT="$(NEW_REGISTRY_USER="$REG_USER" NEW_REGISTRY_PASSWORD="$NEW_PASS" \
    docker compose -f "$COMPOSE_FILE" --profile "$PROFILE" exec -T backend \
    python manage.py shell <<'PYEOF' 2>&1 || true
import os
user = os.environ.get('NEW_REGISTRY_USER', 'smsly-registry')
pwd = os.environ.get('NEW_REGISTRY_PASSWORD', '')
assert pwd, 'missing NEW_REGISTRY_PASSWORD'
from apps.deployments.services.registry_credentials import upsert_htpasswd_user
from apps.deployments.models.core import PlatformConfig
print('HTPASSWD_OK' if upsert_htpasswd_user(user, pwd) else 'HTPASSWD_FAIL')
cfg = PlatformConfig.load()
cfg.registry_user = user
cfg.registry_password = pwd
cfg.save(update_fields=['registry_user', 'registry_password', 'updated_at'])
print('CONFIG_STORED')
PYEOF
)"
echo "$STORE_OUT" | grep -q HTPASSWD_OK || { echo -e "${RED}  ✗ htpasswd update failed — aborting (nothing else changed)${NC}"; echo "$STORE_OUT" | tail -n 5; exit 1; }
echo "$STORE_OUT" | grep -q CONFIG_STORED || { echo -e "${RED}  ✗ PlatformConfig update failed — aborting${NC}"; echo "$STORE_OUT" | tail -n 5; exit 1; }
echo -e "${GREEN}  ✓ htpasswd + PlatformConfig updated${NC}"

# ── 3. .env ────────────────────────────────────────────────────────────
if grep -q '^REGISTRY_PASSWORD=' "$INSTALL_DIR/.env"; then
    sed -i "s|^REGISTRY_PASSWORD=.*|REGISTRY_PASSWORD=$NEW_PASS|" "$INSTALL_DIR/.env"
else
    echo "REGISTRY_PASSWORD=$NEW_PASS" >> "$INSTALL_DIR/.env"
fi
chmod 600 "$INSTALL_DIR/.env" || true
echo -e "${GREEN}  ✓ .env updated${NC}"

# ── 4. Host daemon logins (both hostnames — credentials are per-host) ──
echo -e "${BLUE}  → Logging host daemon in to registry (both hostnames)...${NC}"
for _host in 127.0.0.1:5000 registry:5000; do
    if printf '%s\n' "$NEW_PASS" | docker login --username "$REG_USER" --password-stdin "$_host" >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ docker login $_host${NC}"
    else
        echo -e "${RED}  ✗ docker login $_host FAILED — pulls via that hostname will 401${NC}"
    fi
done

# ── 5. Verify: old dead, new alive ─────────────────────────────────────
echo -e "${BLUE}  → Verifying rotation...${NC}"
_NEW_CODE="$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' -u "$REG_USER:$NEW_PASS" https://127.0.0.1:5000/v2/_catalog 2>/dev/null || true)"
if [ "$_NEW_CODE" = "200" ]; then
    echo -e "${GREEN}  ✓ new credential accepted (catalog 200)${NC}"
else
    echo -e "${RED}  ✗ new credential rejected (catalog HTTP ${_NEW_CODE:-unreachable}) — investigate before relying on it${NC}"
fi
if [ -n "$OLD_PASS" ] && [ "$OLD_PASS" != "$NEW_PASS" ]; then
    _OLD_CODE="$(curl -sk --max-time 10 -o /dev/null -w '%{http_code}' -u "$REG_USER:$OLD_PASS" https://127.0.0.1:5000/v2/_catalog 2>&1 || true)"
    if [ "$_OLD_CODE" = "401" ]; then
        echo -e "${GREEN}  ✓ old credential rejected (401) — rotation complete${NC}"
    else
        echo -e "${YELLOW}  ⚠ old credential probe returned HTTP ${_OLD_CODE:-unreachable} (expected 401)${NC}"
    fi
fi

# ── 6. Remote nodes (listed, not touched) ──────────────────────────────
NODES="$(docker compose -f "$COMPOSE_FILE" --profile "$PROFILE" exec -T backend python manage.py shell -c "
from apps.deployments.models.core import ManagedServer
for s in ManagedServer.objects.filter(is_primary=False):
    print(str(s.name or s.id) + '|' + str(getattr(s, 'host', '') or ''))
" 2>/dev/null | grep '|' || true)"
if [ -n "$NODES" ]; then
    echo -e "${YELLOW}  ⚠ Non-primary node(s) detected — re-login each to <mesh-ip>:5000 with the NEW password from .env:${NC}"
    echo "$NODES" | while IFS='|' read -r _n _h; do echo -e "${YELLOW}      - $_n (${_h:-unknown host})${NC}"; done
else
    echo -e "${GREEN}  ✓ single-host fleet — no remote nodes to re-login${NC}"
fi

echo -e "${GREEN}  ✓ Registry password rotation complete${NC}"
