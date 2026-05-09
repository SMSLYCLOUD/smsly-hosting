#!/bash
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

if [ -z "$BACKEND_CONTAINER" ]; then
    echo -e "${RED}ERROR: Backend container not running. Handshake aborted.${NC}"
    exit 1
fi

echo -e "  → Detected Backend: ${GREEN}${BACKEND_CONTAINER}${NC}"

# ─── 1. Run Migrations ───────────────────────────────────────────────────────
echo -e "${BLUE}  → Reconciling Database Schema...${NC}"
docker exec "$BACKEND_CONTAINER" python manage.py migrate --noinput

# ─── 2. Ensure Superuser ─────────────────────────────────────────────────────
echo -e "${BLUE}  → Reconciling Administrative Identity...${NC}"
# Use heredoc to create superuser if missing
docker exec -i "$BACKEND_CONTAINER" python manage.py shell <<EOF
from django.contrib.auth import get_user_model
User = get_user_model()
username = 'admin'
email = 'admin@smsly.cloud'
password = 'agbonsalo' # Standard inter-node password
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

# ─── 3. Ensure API Tokens & Print Node Health ────────────────────────────────
echo -e "${BLUE}  → Validating Inter-Node Connectivity...${NC}"
DIAG_OUTPUT=$(docker exec "$BACKEND_CONTAINER" python manage.py diagnose_nodes --fix 2>&1)

# Extract token if it was just created (printed by diagnose_nodes)
if echo "$DIAG_OUTPUT" | grep -q "TOKEN:"; then
    echo -e "${GREEN}  ✅ Inter-Node API Token generated/verified!${NC}"
    echo "$DIAG_OUTPUT" | grep -A 1 "TOKEN:" | sed 's/^/    /'
else
    # Check if token exists but wasn't printed (idempotent case)
    TOKEN_EXISTS=$(docker exec "$BACKEND_CONTAINER" python manage.py shell -c "from apps.deployments.api_token_auth import APIToken; print(APIToken.objects.exists())" | tr -d '\r')
    if [ "$TOKEN_EXISTS" == "True" ]; then
        echo -e "${GREEN}  ✅ Inter-Node API Token exists and is active.${NC}"
    else
        echo -e "${RED}  ❌ FAILED to generate API token. Manual intervention required.${NC}"
        echo "$DIAG_OUTPUT"
    fi
fi

echo -e "${BLUE}=== Handshake Complete ===${NC}"
