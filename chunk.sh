else
    # ─── Fresh install: generate secrets ────────────────────────────────────
    # Force IPv4 to ensure valid URL syntax (avoiding [IPv6] bracket issues)
    PUBLIC_IP="$(detect_public_ip)"

    # Allow non-interactive SSL installs by pre-seeding env vars:
    #   USE_SSL=true DOMAIN=cloud.smsly.cloud ACME_EMAIL=admin@example.com SKIP_SCREEN=1 bash install.sh
    PRESET_DOMAIN="${DOMAIN:-}"
    PRESET_ACME_EMAIL="${ACME_EMAIL:-}"
    PRESET_USE_SSL="${USE_SSL:-}"

    echo -e "\n${BLUE}Select Deployment Mode:${NC}"
    echo -e "  1) ${GREEN}IP Mode${NC} (Easy) - http://$PUBLIC_IP:8090"
    echo -e "  2) ${GREEN}SSL Mode${NC} (Prod) - https://your-domain.com (Requires DNS A Record pointing to $PUBLIC_IP)"

    # If any mode was pre-selected (even IP mode), skip prompting even in interactive shells.
    if [ -n "${PRESET_USE_SSL}" ]; then
        if [ "${PRESET_USE_SSL}" = "true" ] && [ -n "${PRESET_DOMAIN}" ] && [ -n "${PRESET_ACME_EMAIL}" ]; then
            echo -e "${BLUE}  → Preset detected. Using SSL Mode for ${PRESET_DOMAIN}.${NC}"
            MODE_CHOICE=2
        elif [ "${PRESET_USE_SSL}" = "false" ]; then
            echo -e "${BLUE}  → Preset detected. Using IP Mode.${NC}"
            MODE_CHOICE=1
        else
            # Pre-seeded but incomplete? Ask anyway.
            if [ -e /dev/tty ]; then
                read -p "Enter choice [1]: " MODE_CHOICE < /dev/tty
                MODE_CHOICE=${MODE_CHOICE:-1}
            else
                MODE_CHOICE=1
            fi
        fi
    elif [ -e /dev/tty ] && [ "$NON_INTERACTIVE" != "true" ]; then
        read -p "Enter choice [1]: " MODE_CHOICE < /dev/tty
        MODE_CHOICE=${MODE_CHOICE:-1}
    else
        echo -e "${YELLOW}  ⚠ Automated mode detected. Defaulting to IP Mode.${NC}"
        MODE_CHOICE=1
    fi

    DOMAIN=""
    ACME_EMAIL=""
    USE_SSL="false"

    if [ "$MODE_CHOICE" -eq "2" ]; then
        USE_SSL="true"
        if [ ! -e /dev/tty ] && [ -n "${PRESET_DOMAIN}" ] && [ -n "${PRESET_ACME_EMAIL}" ]; then
            DOMAIN="${PRESET_DOMAIN}"
            ACME_EMAIL="${PRESET_ACME_EMAIL}"
        else
            while [ -z "$DOMAIN" ]; do
                read -p "Enter your Domain (e.g., app.example.com): " DOMAIN < /dev/tty
            done

            while [ -z "$ACME_EMAIL" ]; do
                read -p "Enter Email for SSL (e.g., admin@example.com): " ACME_EMAIL < /dev/tty
            done
        fi

        echo -e "${BLUE}  → Verifying DNS for $DOMAIN...${NC}"
        if command -v host &> /dev/null; then
            DETECTED_IP=$(host -t A "$DOMAIN" 2>/dev/null | awk '{print $NF}' | tail -n 1)
            if [[ "$DETECTED_IP" != "$PUBLIC_IP" && "$DETECTED_IP" != "127.0.0.1" ]]; then
                echo -e "${YELLOW}  ⚠ WARNING: DNS for $DOMAIN ($DETECTED_IP) does not match this server ($PUBLIC_IP).${NC}"
                echo -e "${YELLOW}  SSL generation may fail. Ensure your DNS A record is set.${NC}"
                if [ -e /dev/tty ] && [ "$NON_INTERACTIVE" != "true" ]; then
                    read -p "  Continue anyway? (y/n) " -n 1 -r < /dev/tty
                    echo
                    if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 1; fi
                else
                    echo -e "${YELLOW}  ⚠ Automated mode: Ignoring DNS mismatch and continuing...${NC}"
                fi
            else
                echo -e "${GREEN}  ✓ DNS looks correct.${NC}"
            fi
        fi
    else
        DOMAIN="$PUBLIC_IP"
        echo -e "${BLUE}  → Using IP Mode: $PUBLIC_IP${NC}"
    fi

    # ─── Wildcard Subdomain & Cloudflare Setup (SSL mode only) ────────────
    WILDCARD_SUBDOMAINS="false"
    CLOUDFLARE_API_TOKEN=""
    if [ "$USE_SSL" = "true" ] && [ -n "$DOMAIN" ] && [ "$DOMAIN" != "$PUBLIC_IP" ]; then
        echo ""
        echo -e "${BLUE}  Wildcard subdomains allow deployed services to get automatic SSL.${NC}"
        echo -e "  e.g., myapp-abc123.${DOMAIN} will automatically have HTTPS."
        echo -e "  This requires a Cloudflare API Token with DNS:Edit permission.\n"

        PRESET_WILDCARD="${WILDCARD_SUBDOMAINS:-}"
        PRESET_CF_TOKEN="${CLOUDFLARE_API_TOKEN:-}"

        if [ -n "$PRESET_WILDCARD" ] && [ -n "$PRESET_CF_TOKEN" ]; then
            WILDCARD_SUBDOMAINS="$PRESET_WILDCARD"
            CLOUDFLARE_API_TOKEN="$PRESET_CF_TOKEN"
            echo -e "${BLUE}  → Preset detected: wildcard=$WILDCARD_SUBDOMAINS${NC}"
        elif [ -e /dev/tty ]; then
            read -p "  Enable wildcard subdomains? (y/n) [n]: " WILDCARD_CHOICE < /dev/tty
            WILDCARD_CHOICE=${WILDCARD_CHOICE:-n}
            if [[ $WILDCARD_CHOICE =~ ^[Yy]$ ]]; then
                WILDCARD_SUBDOMAINS="true"
                while [ -z "$CLOUDFLARE_API_TOKEN" ]; do
                    read -sp "  Enter Cloudflare API Token (DNS:Edit): " CLOUDFLARE_API_TOKEN < /dev/tty
                    echo
                done
                echo -e "${GREEN}  ✓ Wildcard subdomains enabled.${NC}"
            fi
        fi
    fi

    # ─── Generate Secrets (Python-only, NO invalid fallback) ────────────────
    echo -e "${BLUE}  → Generating secure credentials...${NC}"

    # Install cryptography lib (--break-system-packages for Python 3.12+ on Ubuntu 24.04)
    pip3 install cryptography -q --break-system-packages 2>/dev/null || \
        pip3 install cryptography -q 2>/dev/null || true

    # Generate secrets — Python is the ONLY source of truth for Fernet keys
    SECRETS_GENERATED=false
    if python3 -c "
import secrets, string
from cryptography.fernet import Fernet

chars = string.ascii_letters + string.digits
secret_key = ''.join(secrets.choice(chars) for _ in range(50))
fernet_key = Fernet.generate_key().decode()
pg_pass = secrets.token_hex(16)
redis_pass = secrets.token_hex(16)
rabbitmq_pass = secrets.token_hex(16)
gateway_secret = secrets.token_hex(32)
webhook_secret = secrets.token_hex(32)
autoscaler_token = secrets.token_hex(32)
frp_token = secrets.token_hex(32)

# Validate the Fernet key before outputting
Fernet(fernet_key.encode())

print(f'SECRET_KEY={secret_key}')
print(f'FIELD_ENCRYPTION_KEY={fernet_key}')
print(f'POSTGRES_PASSWORD={pg_pass}')
print(f'REDIS_PASSWORD={redis_pass}')
print(f'RABBITMQ_PASSWORD={rabbitmq_pass}')
print(f'GATEWAY_SECRET={gateway_secret}')
print(f'GITHUB_WEBHOOK_SECRET={webhook_secret}')
print(f'AUTOSCALER_API_TOKEN={autoscaler_token}')
print(f'FRP_AUTH_TOKEN={frp_token}')
" > "$INSTALL_DIR/.secrets.tmp" 2>/dev/null; then
        source "$INSTALL_DIR/.secrets.tmp"
        rm -f "$INSTALL_DIR/.secrets.tmp"
        SECRETS_GENERATED=true
        echo -e "${GREEN}  ✓ Secrets generated (Fernet key validated)${NC}"
    fi

    if [ "$SECRETS_GENERATED" != "true" ]; then
        echo -e "${RED}  ✗ CRITICAL: Cannot generate valid Fernet encryption key.${NC}"
        echo -e "${RED}    Install Python 3 and the 'cryptography' package, then re-run.${NC}"
        echo -e "${RED}    pip3 install cryptography${NC}"
        exit 1
    fi

    # Create .env (Atomic)
    local ENV_TMP="$INSTALL_DIR/.env.tmp"
    cat <<EOF > "$ENV_TMP"
# SMSLY Hosting Configuration — Generated $(date -Iseconds)
ENVIRONMENT=production
DEBUG=False
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$FIELD_ENCRYPTION_KEY
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_USER=smsly_admin
POSTGRES_DB=smsly_hosting
DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@pgcat:5432/smsly_hosting

REDIS_PASSWORD=$REDIS_PASSWORD
RABBITMQ_PASSWORD=$RABBITMQ_PASSWORD
REDIS_URL=redis://:$REDIS_PASSWORD@redis:6379/0
CELERY_BROKER_URL=amqp://smsly_user:$RABBITMQ_PASSWORD@rabbitmq:5672//

DOMAIN=$DOMAIN
ACME_EMAIL=${ACME_EMAIL:-}
USE_SSL=$USE_SSL

# Inter-service HMAC authentication secret
GATEWAY_SECRET=$GATEWAY_SECRET

# GitHub webhook signature verification
GITHUB_WEBHOOK_SECRET=$GITHUB_WEBHOOK_SECRET

# Security
ALLOWED_HOSTS=$DOMAIN,$PUBLIC_IP,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://$PUBLIC_IP:8090,https://$DOMAIN,http://$DOMAIN,http://localhost:8090,http://$PUBLIC_IP
CORS_ALLOWED_ORIGINS=http://$PUBLIC_IP:8090,https://$DOMAIN,http://$DOMAIN,http://$PUBLIC_IP

# Docker networking
# Ensure addon containers and deployed app containers share the same network for connectivity.
DOCKER_NETWORK=smsly-net

# Wildcard subdomain SSL (Cloudflare DNS challenge)
WILDCARD_SUBDOMAINS=$WILDCARD_SUBDOMAINS
CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN:-}
CADDY_CONFIG_DIR=/caddy-config
PUBLIC_IP=$PUBLIC_IP

# Autoscaler API authentication (shared with smsly-autoscaler.service)
AUTOSCALER_API_TOKEN=$AUTOSCALER_API_TOKEN

# FRP Tunnel Relay Authentication Token
FRP_AUTH_TOKEN=$FRP_AUTH_TOKEN
EOF

    # Derive expected tunnel domain
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ] && ! echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EXPECTED_TUNNEL_DOMAIN="tunnel.${DOMAIN}"
    elif [ -n "$PUBLIC_IP" ] && ! echo "$PUBLIC_IP" | grep -qE '^(127\.0\.0\.1|0\.0\.0\.0)$'; then
        EXPECTED_TUNNEL_DOMAIN="tunnel.${PUBLIC_IP}.sslip.io"
    else
        EXPECTED_TUNNEL_DOMAIN="tunnel.localhost"
    fi
    echo "TUNNEL_DOMAIN=$EXPECTED_TUNNEL_DOMAIN" >> "$ENV_TMP"

    # ── Agent Lite Overrides ──────────────────────────────────────
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        echo "MODE=agent" >> "$ENV_TMP"
        echo "MASTER_IP=$MASTER_IP" >> "$ENV_TMP"
        # Force Agent to use Master VPS for DB/Redis/RabbitMQ
        sed -i "s|^DATABASE_URL=.*|DATABASE_URL=postgresql://smsly_admin:${MASTER_DB_PASSWORD}@${MASTER_IP}:5432/smsly_hosting|" "$ENV_TMP"
        sed -i "s|^CELERY_BROKER_URL=.*|CELERY_BROKER_URL=amqp://smsly_user:${MASTER_MQ_PASSWORD}@${MASTER_IP}:5672//|" "$ENV_TMP"
        sed -i "s|^REDIS_URL=.*|REDIS_URL=redis://${MASTER_IP}:6379/1|" "$ENV_TMP"
        # Disable local DB/Registry requirements in the app
        echo "SMSLY_DISABLE_LOCAL_SERVICES=true" >> "$ENV_TMP"
    else
        echo "MODE=master" >> "$ENV_TMP"
    fi

    # Atomic move and validation
    if validate_env_file "$ENV_TMP"; then
        mv "$ENV_TMP" "$INSTALL_DIR/.env"
        chmod 600 "$INSTALL_DIR/.env"
        echo -e "${GREEN}  ✓ Configuration saved to .env (chmod 600)${NC}"
    else
        echo -e "${RED}  x Generated .env failed validation. Aborting install.${NC}"
        rm -f "$ENV_TMP"
        exit 1
    fi
    set_checkpoint "config_generated"
fi
