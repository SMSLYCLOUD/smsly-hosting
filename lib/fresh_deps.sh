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
# If the egress-mirror nginx was stopped (either above or before this run),
# clear its NAT REDIRECT rules so TCP/80 builds are not blackholed on :8888
# until fresh_deploy re-enables the shim.
_egress_port="${SMSLY_EGRESS_MIRROR_PORT:-8888}"
if command -v iptables >/dev/null 2>&1 && ! ss -ltn 2>/dev/null | grep -qE ":${_egress_port}([[:space:]]|$)"; then
    for _chain in PREROUTING OUTPUT; do
        while read -r _rule; do
            [ -n "${_rule:-}" ] || continue
            iptables -t nat ${_rule/-A /-D } 2>/dev/null || true
        done < <(iptables -t nat -S "$_chain" 2>/dev/null | grep -E "REDIRECT .*--to-ports? ${_egress_port}([[:space:]]|$)" || true)
    done
    unset _chain _rule
fi
unset _egress_port

# ─── NUCLEAR CLEANUP: Remove ALL stale SMSLY containers, volumes, networks ──
# This prevents: port conflicts, stale DB password volumes, orphan containers
echo -e "${BLUE}  → Cleaning up previous SMSLY installation artifacts...${NC}"

# Stop and remove stale smsly-hosting platform containers (NOT user-deployed services)
# ALSO clean the HA cluster containers (smsly-postgres-*, smsly-redis-*) — they use
# container_name and are not matched by the smsly-hosting- prefix. If they survive,
# their stale volumes stay attached and can't be removed (or worse, keep old DB
# passwords that a fresh install's newly generated secrets can never match).
SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly-hosting-" -q  || true)
SMSLY_CONTAINERS="$SMSLY_CONTAINERS $(docker ps -a --filter "name=smsly-postgres" -q  || true)"
SMSLY_CONTAINERS="$SMSLY_CONTAINERS $(docker ps -a --filter "name=smsly-redis" -q  || true)"
SMSLY_CONTAINERS=$(echo "$SMSLY_CONTAINERS" | tr ' ' '\n' | sort -u | grep -v '^$')
if [ -n "$SMSLY_CONTAINERS" ]; then
    echo -e "${YELLOW}  → Stopping stale smsly platform container(s)...${NC}"
    docker stop $SMSLY_CONTAINERS  || true
    docker rm -f $SMSLY_CONTAINERS  || true
fi

# Remove stale Docker volumes (postgres data with old passwords, etc.)
# A true fresh install (no real .env) generates NEW secrets, so preserving old
# volumes guarantees DB auth failures. Only preserve volumes when a real .env
# (with secrets) exists so the reused .env secrets match the preserved data.
SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly" -q  || true)
if [ -n "$SMSLY_VOLUMES" ]; then
    _env_has_secrets=false
    if [ -f "$INSTALL_DIR/.env" ] && grep -qE '^(POSTGRES_PASSWORD|SECRET_KEY)=' "$INSTALL_DIR/.env" 2>/dev/null; then
        _env_has_secrets=true
    fi
    if [ "${SMSLY_ALLOW_DESTRUCTIVE_FRESH:-0}" = "1" ] || [ "$_env_has_secrets" = "false" ]; then
        if [ "${SMSLY_ALLOW_DESTRUCTIVE_FRESH:-0}" = "1" ]; then
            echo -e "${YELLOW}  → Removing stale SMSLY volumes (SMSLY_ALLOW_DESTRUCTIVE_FRESH=1)...${NC}"
        else
            echo -e "${YELLOW}  → Removing stale SMSLY volumes (no existing .env secrets — new install)${NC}"
        fi
        for vol in $SMSLY_VOLUMES; do
            docker volume rm "$vol"  || true
        done
    else
        echo -e "${YELLOW}  ⚠ Existing SMSLY volumes detected; preserving data (existing .env will be reused).${NC}"
        echo -e "${YELLOW}    Use --wipe or SMSLY_ALLOW_DESTRUCTIVE_FRESH=1 to force removal.${NC}"
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
    systemctl reload-or-restart fail2ban  &
    echo -e "${GREEN}  ✓ Fail2ban configured and started${NC}"
fi
    echo -e "${GREEN}  ✓ Dependencies installed${NC}"
    set_checkpoint "dependencies_installed"
fi

# Always converge WireGuard mesh after deps (runs on resume too so retried node
# provisions pick up freshly rotated WG_PRIVATE_KEY, NODE_MESH_IP, or fallback port).
_ensure_mesh_masquerade() {
    # SNAT container→mesh traffic to the mesh address. The far side only
    # routes 10.100.0.0/24 back through the tunnel — a bridge source IP
    # (172.16.0.0/12) would strand replies on the public internet, so
    # containers could reach mesh IPs over ping/HTTP but never complete
    # TCP to app ports (2026-09-29: node registrar timed out to master).
    # Idempotent; persisted with the other iptables rules by the
    # hardening step (iptables-save → rules.v4).
    ip link show wg0 >/dev/null 2>&1 || return 0
    if command -v iptables >/dev/null 2>&1; then
        iptables -t nat -C POSTROUTING -s 172.16.0.0/12 -o wg0 -j MASQUERADE 2>/dev/null \
            || iptables -t nat -A POSTROUTING -s 172.16.0.0/12 -o wg0 -j MASQUERADE 2>/dev/null \
            || true
    fi
}

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
        if [ -n "${WG_PRIVATE_KEY:-}" ]; then
            # Provisioned mesh identity (bootstrap token): the master
            # already registered this keypair for our mesh address — a
            # fresh key here would fail the handshake closed (2026-09-28:
            # node came up on a stranger key, master only trusts the
            # provisioned one). Always converge on the provisioned key.
            printf '%s' "$WG_PRIVATE_KEY" > /etc/wireguard/private.key
            chmod 600 /etc/wireguard/private.key
            printf '%s' "$WG_PRIVATE_KEY" | wg pubkey > /etc/wireguard/public.key
        elif [ ! -f /etc/wireguard/private.key ]; then
            wg genkey | tee /etc/wireguard/private.key | wg pubkey > /etc/wireguard/public.key
        fi
        local privkey=""
        privkey="$(cat /etc/wireguard/private.key)"
        local wg_conf="/etc/wireguard/${wg_iface}.conf"
        local wg_port="${WG_PRIMARY_PORT:-51820}"
        if [ ! -f "$wg_conf" ] || ! grep -q '^\[Interface\]' "$wg_conf" 2>/dev/null; then
            # Recreate [Interface] whenever wg0.conf is absent or was corrupted
            # into a headless [Peer]-only file (without [Interface], sed
            # inserts below silently do nothing). Preserve existing [Peer] blocks.
            local _preserved_peers=""
            if [ -f "$wg_conf" ] && grep -q '^\[Peer\]' "$wg_conf" 2>/dev/null; then
                _preserved_peers="$(sed -n '/^\[Peer\]/,$p' "$wg_conf")"
            fi
            cat > "$wg_conf" <<WGCONF
[Interface]
PrivateKey = ${privkey}
Address = ${mesh_ip}/24
ListenPort = ${wg_port}
WGCONF
            if [ -n "$_preserved_peers" ]; then
                printf '\n%s\n' "$_preserved_peers" >> "$wg_conf"
            fi
        else
            # Converge Address/PrivateKey/ListenPort under [Interface]:
            # re-runs on a wiped or previously failed node often keep a
            # stale .conf (e.g. .2 placeholder instead of NODE_MESH_IP),
            # which breaks coredns bind ("cannot assign requested address").
            if grep -q '^PrivateKey[[:space:]]*=' "$wg_conf" 2>/dev/null; then
                if [ -n "${WG_PRIVATE_KEY:-}" ]; then
                    sed -i "s|^PrivateKey[[:space:]]*=.*|PrivateKey = ${privkey}|" "$wg_conf"
                fi
            else
                sed -i "/^\[Interface\]/a PrivateKey = ${privkey}" "$wg_conf"
            fi
            if grep -q '^Address[[:space:]]*=' "$wg_conf" 2>/dev/null; then
                sed -i "s|^Address[[:space:]]*=.*|Address = ${mesh_ip}/24|" "$wg_conf"
            else
                sed -i "/^\[Interface\]/a Address = ${mesh_ip}/24" "$wg_conf"
            fi
            if ! grep -q '^ListenPort[[:space:]]*=' "$wg_conf" 2>/dev/null; then
                sed -i "/^\[Interface\]/a ListenPort = ${wg_port}" "$wg_conf"
            fi
        fi
        chmod 600 "$wg_conf"

        # If the bootstrap/SSH env shipped the master's WG identity, seed
        # the [Peer] block right away so wg_ensure_listening can test the
        # primary/fallback UDP path immediately instead of waiting for
        # post-provision mesh deploy.
        local master_pub="${MASTER_WG_PUBKEY:-}"
        local master_ep="${MASTER_WG_ENDPOINT:-}"
        if [ -z "$master_ep" ] && [ -n "${MASTER_IP:-}" ]; then
            master_ep="${MASTER_IP}:${wg_port}"
        fi
        if [ -n "$master_pub" ] && [ -n "$master_ep" ] && ! grep -qF "$master_pub" "$wg_conf" 2>/dev/null; then
            cat >> "$wg_conf" <<WGPEER

[Peer]
PublicKey = ${master_pub}
Endpoint = ${master_ep}
AllowedIPs = ${MASTER_MESH_IP:-10.100.0.1}/32
PersistentKeepalive = 25
WGPEER
        fi

        # systemctl enable --now is a silent no-op if wg-quick@wg0 stayed
        # `active (exited)` after an out-of-band wg-quick down/rollback.
        # Restart whenever wg0 is absent or lacks the target mesh IP.
        if ! ip -4 addr show dev "$wg_iface" 2>/dev/null | grep -q "inet ${mesh_ip}/"; then
            systemctl enable "wg-quick@${wg_iface}" >/dev/null 2>&1 || true
            systemctl restart "wg-quick@${wg_iface}" >/dev/null 2>&1 \
                || { wg-quick down "$wg_iface" >/dev/null 2>&1 || true; wg-quick up "$wg_iface" >/dev/null 2>&1 || true; }
        fi
        if ip link show "$wg_iface" >/dev/null 2>&1; then
            echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface: $mesh_ip) starting (port ${wg_port})${NC}"
            if declare -F wg_ensure_listening >/dev/null 2>&1; then wg_ensure_listening "$wg_iface" "$mesh_ip" || true; fi
        else
            echo -e "${YELLOW}  ⚠ WireGuard ($wg_iface) failed to start on node — mesh will be configured post-provision${NC}"
        fi
        # Point the node host at the master's CoreDNS for *.mesh.internal
        # (per-link wg0 resolver; global DNS is untouched). Best-effort:
        # install continues even if the resolver cannot be configured.
        if declare -F configure_mesh_dns_resolver >/dev/null 2>&1; then
            configure_mesh_dns_resolver || true
        fi
        _ensure_mesh_masquerade
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
        echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface: $mesh_ip) starting (port 51820)${NC}"
        if declare -F wg_ensure_listening >/dev/null 2>&1; then wg_ensure_listening "$wg_iface" "$mesh_ip" || true; fi
    else
        echo -e "${YELLOW}  ⚠ WireGuard ($wg_iface) failed to start — PgCat mesh binding may fail${NC}"
    fi
    _ensure_mesh_masquerade
}
ensure_wireguard_mesh
