#!/bin/bash
# =============================================================================
# SMSLY Grid - Infrastructure Handshake & Health Stabilization
# Ensures superuser, API tokens, and DB consistency on Master/Agent nodes.
# =============================================================================

set -euo pipefail

# ─── Colors ──────────────────────────────────────────────────────────────────
BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}=== Starting Grid Handshake ===${NC}"

# ─── Check Environment ───────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    echo -e "${RED}ERROR: Docker not found. Handshake aborted.${NC}"
    exit 1
fi

BACKEND_CONTAINER=$(docker ps --filter "name=backend" --format "{{.Names}}" | head -n 1)

# Agent-lite nodes don't have a backend service; use celery-worker instead.
if [ -z "$BACKEND_CONTAINER" ]; then
    BACKEND_CONTAINER=$(docker ps --filter "name=celery-worker" --format "{{.Names}}" | head -n 1)
    if [ -n "$BACKEND_CONTAINER" ]; then
        echo -e "  → Detected Agent-Lite mode: ${GREEN}${BACKEND_CONTAINER}${NC}"
    fi
fi

if [ -z "$BACKEND_CONTAINER" ]; then
    echo -e "${RED}ERROR: Neither backend nor celery-worker container found. Handshake aborted.${NC}"
    exit 1
fi

echo -e "  → Detected Backend: ${GREEN}${BACKEND_CONTAINER}${NC}"

# ─── 1. Run Migrations ───────────────────────────────────────────────────────
echo -e "${BLUE}  → Reconciling Database Schema...${NC}"
if [ "${SMSLY_MIGRATIONS_DONE:-0}" = "1" ]; then
    echo -e "${YELLOW}  ⚠ Skipping migrations (already run by update pipeline)${NC}"
else
    timeout -k 5 120 docker exec "$BACKEND_CONTAINER" python manage.py migrate --noinput || true
fi

# ─── 2. Ensure Superuser ─────────────────────────────────────────────────────
echo -e "${BLUE}  → Reconciling Administrative Identity...${NC}"
# Use heredoc to create superuser if missing
timeout -k 5 120 docker exec -i "$BACKEND_CONTAINER" python manage.py shell <<EOF || true
from django.contrib.auth import get_user_model
User = get_user_model()
username = 'admin'
email = 'admin@smsly.cloud'
# Generate a random password if one isn't already set via env.
import secrets as _secrets, os as _os
password = _os.environ.get('DJANGO_SUPERUSER_PASSWORD', '') or _secrets.token_urlsafe(24)
if not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username, email, password)
    print(f'✅ Created superuser: {username}')
else:
    u = User.objects.get(username=username)
    if not u.is_superuser:
        u.is_superuser = True
        u.save()
        print(f'✅ Promoted {username} to superuser')
    else:
        print(f'✅ Superuser {username} already exists')
EOF

# ─── 3. Ensure Inter-Node API Token ────────────────────────────────────────────
# The APIToken model (smsly_ prefix) is separate from DRF Token.
# A DRF token may exist from initial admin setup but won't work for inter-node.
echo -e "${BLUE}  → Ensuring Inter-Node API Token...${NC}"
HAS_API_TOKEN=$(timeout -k 5 120 docker exec "$BACKEND_CONTAINER" python manage.py shell -c "
from apps.deployments.api_token_auth import APIToken; print('yes' if APIToken.objects.filter(is_active=True).exists() else 'no')
" 2>/dev/null | tr -d '\r' | tail -1)
if [ "$HAS_API_TOKEN" = "yes" ]; then
    echo -e "${GREEN}  ✅ Inter-Node API Token exists.${NC}"
else
    echo -e "${YELLOW}  → Creating Inter-Node API Token...${NC}"
    TOKEN_OUTPUT=$(timeout -k 5 120 docker exec "$BACKEND_CONTAINER" python manage.py shell -c "
from django.contrib.auth import get_user_model
from apps.deployments.api_token_auth import APIToken
admin = get_user_model().objects.filter(is_superuser=True).first()
if admin:
    token, raw = APIToken.create_token(admin, name='Inter-Node Access')
    print(f'TOKEN: {raw}')
else:
    print('ERROR: No superuser found')
" 2>/dev/null)
    if echo "$TOKEN_OUTPUT" | grep -q "TOKEN:"; then
        echo -e "${GREEN}  ✅ Inter-Node API Token created!${NC}"
        echo "$TOKEN_OUTPUT" | grep "TOKEN:" | sed 's/^/    /'
    else
        echo -e "${RED}  ❌ Failed to create API token:${NC}"
        echo "$TOKEN_OUTPUT"
    fi
fi

echo -e "${BLUE}=== Handshake Complete ===${NC}"
