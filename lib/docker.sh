configure_docker_mirror() {
    # Ensure COMPOSE_FILE is defined for this scope
    local compose_f="${COMPOSE_FILE:-docker-compose.prod.yml}"

    if [ "${MODE_AGENT_LITE:-false}" = "true" ] && [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
        [ -n "${MASTER_IP:-}" ] || MASTER_IP="$(env_get_value "${INSTALL_DIR:-/opt/smsly-hosting}/.env" "MASTER_IP" 2>/dev/null || true)"
        [ -n "${MASTER_MESH_IP:-}" ] || MASTER_MESH_IP="$(env_get_value "${INSTALL_DIR:-/opt/smsly-hosting}/.env" "MASTER_MESH_IP" 2>/dev/null || true)"
    fi

    # Detect if we need custom DNS fallback (test resolution inside Docker)
    local use_dns_fallback=false
    if command -v docker >/dev/null 2>&1 && systemctl is-active --quiet docker; then
        echo -e "${BLUE}  → Checking Docker DNS resolution for npm registry...${NC}"
        # Use node:20-alpine if cached, otherwise fallback to alpine
        local test_img="node:20-alpine"
        if ! docker image inspect "$test_img" >/dev/null 2>&1; then
            test_img="alpine"
        fi
        if ! docker run --rm "$test_img" nslookup registry.npmjs.org >/dev/null 2>&1; then
            echo -e "${YELLOW}  ⚠ Docker container DNS test failed. Enabling public DNS fallback (8.8.8.8, 1.1.1.1)...${NC}"
            use_dns_fallback=true
        else
            echo -e "${GREEN}  ✓ Docker container DNS resolution verified.${NC}"
        fi
    fi

    # Option B: Pull-Through Cache
    if [ -n "${MASTER_IP:-}" ] && [ "$MASTER_IP" != "127.0.0.1" ] && [ "$MASTER_IP" != "$(detect_public_ip)" ]; then
        # This is a Follower node
        echo -e "${BLUE}  → Configuring Docker pull-through cache mirror (Master: $MASTER_IP)...${NC}"
        mkdir -p /etc/docker

        # Collect IPs to trust: Public IP, Mesh IP (if provided), and local IPs
        local trust_list="\"${MASTER_IP}:5000\", \"${MASTER_IP}:5001\""
        if [ -n "${MASTER_MESH_IP:-}" ]; then
            trust_list="${trust_list}, \"${MASTER_MESH_IP}:5000\""
        fi

        # Build the daemon.json
        local temp_daemon_json
        temp_daemon_json=$(mktemp)
        if [ "$use_dns_fallback" = "true" ]; then
            cat > "$temp_daemon_json" <<EOF
{
  "registry-mirrors": ["http://${MASTER_IP}:5001"],
  "insecure-registries": [${trust_list}],
  "dns": ["8.8.8.8", "1.1.1.1"]
}
EOF
        else
            cat > "$temp_daemon_json" <<EOF
{
  "registry-mirrors": ["http://${MASTER_IP}:5001"],
  "insecure-registries": [${trust_list}]
}
EOF
        fi
        if [ ! -f /etc/docker/daemon.json ] || ! cmp -s "$temp_daemon_json" /etc/docker/daemon.json; then
            mkdir -p /etc/docker
            cp "$temp_daemon_json" /etc/docker/daemon.json
            systemctl restart docker || true
        fi
        rm -f "$temp_daemon_json"
        echo -e "${GREEN}  ✓ Docker mirror configured${NC}"
    else
        # This is the Master node (or MASTER_IP matches local IP)
        local my_ip my_mesh_ip
        my_ip="$(detect_public_ip)"
        # Note: We don't have a clean 'detect_mesh_ip' here, but we can trust 10.0.0.0/8 range if needed.
        # However, Master usually knows its own identity.
        if [ "$my_ip" != "127.0.0.1" ]; then
            echo -e "${BLUE}  → Configuring Master insecure registry (registry:5000, ${my_ip}:5000)...${NC}"
            mkdir -p /etc/docker
            local master_trust_list="\"127.0.0.1:5000\", \"registry:5000\", \"${my_ip}:5000\""
            if [ -n "${MASTER_MESH_IP:-}" ]; then
                master_trust_list="${master_trust_list}, \"${MASTER_MESH_IP}:5000\""
            fi
            # Registry now has TLS + htpasswd auth — keep insecure flag for self-signed certs
            local temp_daemon_json
            temp_daemon_json=$(mktemp)
            if [ "$use_dns_fallback" = "true" ]; then
                cat > "$temp_daemon_json" <<EOF
{
  "insecure-registries": [${master_trust_list}],
  "dns": ["8.8.8.8", "1.1.1.1"]
}
EOF
            else
                cat > "$temp_daemon_json" <<EOF
{
  "insecure-registries": [${master_trust_list}]
}
EOF
            fi
            if [ ! -f /etc/docker/daemon.json ] || ! cmp -s "$temp_daemon_json" /etc/docker/daemon.json; then
                mkdir -p /etc/docker
                cp "$temp_daemon_json" /etc/docker/daemon.json
                systemctl restart docker || true
            fi
            rm -f "$temp_daemon_json"
        elif [ "$use_dns_fallback" = "true" ]; then
            # If local host (127.0.0.1) but DNS fallback is needed
            local temp_daemon_json
            temp_daemon_json=$(mktemp)
            cat > "$temp_daemon_json" <<EOF
{
  "dns": ["8.8.8.8", "1.1.1.1"]
}
EOF
            if [ ! -f /etc/docker/daemon.json ] || ! cmp -s "$temp_daemon_json" /etc/docker/daemon.json; then
                mkdir -p /etc/docker
                cp "$temp_daemon_json" /etc/docker/daemon.json
                systemctl restart docker || true
            fi
            rm -f "$temp_daemon_json"
        fi

        # Ensure the mirror service is UP if it exists in the compose file
        if [ -f "$compose_f" ] && grep -q "docker-mirror:" "$compose_f"; then
            echo -e "${BLUE}  → Ensuring Docker pull-through cache mirror is running...${NC}"
            docker compose -f "$compose_f" up -d docker-mirror >/dev/null 2>&1 || true
        fi
    fi
}