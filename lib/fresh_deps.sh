# -----------------------------------------------------------------------------
# 2. Dependency Management & cleanup
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "dependencies_installed"; then
    echo -e "\n${YELLOW}[2/9] Installing dependencies...${NC}"

# Stop conflicting services if present. Host Caddy conflicts are handled by
# check_caddy_conflict because master Docker Caddy and node Traefik need port 80.
# LEGACY: nginx is only used for the bare-metal install path.
# Docker Compose uses Caddy. See docs/REVERSE_PROXY_DECISION.md.
for svc in nginx apache2; do
    if systemctl is-active --quiet "$svc" ; then
        echo -e "${YELLOW}  ⚠ Stopping conflicting service: $svc${NC}"
        systemctl stop "$svc" || true
        systemctl disable "$svc" || true
    fi
done

# ─── NUCLEAR CLEANUP: Remove ALL stale SMSLY containers, volumes, networks ──
# This prevents: port conflicts, stale DB password volumes, orphan containers
echo -e "${BLUE}  → Cleaning up previous SMSLY installation artifacts...${NC}"

# Stop and remove stale smsly-hosting platform containers (NOT user-deployed services)
SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly-hosting-" -q  || true)
if [ -n "$SMSLY_CONTAINERS" ]; then
    echo -e "${YELLOW}  → Stopping smsly-hosting platform container(s)...${NC}"
    docker stop $SMSLY_CONTAINERS  || true
    docker rm -f $SMSLY_CONTAINERS  || true
fi

# Remove stale Docker volumes (postgres data with old passwords, etc.)
SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly" -q  || true)
if [ -n "$SMSLY_VOLUMES" ]; then
    if [ "${SMSLY_ALLOW_DESTRUCTIVE_FRESH:-0}" = "1" ]; then
        echo -e "${YELLOW}  → Removing stale SMSLY volumes (SMSLY_ALLOW_DESTRUCTIVE_FRESH=1)...${NC}"
        for vol in $SMSLY_VOLUMES; do
            docker volume rm "$vol"  || true
        done
    else
        echo -e "${YELLOW}  ⚠ Existing SMSLY volumes detected; preserving data by default.${NC}"
        echo -e "${YELLOW}    Use --wipe for full reset, or set SMSLY_ALLOW_DESTRUCTIVE_FRESH=1 to delete volumes in fresh install.${NC}"
    fi
fi

# Remove stale Docker networks
SMSLY_NETWORKS=$(docker network ls --filter "name=smsly" -q  || true)
if [ -n "$SMSLY_NETWORKS" ]; then
    for net in $SMSLY_NETWORKS; do
        docker network rm "$net"  || true
    done
fi

echo -e "${GREEN}  ✓ Previous artifacts cleaned${NC}"

apt_run apt-get update -qq
apt_run apt-get install -y curl wget git python3 python3-pip python3-venv openssl ca-certificates gnupg lsb-release dnsutils apache2-utils fail2ban apparmor-utils

# Install Docker if missing
if ! command -v docker ; then
    echo -e "${BLUE}  → Installing Docker...${NC}"
    mkdir -m 0755 -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list
    apt_run apt-get update -qq
    apt_run apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker || echo -e "${YELLOW}    ⚠ docker.service enable failed${NC}"
    systemctl start docker || echo -e "${YELLOW}    ⚠ docker.service start failed${NC}"
    if ! timeout 30 docker info ; then
        echo -e "${RED}  ✗ Docker daemon failed to start. Check 'systemctl status docker' and kernel modules.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ Docker installed and running${NC}"
else
    echo -e "${GREEN}  ✓ Docker already installed ($(docker --version | head -c 40))${NC}"
fi

# Create smsly system user for container file ownership
id smsly  || useradd -r -s /usr/sbin/nologin -u 1000 smsly  || true

# Ensure docker compose is available
if ! docker compose version ; then
    echo -e "${BLUE}  → Installing Docker Compose plugin...${NC}"
    apt_run apt-get install -y docker-compose-plugin || true
fi
# Fallback to docker-compose v1 if plugin still not available
if ! docker compose version ; then
    if command -v docker-compose ; then
        echo -e "${YELLOW}  ⚠ docker compose plugin not available; falling back to docker-compose v1${NC}"
        docker_compose() { docker-compose "$@"; }
    else
        echo -e "${RED}  ✗ Neither 'docker compose' nor 'docker-compose' found. Install Docker Compose.${NC}"
        exit 1
    fi
fi

# Apply mirror config if applicable (Only if docker is now present)
if command -v docker ; then
    configure_docker_mirror
fi

# Ensure security tools (Trivy and Cosign) are installed for image scanning
ensure_security_tools || true

# ─── Security: bootstrap (fire-and-forget) ──────────────────────────────
# Runs AFTER Docker is installed so docker-compose-based hardening layers
# (falco, crowdsec, gVisor, docker daemon config) can actually start.
if [ -f "$INSTALL_DIR/lib/harden.sh" ]; then
    source "$INSTALL_DIR/lib/harden.sh"
    harden_security_bootstrap
else
    # Minimal fallback: basic Fail2ban SSH protection
    cat << 'EOF' > /etc/fail2ban/jail.local
[DEFAULT]
bantime = 10m
findtime = 10m
maxretry = 5

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
EOF
    systemctl enable fail2ban  || true
    systemctl restart fail2ban  &
    echo -e "${GREEN}  ✓ Fail2ban configured and started${NC}"
fi


# Ensure WireGuard mesh interface exists (master gets 10.100.0.1, nodes get
# a placeholder that will be updated by WireGuardService after provisioning).
ensure_wireguard_mesh() {
    local mesh_ip="${MASTER_MESH_IP:-10.100.0.1}"
    local wg_iface="wg0"

    # On node mode, install WireGuard and create a placeholder interface.
    # The real mesh IP (e.g. 10.100.0.x) is assigned later by
    # WireGuardService.ensure_server_in_default_mesh(), but having the
    # interface ready prevents delays during provisioning.
    if is_node_mode; then
        mesh_ip="${NODE_MESH_IP:-10.100.0.2}"
        echo -e "${BLUE}  → Configuring WireGuard mesh on node ($wg_iface: $mesh_ip)...${NC}"
        if ! command -v wg ; then
            apt_run apt-get install -y wireguard
        fi
        mkdir -p /etc/wireguard
        if [ ! -f /etc/wireguard/private.key ]; then
            wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
        fi
        local privkey
        privkey="$(cat /etc/wireguard/private.key)"
        if [ ! -f "/etc/wireguard/${wg_iface}.conf" ]; then
            cat > "/etc/wireguard/${wg_iface}.conf" <<WGCONF
[Interface]
PrivateKey = ${privkey}
Address = ${mesh_ip}/24
ListenPort = 51820
WGCONF
        fi
        systemctl enable --now "wg-quick@${wg_iface}"  || true
        if ip link show "$wg_iface" ; then
            echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface: $mesh_ip) is up on node${NC}"
        else
            echo -e "${YELLOW}  ⚠ WireGuard ($wg_iface) failed to start on node — mesh will be configured post-provision${NC}"
        fi
        return 0
    fi

    # Lite agents don't run WireGuard (they connect via master's mesh)
    if is_agent_lite_mode; then
        return 0
    fi
    if ip link show "$wg_iface" ; then
        echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface) already configured${NC}"
        return 0
    fi
    echo -e "${BLUE}  → Configuring WireGuard mesh interface ($wg_iface: $mesh_ip)...${NC}"
    if ! command -v wg ; then
        apt_run apt-get install -y wireguard
    fi
    mkdir -p /etc/wireguard
    if [ ! -f /etc/wireguard/private.key ]; then
        wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
    fi
    local privkey
    privkey="$(cat /etc/wireguard/private.key)"
    if [ ! -f "/etc/wireguard/${wg_iface}.conf" ]; then
        cat > "/etc/wireguard/${wg_iface}.conf" <<WGCONF
[Interface]
PrivateKey = ${privkey}
Address = ${mesh_ip}/24
ListenPort = 51820
WGCONF
    fi
    systemctl enable --now "wg-quick@${wg_iface}"  || true
    if ip link show "$wg_iface" ; then
        echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface: $mesh_ip) is up${NC}"
    else
        echo -e "${YELLOW}  ⚠ WireGuard ($wg_iface) failed to start — PgCat mesh binding may fail${NC}"
    fi
}
ensure_wireguard_mesh

echo -e "${GREEN}  ✓ Dependencies installed${NC}"
    set_checkpoint "dependencies_installed"
fi
