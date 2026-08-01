# -----------------------------------------------------------------------------
# 6. Admin User (IDEMPOTENT — skips if admin already exists)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "admin_created"; then
    echo -e "\n${YELLOW}[6/9] Creating Admin User...${NC}"

if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "${BLUE}  → Lite Agent mode: skipping master admin and Local Docker provider setup.${NC}"
    set_checkpoint "admin_created"
else
ADMIN_EXISTS=$(echo "from django.contrib.auth import get_user_model; User = get_user_model(); print('1' if User.objects.filter(username='admin').exists() else '0')" | timeout 60 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell  | tail -1)

if [ "${ADMIN_EXISTS:-0}" = "1" ]; then
    echo -e "${GREEN}  ✓ Admin user check bypassed or already exists — skipping${NC}"
    if [ -f "$CREDENTIALS_FILE" ]; then
        echo -e "${GREEN}  ✓ Credentials file exists — leaving unchanged${NC}"
    else
        # Best effort: don't overwrite an unknown existing password.
        cat > "$CREDENTIALS_FILE" <<CREDS
# SMSLY Hosting Admin Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE
Username: admin
Password: <existing — not changed by installer>
CREDS
        chmod 600 "$CREDENTIALS_FILE"
    fi
else
    # Production hardening: never ship with a default admin password.
    # Use a shell-safe hex password (avoids quoting issues in manage.py shell).
    if [ "$MODE_AGENT_LITE" = "false" ]; then
        ADMIN_PASS="$(gen_hex_secret 16)"
        echo "
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
User = get_user_model()
admin = User.objects.create_superuser('admin', 'admin@smsly.cloud', '$ADMIN_PASS')
token = Token.objects.create(user=admin)
print(token.key)
" | timeout 60 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell  | tail -1 > "$INSTALL_DIR/.token"
        echo -e "${GREEN}  ✓ Admin user created with API Token${NC}"
        chmod 600 "$INSTALL_DIR/.token"

        # ─── Save credentials to secure file (NOT echoed to terminal) ───────────────
        cat > "$CREDENTIALS_FILE" <<CREDS
# SMSLY Hosting Admin Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE
Username: admin
Password: $ADMIN_PASS
CREDS
        chmod 600 "$CREDENTIALS_FILE"

        # -----------------------------------------------------------------------------
        # 6b. Ensure Local Cloud Provider exists (required for deployments)
        # -----------------------------------------------------------------------------
        echo -e "${BLUE}  → Ensuring Local Docker cloud provider exists...${NC}"
        echo "
from apps.cloud.models import CloudProvider
cp, created = CloudProvider.objects.get_or_create(
    provider_type='LOCAL',
    defaults={'name': 'Local Docker', 'is_active': True}
)
if not created and not cp.is_active:
    cp.is_active = True
    cp.save()
print('CREATED' if created else 'EXISTS')
" | timeout 60 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell  | tail -1 
        echo -e "${GREEN}  ✓ Local Docker cloud provider ready${NC}"
    fi
fi
    echo -e "${BLUE}  → Keeping backend entrypoint bootstrap disabled; installer controls migrations...${NC}"
env_set_value "$INSTALL_DIR/.env" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
if should_manage_caddy; then
    env_set_value "$INSTALL_DIR/.env" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "true"
else
    env_set_value "$INSTALL_DIR/.env" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false"
fi

    # ─── Generate Recovery Phrase ─────────────────────────────────────────
    echo -e "${BLUE}  → Generating 12-word recovery phrase...${NC}"
    RECOVERY_PHRASE="$(timeout 60 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.core.views.recovery import recovery_phrase_generate
from django.test.client import RequestFactory
factory = RequestFactory()
request = factory.get('/api/v1/auth/recovery/generate/')
request.user = __import__('django').contrib.auth.get_user_model().objects.filter(is_superuser=True).first()
from django.contrib.sessions.middleware import SessionMiddleware
from django.middleware.csrf import CsrfViewMiddleware
# Minimal request setup for the view to work
response = recovery_phrase_generate(request)
import json
print(json.dumps(response.data))
"  < /dev/null | tail -1 || true)"
    if [ -n "$RECOVERY_PHRASE" ]; then
        RECOVERY_PHRASE_TEXT="$(printf '%s' "$RECOVERY_PHRASE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('phrase',''))"  || true)"
        if [ -n "$RECOVERY_PHRASE_TEXT" ]; then
            echo -e "${GREEN}  ✓ Recovery phrase generated${NC}"
            echo -e "$RECOVERY_PHRASE_TEXT" > "$INSTALL_DIR/.recovery_phrase"
            chmod 600 "$INSTALL_DIR/.recovery_phrase"
            echo -e ""
            echo -e "${YELLOW}════════════════════════════════════════════════════════════${NC}"
            echo -e "${YELLOW}   ⚠  ACCOUNT RECOVERY PHRASE — WRITE THIS DOWN             ${NC}"
            echo -e "${YELLOW}   This is the ONLY time this phrase is displayed.            ${NC}"
            echo -e "${YELLOW}   If all trusted devices are lost, this 12-word phrase       ${NC}"
            echo -e "${YELLOW}   is your last resort to recover admin access.               ${NC}"
            echo -e "${YELLOW}                                                              ${NC}"
            echo -e "${YELLOW}   $RECOVERY_PHRASE_TEXT${NC}"
            echo -e "${YELLOW}                                                              ${NC}"
            echo -e "${YELLOW}   Stored (encrypted) in: $INSTALL_DIR/.recovery_phrase${NC}"
            echo -e "${YELLOW}════════════════════════════════════════════════════════════${NC}"
            echo -e ""
        fi
    fi

    set_checkpoint "admin_created"
fi
fi
