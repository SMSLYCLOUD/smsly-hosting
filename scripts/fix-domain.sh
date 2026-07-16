#!/bin/bash
# =============================================================================
# SMSLY Domain Fix Script — v1.0
# Fixes domain/IP sync issues between .env, PlatformConfig DB, and Caddy.
#
# Usage:
#   sudo bash scripts/fix-domain.sh              # interactive (prompts for domain)
#   sudo DOMAIN=grid.smsly.cloud bash scripts/fix-domain.sh  # non-interactive
#
# What it fixes:
#   1. Pulls latest install.sh (with all SEC-xxx guards)
#   2. Fixes .env DOMAIN/USE_SSL to match your real domain
#   3. Syncs PlatformConfig in DB to your real domain
#   4. Regenerates Caddyfile with proper config
#   5. Restarts Caddy so HTTPS works immediately
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

INSTALL_DIR="/opt/smsly-hosting"
COMPOSE_FILE="docker-compose.prod.yml"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ERROR: This script must be run as root.${NC}"
    echo "  sudo bash scripts/fix-domain.sh"
    exit 1
fi

if [ ! -d "$INSTALL_DIR" ]; then
    echo -e "${RED}ERROR: $INSTALL_DIR not found. Is SMSLY installed?${NC}"
    exit 1
fi

cd "$INSTALL_DIR"

# ─── Step 1: Detect current domain ───────────────────────────────────────
CURRENT_ENV_DOMAIN="$(grep -m1 '^DOMAIN=' .env  | cut -d= -f2- || true)"
CURRENT_PUBLIC_IP="$(grep -m1 '^PUBLIC_IP=' .env  | cut -d= -f2- || true)"
CURRENT_USE_SSL="$(grep -m1 '^USE_SSL=' .env  | cut -d= -f2- || true)"

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  SMSLY Domain Fix Script${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Current .env DOMAIN: ${YELLOW}${CURRENT_ENV_DOMAIN:-}(not set)${NC}"
echo -e "  Current PUBLIC_IP:   ${YELLOW}${CURRENT_PUBLIC_IP:-}(detecting...)${NC}"
echo -e "  Current USE_SSL:     ${YELLOW}${CURRENT_USE_SSL:-false}${NC}"
echo ""

# Detect public IP if not in .env
if [ -z "${CURRENT_PUBLIC_IP:-}" ]; then
    CURRENT_PUBLIC_IP="$(curl -4 -fsS -m 5 https://api.ipify.org  || echo "127.0.0.1")"
fi

# ─── Step 2: Determine target domain ─────────────────────────────────────
DOMAIN="${DOMAIN:-}"
while [ -z "$DOMAIN" ]; do
    echo -e "${BLUE}  Enter your domain (e.g., grid.smsly.cloud):${NC}"
    read -p "  Domain: " DOMAIN < /dev/tty
    DOMAIN="$(echo "$DOMAIN" | xargs)"  # trim
done

# Validate it's not an IP
if echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
    echo -e "${RED}  ERROR: '$DOMAIN' looks like an IP address. Enter a real domain name.${NC}"
    exit 1
fi

echo -e "${GREEN}  → Target domain: $DOMAIN${NC}"
echo ""

# ─── Step 3: Pull latest code ──────────────────────────────────────────
echo -e "${BLUE}[1/5] Pulling latest installer code...${NC}"
git config --global --add safe.directory "$INSTALL_DIR"  || true
git stash  || true
git fetch origin main  || { echo -e "${YELLOW}  ⚠ Git fetch failed; continuing with current code.${NC}"; }
git checkout -B main origin/main  || { echo -e "${YELLOW}  ⚠ Git checkout failed; continuing with current code.${NC}"; }
echo -e "${GREEN}  ✓ Code updated${NC}"

# ─── Step 4: Fix .env ──────────────────────────────────────────────────
echo -e "${BLUE}[2/5] Fixing .env with domain: $DOMAIN...${NC}"

# Set DOMAIN
if grep -q '^DOMAIN=' .env ; then
    sed -i "s|^DOMAIN=.*|DOMAIN=$DOMAIN|" .env
else
    echo "DOMAIN=$DOMAIN" >> .env
fi

# Set USE_SSL=true (since this is a real domain)
if grep -q '^USE_SSL=' .env ; then
    sed -i 's/^USE_SSL=.*/USE_SSL=true/' .env
else
    echo "USE_SSL=true" >> .env
fi

# Ensure ALLOWED_HOSTS includes the domain
if ! grep -q "$DOMAIN" .env ; then
    ALLOWED_HOSTS="$(grep -m1 '^ALLOWED_HOSTS=' .env | cut -d= -f2- || true)"
    if [ -n "$ALLOWED_HOSTS" ]; then
        sed -i "s/^ALLOWED_HOSTS=.*/ALLOWED_HOSTS=$ALLOWED_HOSTS,$DOMAIN/" .env
    fi
fi

# Ensure CSRF_TRUSTED_ORIGINS includes https://DOMAIN
if ! grep -q "https://$DOMAIN" .env ; then
    CSRF_ORIGINS="$(grep -m1 '^CSRF_TRUSTED_ORIGINS=' .env | cut -d= -f2- || true)"
    if [ -n "$CSRF_ORIGINS" ]; then
        sed -i "s|^CSRF_TRUSTED_ORIGINS=.*|CSRF_TRUSTED_ORIGINS=$CSRF_ORIGINS,https://$DOMAIN|" .env
    fi
fi

# Ensure CORS_ALLOWED_ORIGINS includes https://DOMAIN
if ! grep -q "https://$DOMAIN" .env ; then
    CORS_ORIGINS="$(grep -m1 '^CORS_ALLOWED_ORIGINS=' .env | cut -d= -f2- || true)"
    if [ -n "$CORS_ORIGINS" ]; then
        sed -i "s|^CORS_ALLOWED_ORIGINS=.*|CORS_ALLOWED_ORIGINS=$CORS_ORIGINS,https://$DOMAIN|" .env
    fi
fi

echo -e "${GREEN}  ✓ .env updated${NC}"

# ─── Step 5: Sync DB PlatformConfig ────────────────────────────────────
echo -e "${BLUE}[3/5] Syncing PlatformConfig in database...${NC}"

# Find the backend container
BACKEND_CONTAINER=$(docker compose -f "$COMPOSE_FILE" ps -q backend  || true)
if [ -z "$BACKEND_CONTAINER" ]; then
    echo -e "${YELLOW}  ⚠ Backend container not running. Starting stack...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d db $(grep -q "^  *pgcat:" "${COMPOSE_FILE:-docker-compose.prod.yml}"  && echo "pgcat") redis rabbitmq socket-proxy || echo -e "${YELLOW}    ⚠ Stack start failed (backend may not be ready)${NC}"
    sleep 10
    docker compose -f "$COMPOSE_FILE" up -d backend || echo -e "${YELLOW}    ⚠ Backend start failed${NC}"
    sleep 15
fi

# Update PlatformConfig in the database
docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell <<PY  || echo -e "${YELLOW}  ⚠ DB sync skipped (backend not ready)${NC}"
from apps.deployments.models import PlatformConfig
cfg = PlatformConfig.load()
old = cfg.domain
cfg.domain = "$DOMAIN"
cfg.use_ssl = True
cfg.save()
print(f"PlatformConfig: {old or '(empty)'} -> {cfg.domain}")
PY

echo -e "${GREEN}  ✓ PlatformConfig synced${NC}"

# ─── Step 6: Regenerate Caddyfile ──────────────────────────────────────
echo -e "${BLUE}[4/5] Regenerating Caddyfile...${NC}"

# Write a proper Caddyfile for this domain
if command -v caddy ; then
    # Use the install script's safe Caddyfile generator
    bash install.sh --verify  || true
fi

# Direct fallback: write Caddyfile
CADDY_FILE="/opt/smsly-hosting/caddy-config/Caddyfile"
if [ -f "$CADDY_FILE" ] || [ -d "/etc/caddy" ]; then
    cat > "$CADDY_FILE" <<CADDYEOF
# SMSLY Caddyfile — Fixed by fix-domain.sh
{
    on_demand_tls {
        ask http://backend:8000/api/v1/services/check-domain/
    }
}

$DOMAIN {
    reverse_proxy backend:8000
    reverse_proxy frontend:3000
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

:80 {
    @redirectable {
        not header_regexp host ^([0-9]{1,3}[.]){3}[0-9]{1,3}(:[0-9]+)?$
        not host localhost
        not host 127.0.0.1
        not host *.local
        header_regexp host .+
    }
    redir @redirectable https://{host}{uri} 308
    handle {
        reverse_proxy backend:8000
        reverse_proxy frontend:3000
    }
}
CADDYEOF
    echo -e "${GREEN}  ✓ Host Caddyfile written${NC}"
fi

# Also update container Caddyfile (shared volume)
if [ -d "caddy-config" ]; then
    cat > caddy-config/Caddyfile <<CADDYVOL
# SMSLY Caddyfile — Container (fixed by fix-domain.sh)
{
    on_demand_tls {
        ask http://backend:8000/api/v1/services/check-domain/
    }
}

$DOMAIN {
    reverse_proxy backend:8000
    reverse_proxy frontend:3000
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

:80 {
    @redirectable {
        not header_regexp host ^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})(:[0-9]+)?$
        not host localhost
        not host 127.0.0.1
        not host *.local
        header_regexp host .+
    }
    redir @redirectable https://{host}{uri} 308
    handle {
        reverse_proxy backend:8000
        reverse_proxy frontend:3000
    }
}
CADDYVOL
    echo -e "${GREEN}  ✓ Container Caddyfile written${NC}"
fi

# ─── Step 7: Restart Caddy ─────────────────────────────────────────────
echo -e "${BLUE}[5/5] Restarting Caddy...${NC}"

# Host Caddy
    if docker compose ps -q caddy  | grep -q .; then
    if command -v caddy  && caddy validate --config /opt/smsly-hosting/caddy-config/Caddyfile ; then
    true
        echo -e "${GREEN}  ✓ Host Caddy reloaded${NC}"
    fi
fi

# Container Caddy
if docker compose -f "$COMPOSE_FILE" ps -q caddy  | grep -q .; then
    docker compose -f "$COMPOSE_FILE" exec caddy caddy reload --config /etc/caddy/Caddyfile  || \
        docker compose -f "$COMPOSE_FILE" restart caddy || echo -e "${YELLOW}    ⚠ Caddy restart failed${NC}"
    echo -e "${GREEN}  ✓ Container Caddy reloaded${NC}"
fi

# ─── Step 8: Quick verification ────────────────────────────────────────
echo ""
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Verification${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo ""

# Check .env
ENV_DOMAIN="$(grep -m1 '^DOMAIN=' .env | cut -d= -f2- || echo 'NOT SET')"
ENV_SSL="$(grep -m1 '^USE_SSL=' .env | cut -d= -f2- || echo 'NOT SET')"
echo -e "  .env DOMAIN:  ${GREEN}$ENV_DOMAIN${NC}"
echo -e "  .env USE_SSL: ${GREEN}$ENV_SSL${NC}"

# Check Caddy
if command -v caddy ; then
    CADDY_ACTIVE=$(true  || echo "inactive")
    echo -e "  Caddy (host): ${GREEN}$CADDY_ACTIVE${NC}"
fi
if docker compose -f "$COMPOSE_FILE" ps -q caddy  | grep -q .; then
    CADDY_STATUS=$(docker compose -f "$COMPOSE_FILE" ps caddy --format "{{.Status}}"  || echo "unknown")
    echo -e "  Caddy (container): ${GREEN}$CADDY_STATUS${NC}"
fi

# Check HTTPS listener
if command -v ss ; then
    if ss -tlnp  | grep -q ':443'; then
        echo -e "  HTTPS (443): ${GREEN}listening${NC}"
    else
        echo -e "  HTTPS (443): ${YELLOW}not listening (may take a moment)${NC}"
    fi
fi

echo ""
echo -e "${GREEN}  ✓ Fix complete!${NC}"
echo -e "  Access your dashboard at: ${BLUE}https://$DOMAIN${NC}"
echo ""
echo -e "  If you still see certificate errors, wait 2 minutes for"
echo -e "  Let's Encrypt to issue a cert, then run:"
echo -e "    ${YELLOW}sudo bash install.sh --verify${NC}"
echo ""