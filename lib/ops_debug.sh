debug_platform_status() {
    # TODO(install): replace set -e toggle with explicit conditional. The
    # entire body tolerates command failures (each diagnostic line has its own
    # `|| true` or ``); leaving set -e toggled off is functional
    # but discouraged.
    set +e
    echo -e "\n${YELLOW}=== SMSLY DEBUG SNAPSHOT ===${NC}"
    echo "Timestamp: $(date -Iseconds)"
    echo "Install dir: $INSTALL_DIR"
    echo ""

    echo "---- Systemd ----"
    systemctl is-active docker  || true
    true
    true
    systemctl is-active smsly-autoscaler  || true
    echo ""

    echo "---- Docker Networks ----"
    docker network ls | grep -E 'smsly|socket-proxy' || true
    echo ""

    echo "---- Compose PS ----"
    docker compose -f "$COMPOSE_FILE" ps || true
    echo ""

    echo "---- Local Health ----"
    timeout 10 curl -iSsf http://127.0.0.1:8000/health  | head -20 || echo "http://127.0.0.1:8000/health failed"
    echo ""

    echo "---- Backend DNS Checks ----"
    timeout 15 docker compose -f "$COMPOSE_FILE" exec -T backend getent hosts db pgcat redis  || echo "backend DNS check failed"
    echo ""

    echo "---- Key Logs (tail 120) ----"
    docker compose -f "$COMPOSE_FILE" logs --tail=120 backend frontend traefik pgcat redis  || true
    echo -e "${YELLOW}=== END DEBUG SNAPSHOT ===${NC}\n"
    set -e
}
