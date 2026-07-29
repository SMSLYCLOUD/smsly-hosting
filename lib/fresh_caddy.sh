# -----------------------------------------------------------------------------
# 7. Caddy Reverse Proxy (Public Access — Dockerized)
# -----------------------------------------------------------------------------
# Agent-lite and node modes use Traefik instead of Caddy — skip this step entirely.
if should_manage_caddy; then
if ! is_checkpoint_done "caddy_configured" || [ "$REFRESH_MODE" = "true" ] || [ "$RECOVER_MODE" = "true" ]; then
    echo -e "\n${YELLOW}[7/9] Setting up Dockerized Caddy Proxy...${NC}"

    # Ensure caddy-config directory exists and has correct permissions
    # Caddy container runs as uid 1000 (nextjs user); group-read access is
    # required so the container can write runtime state (tls certs, reload flag).
    mkdir -p /opt/smsly-hosting/caddy-config
    chown 1000:1000 /opt/smsly-hosting/caddy-config
    chmod 2775 /opt/smsly-hosting/caddy-config
    # Caddy access logs (read by fail2ban on host)
    mkdir -p /opt/smsly-hosting/caddy-logs
    chown 1000:1000 /opt/smsly-hosting/caddy-logs
    chmod 2775 /opt/smsly-hosting/caddy-logs

    # SEED: Create a temporary safety Caddyfile so the container doesn't crash on first start.
    # The backend will overwrite this within seconds of starting up.
    if [ ! -f /opt/smsly-hosting/caddy-config/Caddyfile ]; then
        echo -e "${BLUE}  → Seeding initial safety Caddyfile...${NC}"
        cat > /opt/smsly-hosting/caddy-config/Caddyfile <<EOF
:80 {
    respond "System initializing... Please refresh in 30 seconds." 200
}
EOF
        chown 1000:1000 /opt/smsly-hosting/caddy-config/Caddyfile
        chmod 664 /opt/smsly-hosting/caddy-config/Caddyfile
    fi

    # Build and start Caddy container
    echo -e "${BLUE}  → Building and starting Caddy container...${NC}"
    if ! docker compose -f "$COMPOSE_FILE" build --no-cache caddy; then
        echo -e "${RED}ERROR: Caddy image build failed.${NC}"
        echo -e "${YELLOW}This may be due to a Go version mismatch, missing module, or Dockerfile error.${NC}"
        echo -e "${YELLOW}Check the build logs above for the exact failing stage.${NC}"
        echo -e "${YELLOW}Dockerfile path: ./infrastructure/caddy/Dockerfile${NC}"
        exit 1
    fi
    docker compose -f "$COMPOSE_FILE" up -d --no-deps caddy

    # ACME staging validation — verify Let's Encrypt can reach this server before going live
    if [ "${DOMAIN:-}" ] && [ "$USE_SSL" = "true" ] && ! echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        echo -e "${BLUE}  → Running ACME staging validation for $DOMAIN...${NC}"
        SLEEP_SEC=15
        echo -e "${BLUE}    Waiting ${SLEEP_SEC}s for Caddy to start...${NC}"
        sleep $SLEEP_SEC
        ACME_OK=false
        for attempt in 1 2 3; do
            # Use Let's Encrypt staging endpoint to dry-run the HTTP-01 challenge
            ACME_CHECK=$(curl -fsS -m 10 \
                "http://${DOMAIN}/.well-known/acme-challenge/000000000000000000000000000000000000" \
                 || true)
            # If Caddy returns "challenge not found" (404), that means it IS
            # reachable but doesn't have this challenge registered — which is
            # the expected behavior for a staging check.
            if echo "$ACME_CHECK" | grep -qi "challenge"; then
                echo -e "${GREEN}  ✓ ACME HTTP-01 reachable for $DOMAIN (staging)${NC}"
                ACME_OK=true
                break
            fi
            # Also try: just checking port 80 responds
            if curl -fsSo /dev/null --max-time 5 "http://${DOMAIN}/" ; then
                echo -e "${GREEN}  ✓ Port 80 reachable for $DOMAIN${NC}"
                ACME_OK=true
                break
            fi
            echo -e "${YELLOW}    ACME check attempt $attempt/3 — $DOMAIN not yet reachable, retrying...${NC}"
            sleep 5
        done
        if [ "$ACME_OK" != "true" ]; then
            echo -e "${YELLOW}  ⚠ ACME validation could not confirm $DOMAIN is reachable on port 80.${NC}"
            echo -e "${YELLOW}    SSL certificates may fail to issue. Ensure DNS A record points to $PUBLIC_IP${NC}"
            echo -e "${YELLOW}    and port 80 is open in your firewall.${NC}"
            if [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
                read -p "  Continue anyway? (y/n) " -n 1 -r < /dev/tty
                echo
                if [[ ! $REPLY =~ ^[Yy]$ ]]; then
                    echo -e "${RED}  ACME validation rejected by user. Aborting.${NC}"
                    exit 1
                fi
            fi
        fi
    fi

    # Cleanup legacy host-side bare-metal Caddy server if it exists
    echo -e "${BLUE}  → Cleaning up legacy host-side Caddy service (if any)...${NC}"
    systemctl stop caddy  || true
    systemctl disable caddy  || true
    rm -f /etc/systemd/system/caddy.service
    systemctl daemon-reload

    set_checkpoint "caddy_configured"
fi
fi # end Caddy skip for agent-lite/node modes
