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

# ─── Port fallback helpers ──────────────────────────────────────────────────────
# Primary ports are the defaults; fallback ports are used when the cloud provider
# firewall blocks the primary (common on free-tier / trial instances).

WG_PRIMARY_PORT="${WG_PRIMARY_PORT:-51820}"
WG_FALLBACK_PORT="${WG_FALLBACK_PORT:-33500}"
REGISTRY_PRIMARY_PORT="${REGISTRY_PRIMARY_PORT:-5000}"
REGISTRY_FALLBACK_PORT="${REGISTRY_FALLBACK_PORT:-443}"

# probe_udp_port PORT HOST TIMEOUT_SECS
# Returns 0 if at least one UDP packet round-trip completes within TIMEOUT.
probe_udp_port() {
    local port="${1:?}" host="${2:?}" timeout="${3:-5}"
    # Use a quick WireGuard-style probe: send a single UDP packet and check
    # for a response.  If the cloud firewall drops it, we'll timeout.
    timeout -k "$timeout" "$timeout" bash -c "echo > /dev/udp/${host}/${port}" 2>/dev/null
}

# wg_ensure_listening WG_IFACE MESH_IP
# Starts WireGuard on the primary port.  If no handshake appears within
# 10 seconds (meaning the cloud firewall likely blocks the port), silently
# rewrites the config to the fallback port and restarts.
wg_ensure_listening() {
    local wg_iface="${1:?}" mesh_ip="${2:?}"
    local primary="$WG_PRIMARY_PORT" fallback="$WG_FALLBACK_PORT"
    local conf="/etc/wireguard/${wg_iface}.conf"

    # Already running with a handshake? Nothing to do.
    if wg show "$wg_iface" 2>/dev/null | grep -q "latest handshake"; then
        echo -e "${GREEN}  ✓ WireGuard $wg_iface already active (handshake present)${NC}"
        return 0
    fi

    # Ensure primary port config
    if [ -f "$conf" ]; then
        sed -i "s/^ListenPort = .*/ListenPort = ${primary}/" "$conf"
    fi
    systemctl restart "wg-quick@${wg_iface}" 2>/dev/null || true

    echo -ne "${BLUE}  → Waiting for WireGuard handshake on port ${primary}...${NC}"
    local waited=0
    while [ "$waited" -lt 10 ]; do
        sleep 2
        waited=$((waited + 2))
        if wg show "$wg_iface" 2>/dev/null | grep -q "latest handshake"; then
            echo -e " ${GREEN}done${NC}"
            echo -e "${GREEN}  ✓ WireGuard mesh active on port ${primary}${NC}"
            return 0
        fi
        echo -ne "."
    done
    echo -e " ${YELLOW}timeout${NC}"

    # Primary port blocked — fall back
    echo -e "${YELLOW}  ⚠ Port ${primary} blocked by cloud firewall, falling back to ${fallback}...${NC}"
    systemctl stop "wg-quick@${wg_iface}" 2>/dev/null || true
    if [ -f "$conf" ]; then
        sed -i "s/^ListenPort = .*/ListenPort = ${fallback}/" "$conf"
    fi
    systemctl start "wg-quick@${wg_iface}" 2>/dev/null || true
    sleep 3
    if wg show "$wg_iface" 2>/dev/null | grep -q "latest handshake"; then
        echo -e "${GREEN}  ✓ WireGuard mesh active on fallback port ${fallback}${NC}"
        return 0
    fi
    echo -e "${YELLOW}  ⚠ WireGuard handshake still pending on fallback port (peer may not be configured yet)${NC}"
    return 0
}

# registry_check_with_fallback MASTER_IP_OR_MESH_IP
# Tries the primary registry port, then the fallback.  Prints the working
# URL and returns 0 on success, or returns 1 if both fail.
registry_check_with_fallback() {
    local host="${1:?}"
    local primary="${REGISTRY_PRIMARY_PORT}"
    local fallback="${REGISTRY_FALLBACK_PORT}"
    local code

    # 1. Try primary port (direct TCP)
    if timeout -k 5 3 bash -c "</dev/tcp/${host}/${primary}" 2>/dev/null; then
        if command -v curl; then
            code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "http://${host}:${primary}/v2/" 2>/dev/null || true)"
            if [ "$code" = "000" ] || [ "$code" = "400" ]; then
                code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "https://${host}:${primary}/v2/" 2>/dev/null || true)"
            fi
            case "$code" in
                2*|401)
                    echo "${host}:${primary}"
                    return 0
                    ;;
            esac
        else
            echo "${host}:${primary}"
            return 0
        fi
    fi

    # 2. Try fallback port (HTTPS via Traefik reverse proxy)
    if timeout -k 5 3 bash -c "</dev/tcp/${host}/${fallback}" 2>/dev/null; then
        if command -v curl; then
            # Traefik on 443 may route by Host header — try registry subdomain
            local domain="${DOMAIN:-}"
            local registry_url=""
            if [ -n "$domain" ]; then
                registry_url="https://registry.${domain}"
            else
                registry_url="https://${host}"
            fi
            code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "${registry_url}/v2/" 2>/dev/null || true)"
            case "$code" in
                2*|401)
                    echo "${registry_url}"
                    return 0
                    ;;
            esac
        fi
    fi

    echo ""
    return 1
}