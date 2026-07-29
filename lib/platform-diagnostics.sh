dump_diagnostic_logs() {
    local env_file="${1:-$INSTALL_DIR/.env}"
    echo -e "\n${RED}════════════════════════════════════════════════════════════${NC}"
    echo -e "${RED}   DIAGNOSTIC LOG DUMP (FAILURE ANALYSIS)${NC}"
    echo -e "${RED}════════════════════════════════════════════════════════════${NC}"

    echo -e "${YELLOW}  → System Resource Snapshot:${NC}"
    free -m
    df -h /

    echo -e "\n${YELLOW}  → Container Status:${NC}"
    if command -v docker  && [ -f "$env_file" ] && grep -q '^POSTGRES_PASSWORD=' "$env_file" ; then
        docker compose -f "$COMPOSE_FILE" ps || true

        echo -e "\n${YELLOW}  -> Compose Logs (Last 50 lines):${NC}"
        docker compose -f "$COMPOSE_FILE" logs --tail=50 || true
    else
        echo -e "${YELLOW}  (Docker or .env not ready; skipping container logs)${NC}"
    fi

    echo -e "${RED}════════════════════════════════════════════════════════════${NC}\n"
}
