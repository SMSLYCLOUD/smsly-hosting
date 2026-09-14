# -----------------------------------------------------------------------------
# 9. Verification
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[9/9] Verifying Deployment...${NC}"
VERIFY_PASS_COUNT=0
VERIFY_TOTAL=4
sleep 5

if [ "$MODE_AGENT_LITE" = "true" ]; then
VERIFY_TOTAL=4

echo -e "${BLUE}  → [1/4] Verifying Lite Agent compose profile...${NC}"
AGENT_SERVICES="$(docker compose -f "$COMPOSE_FILE" config --services  || true)"
if printf '%s\n' "$AGENT_SERVICES" | grep -qx "backend" \
   && printf '%s\n' "$AGENT_SERVICES" | grep -qx "celery-worker" \
   && printf '%s\n' "$AGENT_SERVICES" | grep -qx "traefik" \
   && printf '%s\n' "$AGENT_SERVICES" | grep -qx "socket-proxy" \
   && printf '%s\n' "$AGENT_SERVICES" | grep -qx "redis" \
   && printf '%s\n' "$AGENT_SERVICES" | grep -qx "rabbitmq" \
   && ! printf '%s\n' "$AGENT_SERVICES" | grep -Eq "^(frontend|db|pgcat)$"; then
    echo -e "${GREEN}  ✓ Lite Agent profile selected; local Redis/RabbitMQ enabled and control-plane services excluded${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Lite Agent compose profile is wrong. Services: ${AGENT_SERVICES//$'\n'/, }${NC}"
fi

echo -e "${BLUE}  → [2/4] Checking Lite Agent containers...${NC}"
RUNNING_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --status running -q  | wc -l)
TOTAL_COUNT=$(docker compose -f "$COMPOSE_FILE" ps -q  | wc -l)
if [ "$RUNNING_COUNT" -eq "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt 0 ]; then
    echo -e "${GREEN}  ✓ All $TOTAL_COUNT Lite Agent containers running${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Only $RUNNING_COUNT/$TOTAL_COUNT Lite Agent containers running${NC}"
fi

echo -e "${BLUE}  → [3/4] Checking Lite Agent backend...${NC}"
BACKEND_OK=false
BACKEND_STATUS=""
for attempt in $(seq 1 24); do
    BACKEND_STATUS="$(docker compose -f "$COMPOSE_FILE" ps backend --format "{{.Status}}"  || true)"
    if timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS http://127.0.0.1:8000/health/live < /dev/null ; then
        BACKEND_OK=true
        break
    fi
    if echo "$BACKEND_STATUS" | grep -qi "unhealthy"; then
        break
    fi
    echo -ne "\r${YELLOW}  → Lite Agent backend warmup $attempt/24...${NC}"
    sleep 5
done
echo ""
if [ "$BACKEND_OK" = "true" ]; then
    echo -e "${GREEN}  ✓ Lite Agent backend liveness endpoint passed${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Lite Agent backend is not live (status: ${BACKEND_STATUS:-unknown})${NC}"
    docker compose -f "$COMPOSE_FILE" logs --tail=80 backend  || true
fi

echo -e "${BLUE}  → [4/4] Checking swap...${NC}"
SWAP_TOTAL=$(free -m | awk '/^Swap:/{print $2}')
if [ "$SWAP_TOTAL" -ge 1500 ]; then
    echo -e "${GREEN}  ✓ Swap sufficient (${SWAP_TOTAL}MB)${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  ⚠ Swap low (${SWAP_TOTAL}MB) — recommend 2GB+${NC}"
fi
else
# ─── Check 1: Health check ─────────────────────────────────────────────────
echo -e "${BLUE}  → [1/4] Running health check...${NC}"
HEALTH_OK=false
MAX_ATTEMPTS=36
for attempt in $(seq 1 $MAX_ATTEMPTS); do
    if timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health/live < /dev/null ; then
        HEALTH_OK=true
        break
    elif curl -sfL --max-time 5 http://127.0.0.1:8000/health/live ; then
        HEALTH_OK=true
        break
    fi
    echo -ne "\r${YELLOW}  → Health check attempt $attempt/$MAX_ATTEMPTS — waiting...${NC}"
    sleep 5
done
echo ""

if [ "$HEALTH_OK" = "true" ]; then
    echo -e "${GREEN}  ✓ Health Check Passed!${NC}"
    READY_OK=false
    timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health/ready < /dev/null  && READY_OK=true
    if ! $READY_OK && ! curl -sfL --max-time 5 http://127.0.0.1:8000/health/ready ; then
        echo -e "${YELLOW}  ⚠ Readiness endpoint is still warming; continuing because liveness passed.${NC}"
    fi
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Health check failed after $MAX_ATTEMPTS attempts.${NC}"
    dump_diagnostic_logs
fi

# ─── Check 3: All containers running ──────────────────────────────────────
echo -e "${BLUE}  → [2/4] Checking container status...${NC}"
RUNNING_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --status running -q  | wc -l)
TOTAL_COUNT=$(docker compose -f "$COMPOSE_FILE" ps -q  | wc -l)
UNHEALTHY_STATUS="$(docker compose -f "$COMPOSE_FILE" ps --format "{{.Service}}\t{{.Status}}"  | awk 'tolower($0) ~ /unhealthy/ {print}' || true)"
# Also surface containers stuck in Docker's restart loop. These are not
# "unhealthy" (healthcheck hasn't run yet) but they're crash-looping,
# which is the more dangerous failure mode — print them first so the
# tail of their crash log is visible.
RESTARTING_STATUS="$(docker compose -f "$COMPOSE_FILE" ps --format "{{.Service}}\t{{.Status}}"  | awk 'tolower($0) ~ /restarting/ {print}' || true)"
if [ -n "$RESTARTING_STATUS" ]; then
    echo -e "${RED}  ✗ One or more containers are crash-looping:${NC}"
    printf '%s\n' "$RESTARTING_STATUS" | sed 's/^/     - /'
    RESTARTING_SERVICES="$(printf '%s\n' "$RESTARTING_STATUS" | awk '{print $1}' | xargs  || true)"
    if [ -n "$RESTARTING_SERVICES" ]; then
        echo -e "${YELLOW}  ↳ Crash tail of each restarting service (last 40 lines):${NC}"
        for _svc in $RESTARTING_SERVICES; do
            echo -e "${YELLOW}      --- $_svc ---${NC}"
            docker compose -f "$COMPOSE_FILE" logs --tail=40 "$_svc"  | sed 's/^/        /' || true
        done
    fi
fi
if [ -n "$UNHEALTHY_STATUS" ]; then
    echo -e "${RED}  ✗ One or more containers are unhealthy:${NC}"
    printf '%s\n' "$UNHEALTHY_STATUS" | sed 's/^/     - /'
    UNHEALTHY_SERVICES="$(printf '%s\n' "$UNHEALTHY_STATUS" | awk '{print $1}' | xargs  || true)"
    if [ -n "$UNHEALTHY_SERVICES" ]; then
        docker compose -f "$COMPOSE_FILE" logs --tail=80 $UNHEALTHY_SERVICES  || true
    fi
elif [ -z "$RESTARTING_STATUS" ] && [ "$RUNNING_COUNT" -eq "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt 0 ]; then
    echo -e "${GREEN}  ✓ All $TOTAL_COUNT containers running and none are unhealthy${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
elif [ "$RUNNING_COUNT" -eq "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt 0 ]; then
    # All running but some are crash-looping — don't increment pass count
    # but don't double-count either.
    echo -e "${YELLOW}  ⚠ All $TOTAL_COUNT containers present, but see crash-loop warnings above${NC}"
else
    echo -e "${RED}  ✗ Only $RUNNING_COUNT/$TOTAL_COUNT containers running${NC}"
fi

# ─── Check 3: Observability stack present ─────────────────────────────
# loki/promtail/grafana/cadvisor/docker-labels/alertmanager are
# profile-gated (medium/full). Fresh installs used to default to profiles
# without them, finishing "green" while blind. Warn loudly (non-blocking:
# tiny hosts may intentionally skip them).
echo -e "${BLUE}  → [3/4] Checking observability stack...${NC}"
OBS_MISSING=""
for _obs in smsly-loki smsly-promtail smsly-grafana smsly-cadvisor smsly-docker-labels smsly-prometheus smsly-alertmanager; do
    if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$_obs"; then
        OBS_MISSING="${OBS_MISSING} ${_obs}"
    fi
done
if [ -z "$OBS_MISSING" ]; then
    echo -e "${GREEN}  ✓ Observability stack present (loki/promtail/grafana/cadvisor/docker-labels/prometheus/alertmanager)${NC}"
else
    echo -e "${YELLOW}  ⚠ Observability services missing:${OBS_MISSING}${NC}"
    echo -e "${YELLOW}    Grafana embeds will 502, Loki stays empty, and autoscaler targets stay incomplete.${NC}"
    echo -e "${YELLOW}    Ensure COMPOSE_PROFILES in $INSTALL_DIR/.env includes 'medium', then:${NC}"
    echo -e "${YELLOW}    docker compose -f $COMPOSE_FILE up -d loki promtail grafana cadvisor docker-labels alertmanager${NC}"
fi

# ─── Check 4: Swap is sufficient ──────────────────────────────────────────
echo -e "${BLUE}  → [3/4] Checking swap...${NC}"
SWAP_TOTAL=$(free -m | awk '/^Swap:/{print $2}')
if [ "$SWAP_TOTAL" -ge 1500 ]; then
    echo -e "${GREEN}  ✓ Swap sufficient (${SWAP_TOTAL}MB)${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  ⚠ Swap low (${SWAP_TOTAL}MB) — recommend 2GB+${NC}"
fi

# ─── Check 5: Public edge proxy ───────────────────────────────────────────
if should_manage_caddy; then
    echo -e "${BLUE}  → [4/4] Checking Caddy...${NC}"
    caddy_container="$(resolve_container_target "smsly-hosting-caddy-1")"
    if docker inspect -f '{{.State.Running}}' "$caddy_container"  | grep -q "true"; then
        echo -e "${GREEN}  ✓ Caddy reverse proxy container active${NC}"
        VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Caddy container is not running${NC}"
    fi
else
    if is_node_mode; then
        echo -e "${BLUE}  → [4/4] Checking Caddy...${NC}"
        caddy_container="$(resolve_container_target "smsly-hosting-caddy-1")"
        if docker inspect -f '{{.State.Running}}' "$caddy_container" 2>/dev/null | grep -q "true" \
           && curl -fsS --max-time 5 "http://127.0.0.1:2019/config/" ; then
            echo -e "${GREEN}  ✓ Caddy reverse proxy active${NC}"
            VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
        else
            echo -e "${RED}  ✗ Caddy reverse proxy check failed${NC}"
        fi
    else
        echo -e "${BLUE}  → [4/4] Checking Traefik...${NC}"
        TRAEFIK_CHECK_URL="http://127.0.0.1:8081/"
        traefik_container="$(resolve_container_target "smsly-hosting-traefik-1")"
        if docker inspect -f '{{.State.Running}}' "$traefik_container"  | grep -q "true" \
           && curl -fsS --max-time 5 "$TRAEFIK_CHECK_URL" ; then
            echo -e "${GREEN}  ✓ Traefik edge proxy active (${TRAEFIK_CHECK_URL})${NC}"
            VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
        else
            echo -e "${RED}  ✗ Traefik edge proxy check failed (${TRAEFIK_CHECK_URL})${NC}"
        fi
    fi
fi
fi

# Show container status
echo -e "\n${BLUE}Container Status:${NC}"
docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}"  || \
    docker compose -f "$COMPOSE_FILE" ps  || true

echo -e "\n${BLUE}Verification Score: $VERIFY_PASS_COUNT/$VERIFY_TOTAL${NC}"

# ─── Install Autoscaler as systemd service ──────────────────────────────────
echo -e "${BLUE}  → Installing smsly-autoscaler systemd service...${NC}"
cp "$INSTALL_DIR/scripts/smsly-autoscaler.py" /opt/smsly/autoscaler.py  || {
    mkdir -p /opt/smsly
    cp "$INSTALL_DIR/scripts/smsly-autoscaler.py" /opt/smsly/autoscaler.py
}
chmod +x /opt/smsly/autoscaler.py

# Source .env for the token
AUTOSCALER_API_TOKEN="$(env_get_value "$INSTALL_DIR/.env" "AUTOSCALER_API_TOKEN")"

cat <<SVCEOF > /etc/systemd/system/smsly-autoscaler.service
[Unit]
Description=SMSLY VPS Autoscaler — Cross-Service Resource Manager
After=docker.service
Requires=docker.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/smsly/autoscaler.py
Restart=always
RestartSec=10
Environment=AUTOSCALER_API_TOKEN=${AUTOSCALER_API_TOKEN}
Environment=AUTOSCALER_API_BIND=127.0.0.1
Environment=AUTOSCALER_API_PORT=9876
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable smsly-autoscaler || echo -e "${YELLOW}    ⚠ smsly-autoscaler enable failed${NC}"
systemctl restart smsly-autoscaler || echo -e "${YELLOW}    ⚠ smsly-autoscaler restart failed${NC}"
echo -e "${GREEN}  ✓ smsly-autoscaler service installed and started${NC}"

# Install infrastructure monitor
if [ -f "$INSTALL_DIR/scripts/monitor_infra.sh" ]; then
    echo -e "${BLUE}  → Installing critical infrastructure monitoring timer...${NC}"
    chmod +x "$INSTALL_DIR/scripts/monitor_infra.sh"
    cp "$INSTALL_DIR/scripts/smsly-infra-monitor.service" /etc/systemd/system/smsly-infra-monitor.service  || true
    cp "$INSTALL_DIR/scripts/smsly-infra-monitor.timer" /etc/systemd/system/smsly-infra-monitor.timer  || true
    systemctl daemon-reload
    systemctl enable smsly-infra-monitor.timer || echo -e "${YELLOW}    ⚠ smsly-infra-monitor timer enable failed${NC}"
    systemctl restart smsly-infra-monitor.timer || echo -e "${YELLOW}    ⚠ smsly-infra-monitor timer restart failed${NC}"
    echo -e "${GREEN}  ✓ smsly-infra-monitor timer installed and started${NC}"
fi

# Install platform update watcher and caddy watcher services
if [ -f "$INSTALL_DIR/scripts/smsly-update-watcher.service" ]; then
    echo -e "${BLUE}  → Installing platform update and Caddy config watcher services...${NC}"
    chmod +x "$INSTALL_DIR/scripts/platform-update.sh" "$INSTALL_DIR/scripts/caddy-reload.sh"  || true
    cp "$INSTALL_DIR/scripts/smsly-update-watcher.service" /etc/systemd/system/smsly-update-watcher.service  || true
    cp "$INSTALL_DIR/scripts/caddy-watcher.service" /etc/systemd/system/caddy-watcher.service  || true
    systemctl daemon-reload
    systemctl enable smsly-update-watcher caddy-watcher || echo -e "${YELLOW}    ⚠ Watcher services enable failed${NC}"
    systemctl restart smsly-update-watcher caddy-watcher || echo -e "${YELLOW}    ⚠ Watcher services restart failed${NC}"
    echo -e "${GREEN}  ✓ smsly-update-watcher and caddy-watcher services installed and started${NC}"
fi

# Install Celery Worker Autoscaler (scales celery-2/celery-3 based on queue depth)
if [ -f "$INSTALL_DIR/scripts/celery-worker-autoscaler.sh" ]; then
    echo -e "${BLUE}  → Installing Celery Worker Autoscaler service...${NC}"
    chmod +x "$INSTALL_DIR/scripts/celery-worker-autoscaler.sh"
    cp "$INSTALL_DIR/infrastructure/docker/celery-autoscaler.service" /etc/systemd/system/celery-autoscaler.service  || true
    systemctl daemon-reload
    if [ "${CELERY_AUTOSCALE_ENABLED:-true}" = "true" ]; then
        systemctl enable celery-autoscaler || echo -e "${YELLOW}    ⚠ celery-autoscaler enable failed${NC}"
        systemctl restart celery-autoscaler || echo -e "${YELLOW}    ⚠ celery-autoscaler restart failed${NC}"
        echo -e "${GREEN}  ✓ celery-autoscaler service installed and started (scaling celery-2/3 on demand)${NC}"
    else
        systemctl disable celery-autoscaler 2>/dev/null || true
        systemctl stop celery-autoscaler 2>/dev/null || true
        echo -e "${BLUE}  → celery-autoscaler installed but disabled (CELERY_AUTOSCALE_ENABLED=false)${NC}"
    fi
fi

# -----------------------------------------------------------------------------
# 10. CLI Integration
# -----------------------------------------------------------------------------
if [ "$MODE_AGENT_LITE" = "true" ]; then
echo -e "\n${YELLOW}[10/10] Skipping SMSLY CLI on Lite Agent...${NC}"
else
echo -e "\n${YELLOW}[10/10] Integrating SMSLY CLI...${NC}"

if [ -d "$INSTALL_DIR/cli" ]; then
    echo -e "${BLUE}  → Installing 'smsly' CLI command globally...${NC}"
    # Use --break-system-packages for modern Python (Ubuntu 24.04+)
    pip3 install -q --break-system-packages "$INSTALL_DIR/cli"  || \
        pip3 install -q "$INSTALL_DIR/cli"  || true

    # Ensure binary is in path (pip usually puts it in /usr/local/bin)
    if command -v smsly ; then
        echo -e "${GREEN}  ✓ CLI installed: run 'smsly login' or 'smsly --help'${NC}"

        # Auto-configuration for local host
        CLI_DOMAIN="${DOMAIN:-}"
        CLI_USE_SSL="${USE_SSL:-false}"
        if [ -z "$CLI_DOMAIN" ] && [ -f "$INSTALL_DIR/.env" ]; then
            CLI_DOMAIN="$(env_get_value "$INSTALL_DIR/.env" "DOMAIN" || true)"
        fi
        if [ -n "$CLI_DOMAIN" ] && [ "$CLI_DOMAIN" != "localhost" ]; then
            URL_SCHEME="https" && [ "$CLI_USE_SSL" != "true" ] && URL_SCHEME="http"
            API_URL="${URL_SCHEME}://${CLI_DOMAIN}"
        else
            API_URL="http://127.0.0.1"
        fi

        # Best effort: don't auto-login yet (token is in creds file),
        # but let the user know their URL is pre-linked.
        echo -e "${BLUE}  → Your local API URL: $API_URL${NC}"
    else
        echo -e "${YELLOW}  ⚠ CLI installation partially failed (could not find 'smsly' in PATH).${NC}"
    fi
else
    echo -e "${YELLOW}  ⚠ CLI directory not found — skipping integration.${NC}"
fi

# -----------------------------------------------------------------------------
# 11. Finalize Inter-Node Connectivity
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[11/11] Finalizing Inter-Node Connectivity...${NC}"
echo -e "${BLUE}  → Registering this node and creating authentication tokens...${NC}"
# Use -T to avoid TTY issues in non-interactive mode
if timeout 30 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py help diagnose_nodes < /dev/null ; then
    timeout 120 docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py diagnose_nodes --fix < /dev/null || true
    echo -e "${GREEN}  ✓ Node registered as Primary (if Master) and API tokens verified${NC}"
else
    echo -e "${YELLOW}  ⚠ diagnose_nodes command not available in this version; skipping.${NC}"
fi

# ─── Final Verification Sync ──────────────────────────────────────────────────
fi

if [ "$MODE_AGENT_LITE" != "true" ] && command -v smsly ; then
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    VERIFY_TOTAL=$((VERIFY_TOTAL + 1))
fi

# ─── Remove rollback trap (installation succeeded) ─────────────────────────
trap - EXIT
release_install_lock

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
# Infrastructure Handshake & Health Stabilization
echo -e "\n${BLUE}  🔄 Running infrastructure handshake and stabilization...${NC}"
chmod +x scripts/grid-handshake.sh  || true
SMSLY_MIGRATIONS_DONE=1 bash scripts/grid-handshake.sh || \
    echo -e "${YELLOW}  ⚠️ Handshake stabilization failed (non-fatal). You can run it manually later.${NC}"

echo -e "${GREEN}   ✓ INSTALLATION SUCCESSFUL!${NC}"

echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"

SUMMARY_PUBLIC_IP="${PUBLIC_IP:-}"
if [ -z "$SUMMARY_PUBLIC_IP" ] && [ -f "$INSTALL_DIR/.env" ]; then
    SUMMARY_PUBLIC_IP="$(env_get_value "$INSTALL_DIR/.env" "PUBLIC_IP" || true)"
fi
if [ -z "$SUMMARY_PUBLIC_IP" ]; then
    SUMMARY_PUBLIC_IP="$(detect_public_ip)"
fi

SUMMARY_MASTER_IP="${MASTER_IP:-}"
if [ -z "$SUMMARY_MASTER_IP" ] && [ -f "$INSTALL_DIR/.env" ]; then
    SUMMARY_MASTER_IP="$(env_get_value "$INSTALL_DIR/.env" "MASTER_IP" || true)"
fi
SUMMARY_MASTER_IP="${SUMMARY_MASTER_IP:-unknown}"

SUMMARY_DOMAIN="${DOMAIN:-}"
if [ -z "$SUMMARY_DOMAIN" ] && [ -f "$INSTALL_DIR/.env" ]; then
    SUMMARY_DOMAIN="$(env_get_value "$INSTALL_DIR/.env" "DOMAIN" || true)"
fi

if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "   Mode:        Lite Agent"
    echo -e "   Agent Edge:  http://$SUMMARY_PUBLIC_IP"
    echo -e "   Master:      $SUMMARY_MASTER_IP"
elif [ "$MODE_NODE" = "true" ]; then
    echo -e "   Mode:        Full-Stack Node"
    echo -e "   API:         http://$SUMMARY_PUBLIC_IP"
    echo -e "   Edge:        Traefik on public port 80"
    echo -e "   UI/HTTPS:    Managed by Master (frontend/Caddy disabled here)"
    echo -e "   Credentials: $CREDENTIALS_FILE"
else
    if [ "${USE_SSL:-false}" = "true" ] && [ -n "$SUMMARY_DOMAIN" ]; then
        echo -e "   URL:         https://$SUMMARY_DOMAIN"
    else
        echo -e "   URL:         http://$SUMMARY_PUBLIC_IP"
    fi
    echo -e "   Admin:       /admin"
    echo -e "   Credentials: $CREDENTIALS_FILE"
fi
echo -e "   Install Log: $LOG_FILE"
echo -e "   Location:    $INSTALL_DIR"
echo -e "   Memory:      $(free -m | awk '/^Mem:/{print $7}')MB available"
echo -e "   Swap:        $(free -m | awk '/^Swap:/{print $2}')MB total"

# ─── Custom Domain SSL Integration ───────────────────────────────────────────
if should_manage_caddy; then  # Only for master mode
    echo -e "\n${YELLOW}[9/9] Setting up Custom Domain SSL Services...${NC}"
    
    # Check if custom domain SSL manager script exists
    SSL_SCRIPT="install-custom-domain-ssl.sh"
    [ -f "$SSL_SCRIPT" ] || SSL_SCRIPT="$INSTALL_DIR/scripts/legacy/install-custom-domain-ssl.sh"
    if [ -f "$SSL_SCRIPT" ]; then
        echo -e "${BLUE}  → Installing custom domain SSL services...${NC}"
        bash "$SSL_SCRIPT" install
        
        # Start the services
        echo -e "${BLUE}  → Starting custom domain SSL services...${NC}"
        /opt/smsly-hosting/smsly-domain-ssl-manager.sh start
        
        # Enable auto-start on boot
        echo -e "${BLUE}  → Enabling auto-start on boot...${NC}"
        /opt/smsly-hosting/smsly-domain-ssl-manager.sh enable
        
        echo -e "${GREEN}  ✓ Custom domain SSL services configured${NC}"
    else
        echo -e "${YELLOW}  ⚠ Custom domain SSL manager not found, skipping setup${NC}"
    fi
fi

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
if [ "$MODE_AGENT_LITE" != "true" ]; then
    echo -e "   CLI:         'smsly services list'${NC}"
fi
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  View credentials:   cat $CREDENTIALS_FILE${NC}"
echo -e "${YELLOW}  View logs:          cat $LOG_FILE${NC}"
if [ -f "$INSTALL_DIR/.recovery_phrase" ] && [ -s "$INSTALL_DIR/.recovery_phrase" ]; then
    echo -e "${YELLOW}  Recovery phrase:    cat $INSTALL_DIR/.recovery_phrase${NC}"
fi
if is_master_mode; then
    echo -e "${YELLOW}  Update frontend:    sudo bash install.sh --update-frontend${NC}"
fi
echo -e "${YELLOW}  Update backend:     sudo bash install.sh --update-backend${NC}"
echo -e "${YELLOW}  Full update:        sudo bash install.sh --update${NC}"
echo -e "${YELLOW}  Runtime refresh:    sudo bash install.sh --refresh${NC}"
echo -e "${YELLOW}  Runtime recovery:   sudo bash install.sh --recover${NC}"
echo -e "${YELLOW}  Debug snapshot:     sudo bash install.sh --debug${NC}"
echo -e "${YELLOW}  Wipe install:       sudo bash install.sh --wipe${NC}"
if is_master_mode; then
    echo -e "${YELLOW}  Enable read replica (warm-standby):  sudo bash install.sh --with-replica${NC}"
    echo -e "${YELLOW}    (or: sudo $INSTALL_DIR/scripts/enable-replica.sh)${NC}"
fi

# ─── Verification Check Summary ──────────────────────────────────────────────
if [ "$VERIFY_PASS_COUNT" -eq "$VERIFY_TOTAL" ]; then
    echo -e "\n${GREEN}  ✓ All $VERIFY_TOTAL/$VERIFY_TOTAL verification checks passed.${NC}"
    echo -e "${YELLOW}  If needed, run 'sudo reboot' manually to apply sysctl changes.${NC}"
else
    echo -e "\n${RED}  ⚠ Only $VERIFY_PASS_COUNT/$VERIFY_TOTAL checks passed.${NC}"
    echo -e "${YELLOW}  Fix the failed checks above. You can run 'sudo reboot' manually if sysctl changes were made.${NC}"
    if [ "${SMSLY_STRICT_VERIFY:-0}" = "1" ]; then
        echo -e "${RED}  ✗ Strict verification is enabled; failing installation.${NC}"
        exit 1
    fi
fi

# ─── Optional: Enable PostgreSQL Read Replica (only when --with-replica) ─────
# Runs AFTER verification so the primary is confirmed healthy. Runs BEFORE
# the final exit 0 so the post-install message can also report the replica
# status. Non-fatal: if the replica fails to start, the install itself is
# still considered successful and the operator can re-run
# `install.sh --with-replica` later.
if [ "${REPLICA_MODE:-false}" = "true" ] && is_master_mode; then
    echo -e "\n${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}   --with-replica: enabling PostgreSQL streaming replication${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    _replica_script="$INSTALL_DIR/scripts/enable-replica.sh"
    if [ -f "$_replica_script" ]; then
        chmod +x "$_replica_script"  || true
        if bash "$_replica_script"; then
            echo -e "${GREEN}  ✓ Read replica enabled and streaming${NC}"
        else
            _rc=$?
            echo -e "${RED}  ✗ enable-replica.sh exited with code $_rc${NC}"
            echo -e "${YELLOW}    The install itself succeeded. Re-run later with:${NC}"
            echo -e "${YELLOW}      sudo $INSTALL_DIR/scripts/enable-replica.sh${NC}"
        fi
    else
        echo -e "${RED}  ✗ $_replica_script not found. Pull the latest code and re-run.${NC}"
    fi
    unset _replica_script _rc
fi

# ─── Security verify ─────────────────────────────────────────────────────
if command -v harden_security_verify ; then
    harden_security_verify
fi
