is_agent_lite_mode() {
    [ "${INSTALL_MODE:-master}" = "agent-lite" ] || [ "${MODE_AGENT_LITE:-false}" = "true" ]
}

is_node_mode() {
    [ "${INSTALL_MODE:-master}" = "node" ] || [ "${MODE_NODE:-false}" = "true" ]
}

is_master_mode() {
    [ "${INSTALL_MODE:-master}" = "master" ] \
        && [ "${MODE_AGENT_LITE:-false}" != "true" ] \
        && [ "${MODE_NODE:-false}" != "true" ]
}

should_manage_caddy() {
    is_master_mode
}

mode_env_value() {
    if is_agent_lite_mode; then
        printf '%s\n' "agent"
    elif is_node_mode; then
        printf '%s\n' "node"
    else
        printf '%s\n' "master"
    fi
}

sync_install_mode_env_file() {
    local env_file="$1"
    [ -f "$env_file" ] || return 0

    local node_type="${INSTALL_MODE:-master}"
    local mode_value
    local traefik_bind="127.0.0.1:8081"
    local startup_caddy_sync="true"
    mode_value="$(mode_env_value)"

    if is_agent_lite_mode; then
        node_type="agent-lite"
        startup_caddy_sync="false"
    elif is_node_mode; then
        node_type="node"
        traefik_bind="0.0.0.0:80"
        startup_caddy_sync="false"
        env_set_value "$env_file" "COMPOSE_FILE" "infrastructure/docker/docker-compose.node.yml"
    fi

    env_set_value "$env_file" "NODE_TYPE" "$node_type"
    env_set_value "$env_file" "MODE" "$mode_value"
    env_set_value "$env_file" "TRAEFIK_HTTP_BIND" "$traefik_bind"
    env_set_value "$env_file" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "$startup_caddy_sync"
}
load_install_env_defaults() {
    local env_file="${1:-$INSTALL_DIR/.env}"
    local env_domain=""
    local env_public_ip=""
    local env_use_ssl=""
    local env_wildcard=""
    local env_acme_email=""
    local env_cloudflare_token=""
    local env_master_ip=""

    if [ -f "$env_file" ]; then
        env_domain="$(env_get_value "$env_file" "DOMAIN")"
        env_public_ip="$(env_get_value "$env_file" "PUBLIC_IP")"
        env_use_ssl="$(env_get_value "$env_file" "USE_SSL")"
        env_wildcard="$(env_get_value "$env_file" "WILDCARD_SUBDOMAINS")"
        env_acme_email="$(env_get_value "$env_file" "ACME_EMAIL")"
        env_cloudflare_token="$(env_get_value "$env_file" "CLOUDFLARE_API_TOKEN")"
        env_master_ip="$(env_get_value "$env_file" "MASTER_IP")"
    fi

    PUBLIC_IP="${PUBLIC_IP:-$env_public_ip}"
    if [ -z "${PUBLIC_IP:-}" ]; then
        PUBLIC_IP="$(detect_public_ip)"
    fi

    DOMAIN="${DOMAIN:-$env_domain}"
    DOMAIN="${DOMAIN:-$PUBLIC_IP}"

    # SEC-002: IP-mode SSL guard — always force USE_SSL=false for raw IPs,
    # regardless of env var override. Let's Encrypt cannot issue certs for IPs.
    if [[ "$DOMAIN" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        if [ "${USE_SSL:-}" = "true" ]; then
            echo -e "${YELLOW}  ⚠ SEC-002: USE_SSL=true ignored — DOMAIN ($DOMAIN) is a raw IP. Forcing USE_SSL=false.${NC}"
        fi
        USE_SSL="false"
        echo -e "${BLUE}  → IP mode confirmed: USE_SSL forced to false${NC}"
    else
        USE_SSL="${USE_SSL:-$env_use_ssl}"
    fi
    USE_SSL="${USE_SSL:-false}"
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-$env_wildcard}"
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-false}"
    ACME_EMAIL="${ACME_EMAIL:-$env_acme_email}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-$env_cloudflare_token}"
    MASTER_IP="${MASTER_IP:-$env_master_ip}"
}

compose_stack_drift() {
    local services=""
    local service=""
    local container_id=""
    local container_state=""

    if ! services="$(compose_stack_services 2>/tmp/smsly-compose-config.err)"; then
        echo "__compose_config__:invalid"
        sed 's/^/__compose_config_error__:/' /tmp/smsly-compose-config.err  | head -5 || true
        return 0
    fi

    printf '%s\n' "$services" | while IFS= read -r service; do
        [ -n "$service" ] || continue
        container_id="$(docker compose -f "$COMPOSE_FILE" ps -q "$service"  || true)"
        if [ -z "$container_id" ]; then
            echo "$service:missing"
            continue
        fi
        container_state="$(docker inspect -f '{{.State.Status}}' "$container_id"  || true)"
        if [ "$container_state" != "running" ]; then
            echo "$service:${container_state:-unknown}"
        fi
    done
}

reconcile_compose_stack_after_resume() {
    local drift=""
    local reconcile_rc=0

    drift="$(compose_stack_drift || true)"
    if [ -z "$drift" ]; then
        return 0
    fi

    echo -e "${YELLOW}  -> Resumed checkpoint is stale; reconciling compose stack:${NC}"
    printf '%s\n' "$drift" | sed 's/^/     - /'

    set +e; compose_stack_up --remove-orphans; reconcile_rc=$?; set -e
    if [ "$reconcile_rc" -ne 0 ]; then
        echo -e "${YELLOW}  -> Compose reconciliation needs a rebuild; rebuilding stack...${NC}"
        echo -e "${YELLOW}    ↳ Rebuilding with --no-cache to ensure clean state...${NC}"
        set +e; compose_stack_build --no-cache; reconcile_rc=$?; set -e
        if [ "$reconcile_rc" -eq 0 ]; then
            set +e; compose_stack_up --remove-orphans; reconcile_rc=$?; set -e
        fi
    fi

    if [ "$reconcile_rc" -ne 0 ]; then
        echo -e "${RED}  x Compose reconciliation failed (exit $reconcile_rc).${NC}"
        docker compose -f "$COMPOSE_FILE" ps  || true
        docker compose -f "$COMPOSE_FILE" logs --tail=120  || true
        exit "$reconcile_rc"
    fi

    echo -e "${GREEN}  OK Compose stack reconciled after resume${NC}"
}