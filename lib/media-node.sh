# lib/media-node.sh — Media Node provisioning functions
# Sourced by install.sh when --mode=media-node

[ "${_MEDIA_NODE_LOADED:-}" = "true" ] && return 0
_MEDIA_NODE_LOADED=true

MEDIA_NODE_INSTALL_DIR="/opt/smsly-hosting-media"
MEDIA_NODE_ENV="$MEDIA_NODE_INSTALL_DIR/.env"
MEDIA_NODE_LOG="/var/log/smsly-media-install.log"

# ─── Detect media hardware ───────────────────────────────────────────────────
detect_media_hardware() {
    echo -e "${BLUE}  → Detecting media hardware...${NC}"

    local cores
    cores=$(nproc 2>/dev/null || echo 0)
    local ram_kb
    ram_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}' 2>/dev/null || echo 0)
    local ram_mb=$((ram_kb / 1024))
    local disk_gb
    disk_gb=$(df -BG / | awk 'NR==2 {print $2}' | tr -d 'G' 2>/dev/null || echo 0)

    echo -e "${BLUE}    CPU: ${cores} cores | RAM: ${ram_mb}MB | Disk: ${disk_gb}GB${NC}"

    if [ "$cores" -lt 4 ]; then
        echo -e "${RED}  ✗ Media node requires ≥4 CPU cores (found: ${cores})${NC}"
        return 1
    fi
    if [ "$ram_mb" -lt 7680 ]; then
        echo -e "${RED}  ✗ Media node requires ≥8GB RAM (found: ${ram_mb}MB)${NC}"
        return 1
    fi
    if [ "$disk_gb" -lt 50 ]; then
        echo -e "${RED}  ✗ Media node requires ≥50GB disk (found: ${disk_gb}GB)${NC}"
        return 1
    fi

    echo -e "${GREEN}  ✓ Hardware requirements met${NC}"
}

# ─── Detect available ports ──────────────────────────────────────────────────
check_media_ports() {
    echo -e "${BLUE}  → Checking media ports...${NC}"
    local ports=(80 443 5060 5060/udp 3478 3478/udp 9090 9091 30000-31000/udp)
    local blocked=()

    for port_spec in "${ports[@]}"; do
        local port="${port_spec%%/*}"
        local proto="${port_spec##*/}"
        if [ "$proto" = "$port_spec" ]; then
            proto="tcp"
        fi

        if [ "$proto" = "udp" ]; then
            if ss -ulnp 2>/dev/null | grep -q ":${port} " 2>/dev/null; then
                blocked+=("$port_spec")
            fi
        else
            if ss -tlnp 2>/dev/null | grep -q ":${port} " 2>/dev/null; then
                blocked+=("$port_spec")
            fi
        fi
    done

    if [ ${#blocked[@]} -gt 0 ]; then
        echo -e "${RED}  ✗ Ports blocked: ${blocked[*]}${NC}"
        return 1
    fi

    echo -e "${GREEN}  ✓ All media ports available${NC}"
}

# ─── Detect TPM ─────────────────────────────────────────────────────────────
detect_tpm() {
    if [ -c /dev/tpm0 ] && command -v tpm2_pcrread >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ TPM 2.0 detected${NC}"
        echo "tpm2"
    else
        echo -e "${YELLOW}  ⚠ No TPM 2.0 — using software fallback${NC}"
        echo "software"
    fi
}

# ─── Generate media node secrets ────────────────────────────────────────────
generate_media_secrets() {
    local env_file="$1"
    echo -e "${BLUE}  → Generating media node secrets...${NC}"

    local gateway_secret
    gateway_secret="$(openssl rand -hex 32 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(32))')"
    local livekit_api_key
    livekit_api_key="$(openssl rand -hex 16 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(16))')"
    local livekit_api_secret
    livekit_api_secret="$(openssl rand -hex 32 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(32))')"
    local turn_secret
    turn_secret="$(openssl rand -hex 32 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(32))')"
    local postgres_password
    postgres_password="$(openssl rand -hex 16 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(16))')"
    local redis_password
    redis_password="$(openssl rand -hex 16 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(16))')"

    cat > "$env_file" <<EOF
# SMSLY Media Node — Auto-generated secrets
# Generated: $(date -Iseconds)

NODE_TYPE=media
NODE_ID=${NODE_ID:-$(hostname -f 2>/dev/null || hostname)}

# Master connection
MASTER_IP=${MASTER_IP:-}
MASTER_MESH_IP=${MASTER_MESH_IP:-}
MASTER_API_URL=${MASTER_API_URL:-https://master.smsly.com/api/v1}
GATEWAY_SECRET=${gateway_secret}

# Database (local)
POSTGRES_PASSWORD=${postgres_password}
REDIS_PASSWORD=${redis_password}

# LiveKit
LIVEKIT_API_KEY=${livekit_api_key}
LIVEKIT_API_SECRET=${livekit_api_secret}

# TURN
TURN_SECRET=${turn_secret}

# Node identity
PUBLIC_IP=${PUBLIC_IP:-$(detect_public_ip 2>/dev/null || echo "")}
DOMAIN=${DOMAIN:-$(hostname -f 2>/dev/null || hostname)}

# Management daemon
CONFIG_PATH=/etc/smsly/media-mgmt.json
RUST_LOG=smsly_media_mgmt=info,tower_http=info
EOF

    chmod 600 "$env_file"
    echo -e "${GREEN}  ✓ Secrets generated${NC}"
}

# ─── Install media infrastructure packages ───────────────────────────────────
install_media_packages() {
    echo -e "${BLUE}  → Installing media infrastructure packages...${NC}"

    apt-get update -qq
    apt-get install -y -qq \
        postgresql-15 \
        redis-server \
        wireguard \
        kamailio \
        freeswitch \
        coturn \
        openresty \
        curl \
        jq \
        netcat-openbsd \
        fs_cli \
        >/dev/null 2>&1

    echo -e "${GREEN}  ✓ Packages installed${NC}"
}

# ─── Deploy media configs ────────────────────────────────────────────────────
deploy_media_configs() {
    local script_dir="$1"
    echo -e "${BLUE}  → Deploying media node configs...${NC}"

    local infra_dir="$script_dir/infrastructure/media"
    if [ ! -d "$infra_dir" ]; then
        echo -e "${YELLOW}  ⚠ infrastructure/media/ not found — skipping config deployment${NC}"
        return 0
    fi

    # Kamailio
    [ -d /etc/kamailio ] && cp -f "$infra_dir/kamailio/kamailio.cfg" /etc/kamailio/ 2>/dev/null || true

    # FreeSWITCH
    [ -d /etc/freeswitch ] && cp -f "$infra_dir/freeswitch/freeswitch.xml" /etc/freeswitch/ 2>/dev/null || true

    # RTPEngine
    [ -d /etc/rtpengine ] || mkdir -p /etc/rtpengine
    cp -f "$infra_dir/rtpengine/rtpengine.conf" /etc/rtpengine/ 2>/dev/null || true

    # LiveKit
    [ -d /etc/livekit ] || mkdir -p /etc/livekit
    cp -f "$infra_dir/livekit/livekit.yaml" /etc/livekit/ 2>/dev/null || true

    # coturn
    [ -d /etc/coturn ] || mkdir -p /etc/coturn
    cp -f "$infra_dir/coturn/turnserver.conf" /etc/coturn/ 2>/dev/null || true

    # OpenResty
    [ -d /usr/local/openresty/nginx/conf ] || mkdir -p /usr/local/openresty/nginx/conf
    cp -f "$infra_dir/openresty/nginx.conf" /usr/local/openresty/nginx/conf/ 2>/dev/null || true

    # Attestation
    [ -d /etc/smsly ] || mkdir -p /etc/smsly
    cp -f "$infra_dir/attestation/attestation.json" /etc/smsly/ 2>/dev/null || true

    echo -e "${GREEN}  ✓ Configs deployed${NC}"
}

# ─── Deploy systemd units ───────────────────────────────────────────────────
deploy_media_systemd_units() {
    local script_dir="$1"
    echo -e "${BLUE}  → Deploying systemd units...${NC}"

    local systemd_dir="$script_dir/scripts/systemd"
    if [ ! -d "$systemd_dir" ]; then
        echo -e "${YELLOW}  ⚠ scripts/systemd/ not found — skipping systemd deployment${NC}"
        return 0
    fi

    for unit in "$systemd_dir"/*.service; do
        [ -f "$unit" ] || continue
        local name
        name=$(basename "$unit")
        cp -f "$unit" /etc/systemd/system/
        echo -e "  → Installed ${name}"
    done

    systemctl daemon-reload
    echo -e "${GREEN}  ✓ Systemd units deployed${NC}"
}

# ─── Template env vars into config files ─────────────────────────────────────
template_media_configs() {
    local env_file="$1"
    echo -e "${BLUE}  → Templating env vars into media configs...${NC}"

    # Source env for values
    set -a
    source "$env_file"
    set +a

    # Kamailio
    if [ -f /etc/kamailio/kamailio.cfg ]; then
        sed -i \
            -e "s|\${PUBLIC_IP}|${PUBLIC_IP}|g" \
            -e "s|\${PRIVATE_IP}|${PRIVATE_IP:-127.0.0.1}|g" \
            -e "s|\${DOMAIN}|${DOMAIN}|g" \
            /etc/kamailio/kamailio.cfg
    fi

    # LiveKit
    if [ -f /etc/livekit/livekit.yaml ]; then
        sed -i \
            -e "s|\${LIVEKIT_API_KEY}|${LIVEKIT_API_KEY}|g" \
            -e "s|\${LIVEKIT_API_SECRET}|${LIVEKIT_API_SECRET}|g" \
            -e "s|\${PUBLIC_IP}|${PUBLIC_IP}|g" \
            -e "s|\${PRIVATE_IP}|${PRIVATE_IP:-127.0.0.1}|g" \
            -e "s|\${TURN_SECRET}|${TURN_SECRET}|g" \
            -e "s|\${MASTER_API_URL}|${MASTER_API_URL}|g" \
            /etc/livekit/livekit.yaml
    fi

    # coturn
    if [ -f /etc/coturn/turnserver.conf ]; then
        sed -i \
            -e "s|\${PUBLIC_IP}|${PUBLIC_IP}|g" \
            -e "s|\${PRIVATE_IP}|${PRIVATE_IP:-127.0.0.1}|g" \
            -e "s|\${TURN_SECRET}|${TURN_SECRET}|g" \
            -e "s|\${DOMAIN}|${DOMAIN}|g" \
            /etc/coturn/turnserver.conf
    fi

    # Attestation
    if [ -f /etc/smsly/attestation.json ]; then
        sed -i \
            -e "s|\${NODE_ID}|${NODE_ID}|g" \
            -e "s|\${POSTGRES_PASSWORD}|${POSTGRES_PASSWORD}|g" \
            -e "s|\${REDIS_PASSWORD}|${REDIS_PASSWORD}|g" \
            -e "s|\${MASTER_API_URL}|${MASTER_API_URL}|g" \
            -e "s|\${GATEWAY_SECRET}|${GATEWAY_SECRET}|g" \
            /etc/smsly/attestation.json
    fi

    echo -e "${GREEN}  ✓ Configs templated${NC}"
}

# ─── Start media services in order ───────────────────────────────────────────
start_media_services() {
    echo -e "${BLUE}  → Starting media services...${NC}"

    local infra_services=(postgresql redis-server wireguard)
    local media_services=(kamailio rtpengine freeswitch coturn)
    local app_services=(livekit-server smsly-voice-api smsly-video)
    local mgmt_services=(smsly-media-mgmt openresty)

    for svc in "${infra_services[@]}"; do
        systemctl enable --now "$svc" 2>/dev/null || true
    done
    sleep 2

    for svc in "${media_services[@]}"; do
        systemctl enable --now "$svc" 2>/dev/null || true
    done
    sleep 1

    for svc in "${app_services[@]}"; do
        systemctl enable --now "$svc" 2>/dev/null || true
    done

    for svc in "${mgmt_services[@]}"; do
        systemctl enable --now "$svc" 2>/dev/null || true
    done

    echo -e "${GREEN}  ✓ All media services started${NC}"
}

# ─── Verify media services ───────────────────────────────────────────────────
verify_media_services() {
    echo -e "${BLUE}  → Verifying media services...${NC}"
    local failures=0

    local services=(
        "postgresql:pg_isready -q"
        "redis:redis-cli ping"
        "kamailio:nc -zvu 127.0.0.1 5060"
        "smsly-media-mgmt:curl -sf http://127.0.0.1:9090/health"
    )

    for entry in "${services[@]}"; do
        local name="${entry%%:*}"
        local check="${entry##*:}"
        if eval "$check" >/dev/null 2>&1; then
            echo -e "  ${GREEN}✓${NC} ${name}"
        else
            echo -e "  ${RED}✗${NC} ${name}"
            ((failures++))
        fi
    done

    if [ "$failures" -gt 0 ]; then
        echo -e "${RED}  ✗ ${failures} services failed verification${NC}"
        return 1
    fi

    echo -e "${GREEN}  ✓ All services healthy${NC}"
}

# ─── Full media node fresh install ───────────────────────────────────────────
install_media_node() {
    local script_dir="$1"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  SMSLY Media Node — Fresh Install${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

    # Phase 0: Pre-flight
    check_internet
    detect_media_hardware
    check_media_ports

    # Phase 1: Core infrastructure
    install_media_packages

    # Phase 2: Create install dir + generate secrets
    mkdir -p "$MEDIA_NODE_INSTALL_DIR"
    generate_media_secrets "$MEDIA_NODE_ENV"

    # Phase 3: Deploy configs + systemd
    deploy_media_configs "$script_dir"
    deploy_media_systemd_units "$script_dir"

    # Phase 4: Template configs
    template_media_configs "$MEDIA_NODE_ENV"

    # Phase 5: Start services
    start_media_services

    # Phase 6: Verify
    sleep 3
    verify_media_services

    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  ✓ Media node installation complete${NC}"
    echo -e "${GREEN}  → Config: ${MEDIA_NODE_ENV}${NC}"
    echo -e "${GREEN}  → Logs:   ${MEDIA_NODE_LOG}${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════${NC}"
}
