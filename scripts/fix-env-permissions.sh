#!/bin/bash
# =============================================================================
# Fix .env permissions for backend container write access
# =============================================================================
# The backend container runs as user smsly (UID 1000). When the domain is
# updated via Settings → Domain & SSL in the UI, the post_save signal
# (backend/apps/deployments/signals.py) writes the new DOMAIN, USE_SSL, etc.
# back to the host .env file.
#
# If .env is owned by root:root with 644 permissions, UID 1000 cannot write
# and the signal fails silently. This script fixes the permissions so the
# signal can persist domain changes back to .env without SSH.
#
# Usage:
#   sudo bash scripts/fix-env-permissions.sh
#
# Or directly from the install directory:
#   cd /opt/smsly-hosting && sudo bash scripts/fix-env-permissions.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Determine install directory
INSTALL_DIR=""
if [ -d "/opt/smsly-hosting" ]; then
    INSTALL_DIR="/opt/smsly-hosting"
elif [ -f "./.env" ] && [ -f "./docker-compose.prod.yml" ]; then
    INSTALL_DIR="$(pwd)"
else
    echo -e "${RED}✗ Cannot find Grid installation directory.${NC}"
    echo "  Tried: /opt/smsly-hosting and current directory"
    echo "  Usage: sudo bash $0"
    exit 1
fi

ENV_FILE="$INSTALL_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}✗ .env not found at $ENV_FILE${NC}"
    exit 1
fi

echo -e "${BLUE}Grid — Fix .env Permissions${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  Installation: ${YELLOW}$INSTALL_DIR${NC}"
echo -e "  .env file:    ${YELLOW}$ENV_FILE${NC}"
echo ""

# ──────────────────────────────────────────
# Step 1: Show current permissions
# ──────────────────────────────────────────
echo -e "${BLUE}Current permissions:${NC}"
ls -la "$ENV_FILE"
echo ""

CURRENT_OWNER=$(stat -c "%u:%g" "$ENV_FILE"  || stat -f "%u:%g" "$ENV_FILE"  || echo "unknown")
CURRENT_MODE=$(stat -c "%a" "$ENV_FILE"  || stat -f "%OLp" "$ENV_FILE"  || echo "unknown")

# ──────────────────────────────────────────
# Step 2: Fix ownership — root:1000
#   root (owner) can read/write
#   GID 1000 (group) can read-only (this is the container's smsly user)
#   others have no access
# ──────────────────────────────────────────
echo -e "${BLUE}Fixing ownership:${NC}"
chown root:1000 "$ENV_FILE"  || {
    echo -e "${YELLOW}  ⚠ chown failed. Trying with sudo...${NC}"
    sudo chown root:1000 "$ENV_FILE"
}
echo -e "  ${GREEN}✓${NC} Owner set to root:1000"
echo ""

# ──────────────────────────────────────────
# Step 3: Fix permissions — 640
#   6 = owner read+write
#   4 = group read-only
#   0 = others none
# ──────────────────────────────────────────
echo -e "${BLUE}Fixing permissions:${NC}"
chmod 640 "$ENV_FILE"  || {
    echo -e "${YELLOW}  ⚠ chmod failed. Trying with sudo...${NC}"
    sudo chmod 640 "$ENV_FILE"
}
echo -e "  ${GREEN}✓${NC} Permissions set to 640"
echo ""

# ──────────────────────────────────────────
# Step 4: Also fix caddy-config permissions
# ──────────────────────────────────────────
if [ -d "$INSTALL_DIR/caddy-config" ]; then
    echo -e "${BLUE}Fixing caddy-config directory:${NC}"
    chown -R 1000:1000 "$INSTALL_DIR/caddy-config"  || sudo chown -R 1000:1000 "$INSTALL_DIR/caddy-config"
    chmod -R u+rwX,g+rwX "$INSTALL_DIR/caddy-config"  || true
    echo -e "  ${GREEN}✓${NC} caddy-config permissions fixed"
    echo ""
fi

# ──────────────────────────────────────────
# Step 5: Verify
# ──────────────────────────────────────────
echo -e "${BLUE}New permissions:${NC}"
ls -la "$ENV_FILE"
echo ""

NEW_OWNER=$(stat -c "%u:%g" "$ENV_FILE"  || stat -f "%u:%g" "$ENV_FILE"  || echo "unknown")
NEW_MODE=$(stat -c "%a" "$ENV_FILE"  || stat -f "%OLp" "$ENV_FILE"  || echo "unknown")

echo ""
if [ "$NEW_OWNER" = "0:1000" ] && [ "$NEW_MODE" = "640" ]; then
    echo -e "${GREEN}✅ Permissions are correct.${NC}"
    echo ""
    echo -e "  The backend container (UID 1000) can now read .env."
    echo -e "  Any domain changes via Settings → Domain & SSL will"
    echo -e "  automatically persist to .env — no SSH needed."
    echo ""
    echo -e "  ${BLUE}To apply the updated signal code, rebuild:${NC}"
    echo -e "    docker compose -f $INSTALL_DIR/docker-compose.prod.yml build backend"
    echo -e "    docker compose -f $INSTALL_DIR/docker-compose.prod.yml down"
    echo -e "    docker compose -f $INSTALL_DIR/docker-compose.prod.yml up -d"
    echo ""
    echo -e "  Then re-save the domain via Settings → Domain & SSL."
else
    echo -e "${YELLOW}⚠ Unexpected permissions: owner=$NEW_OWNER mode=$NEW_MODE${NC}"
    echo -e "  Expected: owner=0:1000 mode=640"
    echo -e "  Try running with: sudo bash $0"
fi
