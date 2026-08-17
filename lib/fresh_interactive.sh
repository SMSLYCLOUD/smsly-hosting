
# ─── Interactive Setup (Step 0) ──────────────────────────────────────────────
if [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
    # Agent Lite Selection
    if [ "$MODE_AGENT_LITE" = "true" ] && [ -z "${MASTER_IP:-}" ]; then
        echo -e "\n${BLUE}═══════════════════════════════════════════════════════════"
        echo "  CONFIGURING LITE AGENT NODE"
        echo "═══════════════════════════════════════════════════════════${NC}"
        read -p "  Enter Master VPS IP Address: " MASTER_IP < /dev/tty
        read -p "  Enter Master Database Password: " MASTER_DB_PASSWORD < /dev/tty
        echo ""
        read -p "  Enter Master RabbitMQ Password: " MASTER_MQ_PASSWORD < /dev/tty
        echo ""
        COMPOSE_FILE="infrastructure/docker/docker-compose.agent-lite.yml"
        export MASTER_IP MASTER_DB_PASSWORD MASTER_MQ_PASSWORD
    fi

    # ─── Deployment Mode Selection (Moved up) ──────────────────────────────
    # Initialize defaults
    MODE_CHOICE=1
    PUBLIC_IP="${PUBLIC_IP:-$(detect_public_ip)}"
    PRESET_DOMAIN="${DOMAIN:-}"
    PRESET_ACME_EMAIL="${ACME_EMAIL:-}"
    PRESET_USE_SSL="${USE_SSL:-}"

    # Deployment Mode Selection - Only prompt if not preset and in interactive shell
    if is_node_mode; then
        if [ "${PRESET_USE_SSL}" = "true" ] && [ -n "${PRESET_DOMAIN}" ]; then
            echo -e "${BLUE}  → Node mode: SSL preset detected for ${PRESET_DOMAIN}.${NC}"
        else
            USE_SSL="false"
            DOMAIN="${DOMAIN:-$PUBLIC_IP}"
            MODE_CHOICE=1
            echo -e "${BLUE}  → Node mode: using Caddy HTTP on $DOMAIN.${NC}"
        fi
    elif [ -n "${PRESET_USE_SSL}" ]; then
        if [ "${PRESET_USE_SSL}" = "true" ] && [ -n "${PRESET_DOMAIN}" ] && [ -n "${PRESET_ACME_EMAIL}" ]; then
            echo -e "${BLUE}  → Preset detected. Using SSL Mode for ${PRESET_DOMAIN}.${NC}"
            MODE_CHOICE=2
        elif [ "${PRESET_USE_SSL}" = "false" ]; then
            echo -e "${BLUE}  → Preset detected. Using IP Mode.${NC}"
            MODE_CHOICE=1
        fi
    elif [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
        echo -e "\n${BLUE}Select Deployment Mode:${NC}"
        echo -e "  1) ${GREEN}IP Mode${NC} (Easy) - http://$PUBLIC_IP"
        echo -e "  2) ${GREEN}SSL Mode${NC} (Prod) - https://your-domain.com (Requires DNS A Record pointing to $PUBLIC_IP)"
        read -p "Enter choice [1]: " MODE_CHOICE < /dev/tty
        MODE_CHOICE=${MODE_CHOICE:-1}
    fi

    # Set configuration based on choice or presets
    if is_node_mode; then
        if [ "${PRESET_USE_SSL}" != "true" ] || [ -z "${PRESET_DOMAIN:-}" ]; then
            USE_SSL="false"
            DOMAIN="${DOMAIN:-$PUBLIC_IP}"
        fi
    elif [ "$MODE_CHOICE" -eq "2" ] || [ "${PRESET_USE_SSL}" = "true" ]; then
        USE_SSL="true"
        DOMAIN="${PRESET_DOMAIN:-}"
        ACME_EMAIL="${PRESET_ACME_EMAIL:-}"

        if [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
            while [ -z "$DOMAIN" ]; do
                read -p "Enter your Domain (e.g., app.example.com): " DOMAIN < /dev/tty
            done
            while [ -z "$ACME_EMAIL" ]; do
                read -p "Enter Email for SSL (e.g., admin@example.com): " ACME_EMAIL < /dev/tty
            done
        fi

        if [ -n "$DOMAIN" ]; then
            echo -e "${BLUE}  → Verifying DNS for $DOMAIN...${NC}"
            DETECTED_IP=""
            # Try 'host' first (dnsutils), fall back to API-based DNS lookup
            if command -v host ; then
                DETECTED_IP=$(host -t A "$DOMAIN"  | awk '{print $NF}' | tail -n 1)
            fi
            if [ -z "$DETECTED_IP" ] || [ "$DETECTED_IP" = "found:" ] || [ "$DETECTED_IP" = "not" ]; then
                DETECTED_IP=""
                # Fallback to DNS over HTTPS (Google)
                DETECTED_IP="$(curl -fsS "https://dns.google/resolve?name=${DOMAIN}&type=A" -m 5  | python3 -c "import json,sys; data=json.load(sys.stdin); ans=data.get('Answer',[]); print(ans[0]['data']) if ans and 'data' in ans[0] else print('')"  || echo "")"
            fi
            if [ -n "$DETECTED_IP" ]; then
                if [ "$DETECTED_IP" != "$PUBLIC_IP" ] && [ "$DETECTED_IP" != "127.0.0.1" ]; then
                    echo -e "${YELLOW}  ⚠ WARNING: DNS for $DOMAIN ($DETECTED_IP) does not match this server ($PUBLIC_IP).${NC}"
                    echo -e "${YELLOW}  SSL generation may fail. Ensure your DNS A record is set.${NC}"
                    if [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
                        read -p "  Continue anyway? (y/n) " -n 1 -r < /dev/tty
                        echo
                        if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 1; fi
                    fi
                else
                    echo -e "${GREEN}  ✓ DNS looks correct.${NC}"
                fi
            else
                echo -e "${YELLOW}  ⚠ Could not resolve DNS for $DOMAIN. SSL may fail.${NC}"
                echo -e "${YELLOW}  Ensure your DNS A record points to $PUBLIC_IP${NC}"
            fi
        fi
    else
        USE_SSL="false"
        DOMAIN="${DOMAIN:-$PUBLIC_IP}"
        echo -e "${BLUE}  → Using IP Mode: $DOMAIN${NC}"
    fi

    # ─── Wildcard Subdomain & Cloudflare Setup (Front-loaded) ────────────
    WILDCARD_SUBDOMAINS="false"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
    if [ "$USE_SSL" = "true" ] && [ -n "$DOMAIN" ] && [ "$DOMAIN" != "$PUBLIC_IP" ]; then
        echo ""
        echo -e "${BLUE}  Wildcard subdomains allow deployed services to get automatic SSL.${NC}"
        echo -e "  e.g., myapp-abc123.${DOMAIN} will automatically have HTTPS."
        echo -e "  This requires a Cloudflare API Token with DNS:Edit permission.\n"

        if [ -n "${CLOUDFLARE_API_TOKEN}" ]; then
            WILDCARD_SUBDOMAINS="true"
            echo -e "${BLUE}  → Preset Cloudflare token detected. Enabling wildcard subdomains.${NC}"
        elif [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
            read -p "  Enable wildcard subdomains? (y/n) [n]: " WILDCARD_CHOICE < /dev/tty
            WILDCARD_CHOICE=${WILDCARD_CHOICE:-n}
            if [[ $WILDCARD_CHOICE =~ ^[Yy]$ ]]; then
                WILDCARD_SUBDOMAINS="true"
                while [ -z "$CLOUDFLARE_API_TOKEN" ]; do
                    read -p "  Enter Cloudflare API Token (DNS:Edit): " CLOUDFLARE_API_TOKEN < /dev/tty
                    echo
                done
                echo -e "${GREEN}  ✓ Wildcard subdomains enabled.${NC}"
            fi
        fi
    fi
fi

if is_node_mode; then
    PUBLIC_IP="${PUBLIC_IP:-$(detect_public_ip)}"
    if [ "${USE_SSL:-false}" != "true" ] || [ -z "${DOMAIN:-}" ] || echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        USE_SSL="false"
        DOMAIN="${DOMAIN:-$PUBLIC_IP}"
    fi
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-false}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
fi
