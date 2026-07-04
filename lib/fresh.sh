#!/bin/bash
# Grid by SMSLY - Fresh Install Mode Module
# Sourced by install.sh for fresh installs

# =============================================================================
# FRESH INSTALL — Full setup from scratch
# =============================================================================

# ─── Interactive Setup (Step 0) ──────────────────────────────────────────────
if [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
    # Agent Lite Selection
    if [ "$MODE_AGENT_LITE" = "true" ] && [ -z "${MASTER_IP:-}" ]; then
        echo -e "\n${BLUE}═══════════════════════════════════════════════════════════"
        echo "  CONFIGURING LITE AGENT NODE"
        echo "═══════════════════════════════════════════════════════════${NC}"
        read -p "  Enter Master VPS IP Address: " MASTER_IP < /dev/tty
        read -p "  Enter Master Database Password: " MASTER_DB_PASSWORD < /dev/tty
        echo ""
        read -p "  Enter Master RabbitMQ Password: " MASTER_MQ_PASSWORD < /dev/tty
        echo ""
        COMPOSE_FILE="infrastructure/docker/docker-compose.agent-lite.yml"
        export MASTER_IP MASTER_DB_PASSWORD MASTER_MQ_PASSWORD
    fi

    # ─── Deployment Mode Selection (Moved up) ──────────────────────────────
    # Initialize defaults
    MODE_CHOICE=1
    PUBLIC_IP="${PUBLIC_IP:-$(detect_public_ip)}"
    PRESET_DOMAIN="${DOMAIN:-}"
    PRESET_ACME_EMAIL="${ACME_EMAIL:-}"
    PRESET_USE_SSL="${USE_SSL:-}"

    # Deployment Mode Selection - Only prompt if not preset and in interactive shell
    if is_node_mode; then
        USE_SSL="false"
        DOMAIN="${DOMAIN:-$PUBLIC_IP}"
        MODE_CHOICE=1
        echo -e "${BLUE}  → Node mode: using Traefik HTTP on $DOMAIN; Caddy/HTTPS is master-owned.${NC}"
    elif [ -n "${PRESET_USE_SSL}" ]; then
        if [ "${PRESET_USE_SSL}" = "true" ] && [ -n "${PRESET_DOMAIN}" ] && [ -n "${PRESET_ACME_EMAIL}" ]; then
            echo -e "${BLUE}  → Preset detected. Using SSL Mode for ${PRESET_DOMAIN}.${NC}"
            MODE_CHOICE=2
        elif [ "${PRESET_USE_SSL}" = "false" ]; then
            echo -e "${BLUE}  → Preset detected. Using IP Mode.${NC}"
            MODE_CHOICE=1
        fi
    elif [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
        echo -e "\n${BLUE}Select Deployment Mode:${NC}"
        echo -e "  1) ${GREEN}IP Mode${NC} (Easy) - http://$PUBLIC_IP"
        echo -e "  2) ${GREEN}SSL Mode${NC} (Prod) - https://your-domain.com (Requires DNS A Record pointing to $PUBLIC_IP)"
        read -p "Enter choice [1]: " MODE_CHOICE < /dev/tty
        MODE_CHOICE=${MODE_CHOICE:-1}
    fi

    # Set configuration based on choice or presets
    if is_node_mode; then
        USE_SSL="false"
        DOMAIN="${DOMAIN:-$PUBLIC_IP}"
    elif [ "$MODE_CHOICE" -eq "2" ] || [ "${PRESET_USE_SSL}" = "true" ]; then
        USE_SSL="true"
        DOMAIN="${PRESET_DOMAIN:-}"
        ACME_EMAIL="${PRESET_ACME_EMAIL:-}"

        if [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
            while [ -z "$DOMAIN" ]; do
                read -p "Enter your Domain (e.g., app.example.com): " DOMAIN < /dev/tty
            done
            while [ -z "$ACME_EMAIL" ]; do
                read -p "Enter Email for SSL (e.g., admin@example.com): " ACME_EMAIL < /dev/tty
            done
        fi

        if [ -n "$DOMAIN" ]; then
            echo -e "${BLUE}  → Verifying DNS for $DOMAIN...${NC}"
            DETECTED_IP=""
            # Try 'host' first (dnsutils), fall back to API-based DNS lookup
            if command -v host &> /dev/null; then
                DETECTED_IP=$(host -t A "$DOMAIN" 2>/dev/null | awk '{print $NF}' | tail -n 1)
            fi
            if [ -z "$DETECTED_IP" ] || [ "$DETECTED_IP" = "found:" ] || [ "$DETECTED_IP" = "not" ]; then
                DETECTED_IP=""
                # Fallback to DNS over HTTPS (Google)
                DETECTED_IP="$(curl -fsS "https://dns.google/resolve?name=${DOMAIN}&type=A" -m 5 2>/dev/null | python3 -c "import json,sys; data=json.load(sys.stdin); ans=data.get('Answer',[]); print(ans[0]['data']) if ans and 'data' in ans[0] else print('')" 2>/dev/null || echo "")"
            fi
            if [ -n "$DETECTED_IP" ]; then
                if [ "$DETECTED_IP" != "$PUBLIC_IP" ] && [ "$DETECTED_IP" != "127.0.0.1" ]; then
                    echo -e "${YELLOW}  ⚠ WARNING: DNS for $DOMAIN ($DETECTED_IP) does not match this server ($PUBLIC_IP).${NC}"
                    echo -e "${YELLOW}  SSL generation may fail. Ensure your DNS A record is set.${NC}"
                    if [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
                        read -p "  Continue anyway? (y/n) " -n 1 -r < /dev/tty
                        echo
                        if [[ ! $REPLY =~ ^[Yy]$ ]]; then exit 1; fi
                    fi
                else
                    echo -e "${GREEN}  ✓ DNS looks correct.${NC}"
                fi
            else
                echo -e "${YELLOW}  ⚠ Could not resolve DNS for $DOMAIN. SSL may fail.${NC}"
                echo -e "${YELLOW}  Ensure your DNS A record points to $PUBLIC_IP${NC}"
            fi
        fi
    else
        USE_SSL="false"
        DOMAIN="${DOMAIN:-$PUBLIC_IP}"
        echo -e "${BLUE}  → Using IP Mode: $DOMAIN${NC}"
    fi

    # ─── Wildcard Subdomain & Cloudflare Setup (Front-loaded) ────────────
    WILDCARD_SUBDOMAINS="false"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
    if [ "$USE_SSL" = "true" ] && [ -n "$DOMAIN" ] && [ "$DOMAIN" != "$PUBLIC_IP" ]; then
        echo ""
        echo -e "${BLUE}  Wildcard subdomains allow deployed services to get automatic SSL.${NC}"
        echo -e "  e.g., myapp-abc123.${DOMAIN} will automatically have HTTPS."
        echo -e "  This requires a Cloudflare API Token with DNS:Edit permission.\n"

        if [ -n "${CLOUDFLARE_API_TOKEN}" ]; then
            WILDCARD_SUBDOMAINS="true"
            echo -e "${BLUE}  → Preset Cloudflare token detected. Enabling wildcard subdomains.${NC}"
        elif [ "$NON_INTERACTIVE" != "true" ] && [ -e /dev/tty ]; then
            read -p "  Enable wildcard subdomains? (y/n) [n]: " WILDCARD_CHOICE < /dev/tty
            WILDCARD_CHOICE=${WILDCARD_CHOICE:-n}
            if [[ $WILDCARD_CHOICE =~ ^[Yy]$ ]]; then
                WILDCARD_SUBDOMAINS="true"
                while [ -z "$CLOUDFLARE_API_TOKEN" ]; do
                    read -p "  Enter Cloudflare API Token (DNS:Edit): " CLOUDFLARE_API_TOKEN < /dev/tty
                    echo
                done
                echo -e "${GREEN}  ✓ Wildcard subdomains enabled.${NC}"
            fi
        fi
    fi
fi

if is_node_mode; then
    PUBLIC_IP="${PUBLIC_IP:-$(detect_public_ip)}"
    USE_SSL="false"
    DOMAIN="${DOMAIN:-$PUBLIC_IP}"
    WILDCARD_SUBDOMAINS="false"
    CLOUDFLARE_API_TOKEN=""
fi

# -----------------------------------------------------------------------------
# 1. Pre-flight Checks
# -----------------------------------------------------------------------------
echo -e "${YELLOW}[1/9] Checking system requirements...${NC}"

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}✗ Please run as root (sudo bash install.sh)${NC}"
    exit 1
fi

check_internet
check_hardware
check_caddy_conflict
ensure_system_swap

# Check OS
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo -e "${BLUE}  Detected: $NAME $VERSION_ID${NC}"
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
        echo -e "${YELLOW}⚠ Warning: This script is optimized for Ubuntu/Debian.${NC}"
        if [ -e /dev/tty ] && [ "$NON_INTERACTIVE" != "true" ]; then
             echo -e "${YELLOW}  Press ENTER to continue anyway, or Ctrl+C to abort.${NC}"
             read -r < /dev/tty
        else
             echo -e "${YELLOW}  ⚠ Automated mode: Continuing automatically...${NC}"
        fi
    fi
fi

# ─── Disk space check (prevents mid-build OOM / no-space failures) ──────────
DISK_AVAIL_MB=$(df -BM / | tail -1 | awk '{print $4}' | tr -d 'M')
echo -e "${BLUE}  Disk space available: ${DISK_AVAIL_MB}MB${NC}"
if [ "$DISK_AVAIL_MB" -lt 3000 ]; then
    echo -e "${YELLOW}  ⚠ Low disk space (${DISK_AVAIL_MB}MB). Recommended: 3GB+${NC}"
    echo -e "${YELLOW}    Attempting Docker cache cleanup...${NC}"
    docker system prune -f 2>/dev/null || true
    docker builder prune -f 2>/dev/null || true
    DISK_AVAIL_MB=$(df -BM / | tail -1 | awk '{print $4}' | tr -d 'M')
    if [ "$DISK_AVAIL_MB" -lt 1500 ]; then
        echo -e "${RED}  ✗ Insufficient disk space (${DISK_AVAIL_MB}MB). Need at least 1.5GB for fresh install.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ After cleanup: ${DISK_AVAIL_MB}MB available${NC}"
fi

# ─── Git Initialization & Sync ──────────────────────────────────────────────
SMSLY_BRANCH="${SMSLY_BRANCH:-main}"
SMSLY_GIT_REMOTE="${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"

if [ -d "$INSTALL_DIR/.git" ]; then
    echo -e "${BLUE}  → Updating existing repository ($SMSLY_BRANCH)...${NC}"
    cd "$INSTALL_DIR"
    ensure_local_ignores
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
        echo -e "${YELLOW}  ! Local changes detected - stashing before repository sync${NC}"
        git stash push --include-untracked -m "install-sync-$(date +%s)" >/dev/null 2>&1 || true
    fi
    if ! git fetch origin "$SMSLY_BRANCH" >/dev/null 2>&1 || ! git reset --hard "origin/$SMSLY_BRANCH" >/dev/null 2>&1; then
        echo -e "${RED}  ✗ Git update failed for $SMSLY_BRANCH. SSL verification is always enforced — check network or CA certificates.${NC}"
    fi
else
    echo -e "${BLUE}  → Cloning repository ($SMSLY_BRANCH)...${NC}"
    CLONE_SUCCESS=false
    if [ -f "$INSTALL_DIR/.env" ]; then
        echo -e "${YELLOW}  → Existing .env found — preserving configuration${NC}"
        cp "$INSTALL_DIR/.env" /tmp/smsly-env-backup 2>/dev/null || true
    fi
    rm -rf "$INSTALL_DIR"
    if git clone -b "$SMSLY_BRANCH" "$SMSLY_GIT_REMOTE" "$INSTALL_DIR"; then
        CLONE_SUCCESS=true
    else
        echo -e "${RED}  ✗ Git clone failed. SSL verification is always enforced — check network or CA certificates.${NC}"
    fi
    if [ "$CLONE_SUCCESS" = "true" ] && [ -f /tmp/smsly-env-backup ]; then
        cp /tmp/smsly-env-backup "$INSTALL_DIR/.env"
        rm -f /tmp/smsly-env-backup
        echo -e "${GREEN}  ✓ Restored existing .env${NC}"
    fi

    if [ "$CLONE_SUCCESS" = "false" ]; then
        echo -e "${YELLOW}  ⚠️ Git clone/fetch failed.${NC}"
        if [ -n "${SMSLY_INSTALL_WORKDIR:-}" ] && [ -d "${SMSLY_INSTALL_WORKDIR}" ]; then
            echo -e "${BLUE}  → Fallback: Initializing from pre-uploaded source bundle...${NC}"
            mkdir -p "$INSTALL_DIR"
            cp -rv "${SMSLY_INSTALL_WORKDIR}/"* "$INSTALL_DIR/" 2>/dev/null || true
            cd "$INSTALL_DIR"
            if [ ! -d ".git" ]; then
                git init -q
                git remote add origin "$SMSLY_GIT_REMOTE"
            fi
            echo -e "${GREEN}  ✓ Fallback initialization complete.${NC}"
        fi
    fi
fi

echo -e "${GREEN}  ✓ Pre-flight checks passed${NC}"
set_checkpoint "requirements_checked"

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
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        echo -e "${YELLOW}  ⚠ Stopping conflicting service: $svc${NC}"
        systemctl stop "$svc" || true
        systemctl disable "$svc" || true
    fi
done

# ─── NUCLEAR CLEANUP: Remove ALL stale SMSLY containers, volumes, networks ──
# This prevents: port conflicts, stale DB password volumes, orphan containers
echo -e "${BLUE}  → Cleaning up previous SMSLY installation artifacts...${NC}"

# Stop and remove stale smsly-hosting platform containers (NOT user-deployed services)
SMSLY_CONTAINERS=$(docker ps -a --filter "name=smsly-hosting-" -q 2>/dev/null || true)
if [ -n "$SMSLY_CONTAINERS" ]; then
    echo -e "${YELLOW}  → Stopping smsly-hosting platform container(s)...${NC}"
    docker stop $SMSLY_CONTAINERS 2>/dev/null || true
    docker rm -f $SMSLY_CONTAINERS 2>/dev/null || true
fi

# Remove stale Docker volumes (postgres data with old passwords, etc.)
SMSLY_VOLUMES=$(docker volume ls --filter "name=smsly" -q 2>/dev/null || true)
if [ -n "$SMSLY_VOLUMES" ]; then
    if [ "${SMSLY_ALLOW_DESTRUCTIVE_FRESH:-0}" = "1" ]; then
        echo -e "${YELLOW}  → Removing stale SMSLY volumes (SMSLY_ALLOW_DESTRUCTIVE_FRESH=1)...${NC}"
        for vol in $SMSLY_VOLUMES; do
            docker volume rm "$vol" 2>/dev/null || true
        done
    else
        echo -e "${YELLOW}  ⚠ Existing SMSLY volumes detected; preserving data by default.${NC}"
        echo -e "${YELLOW}    Use --wipe for full reset, or set SMSLY_ALLOW_DESTRUCTIVE_FRESH=1 to delete volumes in fresh install.${NC}"
    fi
fi

# Remove stale Docker networks
SMSLY_NETWORKS=$(docker network ls --filter "name=smsly" -q 2>/dev/null || true)
if [ -n "$SMSLY_NETWORKS" ]; then
    for net in $SMSLY_NETWORKS; do
        docker network rm "$net" 2>/dev/null || true
    done
fi

echo -e "${GREEN}  ✓ Previous artifacts cleaned${NC}"

apt_run apt-get update -qq
apt_run apt-get install -y curl wget git python3 python3-pip python3-venv openssl ca-certificates gnupg lsb-release dnsutils apache2-utils fail2ban apparmor-utils

# ─── Security: bootstrap (fire-and-forget) ──────────────────────────────
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
    systemctl enable fail2ban >/dev/null 2>&1 || true
    systemctl restart fail2ban >/dev/null 2>&1 &
    echo -e "${GREEN}  ✓ Fail2ban configured and started${NC}"
fi

# Install Docker if missing
if ! command -v docker &> /dev/null; then
    echo -e "${BLUE}  → Installing Docker...${NC}"
    mkdir -m 0755 -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt_run apt-get update -qq
    apt_run apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable docker 2>/dev/null || true
    systemctl start docker 2>/dev/null || true
    if ! docker info >/dev/null 2>&1; then
        echo -e "${RED}  ✗ Docker daemon failed to start. Check 'systemctl status docker' and kernel modules.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ Docker installed and running${NC}"
else
    echo -e "${GREEN}  ✓ Docker already installed ($(docker --version | head -c 40))${NC}"
fi

# Create smsly system user for container file ownership
id smsly >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin -u 1000 smsly 2>/dev/null || true

# Ensure docker compose is available
if ! docker compose version >/dev/null 2>&1; then
    echo -e "${BLUE}  → Installing Docker Compose plugin...${NC}"
    apt_run apt-get install -y docker-compose-plugin || true
fi
# Fallback to docker-compose v1 if plugin still not available
if ! docker compose version >/dev/null 2>&1; then
    if command -v docker-compose >/dev/null 2>&1; then
        echo -e "${YELLOW}  ⚠ docker compose plugin not available; falling back to docker-compose v1${NC}"
        docker_compose() { docker-compose "$@"; }
    else
        echo -e "${RED}  ✗ Neither 'docker compose' nor 'docker-compose' found. Install Docker Compose.${NC}"
        exit 1
    fi
fi

# Apply mirror config if applicable (Only if docker is now present)
if command -v docker &> /dev/null; then
    configure_docker_mirror
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
        if ! command -v wg >/dev/null 2>&1; then
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
        systemctl enable --now "wg-quick@${wg_iface}" 2>/dev/null || true
        if ip link show "$wg_iface" >/dev/null 2>&1; then
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
    if ip link show "$wg_iface" >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface) already configured${NC}"
        return 0
    fi
    echo -e "${BLUE}  → Configuring WireGuard mesh interface ($wg_iface: $mesh_ip)...${NC}"
    if ! command -v wg >/dev/null 2>&1; then
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
    systemctl enable --now "wg-quick@${wg_iface}" 2>/dev/null || true
    if ip link show "$wg_iface" >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ WireGuard mesh ($wg_iface: $mesh_ip) is up${NC}"
    else
        echo -e "${YELLOW}  ⚠ WireGuard ($wg_iface) failed to start — PgCat mesh binding may fail${NC}"
    fi
}
ensure_wireguard_mesh

echo -e "${GREEN}  ✓ Dependencies installed${NC}"
    set_checkpoint "dependencies_installed"
fi

# -----------------------------------------------------------------------------
# 3. Configuration & Secrets (IDEMPOTENT)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "config_generated"; then
    echo -e "\n${YELLOW}[3/9] Configuration...${NC}"

mkdir -p "$INSTALL_DIR"

# Ensure we are in the install directory with correct files
if [ "$(pwd)" != "$INSTALL_DIR" ]; then
    echo -e "${BLUE}  → Setting up installation in $INSTALL_DIR${NC}"
    if [ -f "docker-compose.prod.yml" ]; then
        if [ "${SMSLY_FORCE_SOURCE_SYNC:-0}" = "1" ]; then
            cp -rf . "$INSTALL_DIR/"
        else
            cp -rn . "$INSTALL_DIR/" 2>/dev/null || cp -r . "$INSTALL_DIR/"
        fi
    else
        if [ -d "$INSTALL_DIR/.git" ]; then
             echo -e "${BLUE}  → Updating existing repository...${NC}"
             cd "$INSTALL_DIR"
             if ! git pull origin "$SMSLY_BRANCH" >/dev/null 2>&1; then
                 echo -e "${RED}  ✗ Git pull failed for $SMSLY_BRANCH. SSL verification is always enforced.${NC}"
             fi
        else
             echo -e "${BLUE}  → Cloning repository...${NC}"
             if [ -f "$INSTALL_DIR/.env" ]; then
                 cp "$INSTALL_DIR/.env" /tmp/smsly-env-backup 2>/dev/null || true
             fi
             rm -rf "$INSTALL_DIR"
             if ! git clone -b "$SMSLY_BRANCH" "${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}" "$INSTALL_DIR"; then
                 echo -e "${RED}  ✗ Git clone failed for $SMSLY_BRANCH. SSL verification is always enforced.${NC}"
             fi
             cd "$INSTALL_DIR"
             if [ -f /tmp/smsly-env-backup ]; then
                 cp /tmp/smsly-env-backup "$INSTALL_DIR/.env"
                 rm -f /tmp/smsly-env-backup
                 echo -e "${GREEN}  ✓ Restored existing .env${NC}"
             fi
        fi
    fi
fi
cd "$INSTALL_DIR"

# ─── Git Initialization (for bundled installs) ──────────────────────────────
if [ ! -d ".git" ] && [ -n "${SMSLY_GIT_REMOTE:-}" ]; then
    echo -e "${BLUE}  -> Initializing Git repository...${NC}"
    git init -q
    git checkout -b "$SMSLY_BRANCH" >/dev/null 2>&1 || true
    git remote add origin "$SMSLY_GIT_REMOTE"
    if ! git fetch origin "$SMSLY_BRANCH" -q --depth=1; then
        echo -e "${YELLOW}  ⚠ Git fetch failed — repository will be unlinked from remote (SSL verification enforced)${NC}"
    fi
    git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
    # We don't reset --hard here to avoid losing the bundled files we just copied,
    # but the repo is now linked for future updates.
    echo -e "${GREEN}  ✓ Git origin set to ${SMSLY_GIT_REMOTE}${NC}"
fi

# ─── BLINDSPOT FIX: Validate required deployment files ──────────────────────
echo -e "${BLUE}  → Validating deployment files...${NC}"
MISSING_FILES=()
if [ "$MODE_AGENT_LITE" = "true" ]; then
    REQUIRED_FILES=("$COMPOSE_FILE" "backend/Dockerfile" "backend/entrypoint.sh" "backend/requirements.txt")
elif [ "$MODE_NODE" = "true" ]; then
    REQUIRED_FILES=("$COMPOSE_FILE" "backend/Dockerfile" "backend/entrypoint.sh" "backend/requirements.txt")
else
    REQUIRED_FILES=("$COMPOSE_FILE" "backend/Dockerfile" "frontend/Dockerfile" "backend/entrypoint.sh")
fi
for required_file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$required_file" ]; then
        MISSING_FILES+=("$required_file")
    fi
done

if [ ${#MISSING_FILES[@]} -gt 0 ]; then
    echo -e "${RED}✗ Missing required files:${NC}"
    for f in "${MISSING_FILES[@]}"; do
        echo -e "${RED}    - $f${NC}"
    done
    exit 1
fi
echo -e "${GREEN}  ✓ All required deployment files present${NC}"

# ─── BLINDSPOT FIX: Ensure correct compose file is used ─────────────────────
# Check if any containers are running with the wrong compose file (dev instead of prod)
wrong_project=false
for c_id in $(docker ps --filter "name=smsly-hosting" -q 2>/dev/null || true); do
    config_file=$(docker inspect "$c_id" --format='{{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>/dev/null || true)
    compose_base=$(basename "$COMPOSE_FILE")
    if [ -n "$config_file" ] && [[ "$config_file" != *"$compose_base"* ]]; then
        wrong_project=true
        break
    fi
done

if [ "$wrong_project" = "true" ]; then
    echo -e "${YELLOW}  ⚠ Found containers running from a different compose project configuration. Stopping...${NC}"
    for c_id in $(docker ps --filter "name=smsly-hosting" -q 2>/dev/null || true); do
        config_file=$(docker inspect "$c_id" --format='{{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>/dev/null || true)
        compose_base=$(basename "$COMPOSE_FILE")
        if [ -n "$config_file" ] && [[ "$config_file" != *"$compose_base"* ]]; then
            docker stop "$c_id" >/dev/null 2>&1 || true
            docker rm "$c_id" >/dev/null 2>&1 || true
        fi
    done
fi

# ─── IDEMPOTENCY: Skip secret generation if .env already exists ─────────────
if [ -f "$INSTALL_DIR/.env" ]; then
    echo -e "${GREEN}  ✓ Existing .env found — preserving configuration${NC}"
    echo -e "${BLUE}  → Backing up existing .env to .env.backup${NC}"
    cp "$INSTALL_DIR/.env" "$INSTALL_DIR/.env.backup"

    # Backfill newer required keys and validate before deployment.
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    apply_agent_lite_env_overrides "$INSTALL_DIR/.env"
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x Existing .env is invalid. Fix it or restore .env.backup and rerun.${NC}"
        exit 1
    fi

    # Source existing values for summary output.
    set -a
    source "$INSTALL_DIR/.env" 2>/dev/null || true
    set +a
    DOMAIN="${DOMAIN:-localhost}"
    USE_SSL="${USE_SSL:-false}"
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-false}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
    PUBLIC_IP="$(detect_public_ip)"


else
    # ─── Configuration Summary ──────────────────────────────────────────────
    PUBLIC_IP="${PUBLIC_IP:-$(detect_public_ip)}"
    DOMAIN="${DOMAIN:-$PUBLIC_IP}"
    # SEC-002: IP-mode SSL guard — force USE_SSL=false if DOMAIN is a raw IP
    if echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        USE_SSL="${USE_SSL:-false}"
        if [ "${USE_SSL:-false}" = "true" ]; then
            echo -e "${YELLOW}  ⚠ WARNING: USE_SSL=true ignored — DOMAIN is a raw IP. Forcing USE_SSL=false.${NC}"
        fi
        USE_SSL="false"
    else
        USE_SSL="${USE_SSL:-false}"
    fi
    WILDCARD_SUBDOMAINS="${WILDCARD_SUBDOMAINS:-false}"
    CLOUDFLARE_API_TOKEN="${CLOUDFLARE_API_TOKEN:-}"
    ACME_EMAIL="${ACME_EMAIL:-}"

    # ─── Generate Secrets (scripts/generate_env_secrets.py — single source of truth) ──
    echo -e "${BLUE}  → Generating secure credentials...${NC}"

    # Ensure cryptography is installed (required for Fernet key generation).
    # Retry with and without --break-system-packages for different Ubuntu versions.
    pip3 install cryptography -q --break-system-packages 2>/dev/null || \
        pip3 install cryptography -q 2>/dev/null || \
        (echo -e "${YELLOW}  → Retrying cryptography install...${NC}" && \
         pip3 install cryptography 2>&1 | tail -3) || true

    # Verify cryptography is importable before proceeding
    if ! python3 -c "from cryptography.fernet import Fernet; print('ok')" 2>/dev/null; then
        echo -e "${RED}  ✗ CRITICAL: cryptography package is not installable.${NC}"
        echo -e "${RED}    The 'cryptography' package is required to generate a Fernet encryption key.${NC}"
        echo -e "${RED}    Install it manually: pip3 install cryptography${NC}"
        exit 1
    fi

    # Use the dedicated secrets generation script (single source of truth).
    # SECURITY: stream secrets directly into shell variables via process
    # substitution so the plaintext never touches the filesystem. The previous
    # implementation wrote to $INSTALL_DIR/.secrets.tmp which could leak on
    # early failure (rm -f only ran on the success path).
    SECRETS_GENERATED=false
    while IFS='=' read -r _smsly_secrets_key _smsly_secrets_val; do
        case "$_smsly_secrets_key" in
            SECRET_KEY|FIELD_ENCRYPTION_KEY|POSTGRES_PASSWORD|REDIS_PASSWORD|RABBITMQ_PASSWORD|GATEWAY_SECRET|GITHUB_WEBHOOK_SECRET|AUTOSCALER_API_TOKEN|FRP_AUTH_TOKEN|PGCAT_ADMIN_PASSWORD)
                printf -v "$_smsly_secrets_key" '%s' "$_smsly_secrets_val"
                ;;
        esac
    done < <(python3 "$INSTALL_DIR/scripts/generate_env_secrets.py" 2>/dev/null | grep -E '^[A-Z_]+=' || true)
    unset _smsly_secrets_key _smsly_secrets_val
    if [ -n "${SECRET_KEY:-}" ] && [ -n "${FIELD_ENCRYPTION_KEY:-}" ]; then
        SECRETS_GENERATED=true
        echo -e "${GREEN}  ✓ Secrets generated (Fernet key validated)${NC}"
    else
        echo -e "${YELLOW}  ⚠ Secrets script ran but Fernet key is missing — generating inline...${NC}"
    fi

    # Fallback: if the script didn't produce a valid Fernet key, generate it inline
    # (cryptography is guaranteed importable at this point from the check above).
    if [ -z "${FIELD_ENCRYPTION_KEY:-}" ]; then
        FIELD_ENCRYPTION_KEY="${MASTER_FIELD_ENCRYPTION_KEY:-$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || true)}"
    fi
    # Ensure all other secrets have fallback values just in case
    [ -n "${SECRET_KEY:-}" ] || SECRET_KEY="$(python3 -c "import secrets,string; chars=string.ascii_letters+string.digits; print(''.join(secrets.choice(chars) for _ in range(50)))" 2>/dev/null || true)"
    [ -n "${POSTGRES_PASSWORD:-}" ] || POSTGRES_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null || true)"
    [ -n "${REDIS_PASSWORD:-}" ] || REDIS_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null || true)"
    [ -n "${RABBITMQ_PASSWORD:-}" ] || RABBITMQ_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null || true)"
    [ -n "${GATEWAY_SECRET:-}" ] || GATEWAY_SECRET="$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
    [ -n "${GITHUB_WEBHOOK_SECRET:-}" ] || GITHUB_WEBHOOK_SECRET="$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
    [ -n "${AUTOSCALER_API_TOKEN:-}" ] || AUTOSCALER_API_TOKEN="$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
    [ -n "${FRP_AUTH_TOKEN:-}" ] || FRP_AUTH_TOKEN="$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
    [ -n "${PGCAT_ADMIN_PASSWORD:-}" ] || PGCAT_ADMIN_PASSWORD="$(python3 -c "import secrets; print(secrets.token_hex(24))" 2>/dev/null || true)"
    [ -n "${GRAFANA_PASSWORD:-}" ] || GRAFANA_PASSWORD="$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits+'-_') for _ in range(40)))" 2>/dev/null || openssl rand -base64 30 | tr -d '+/=' )"
    [ -n "${BACKUP_ENCRYPTION_KEY:-}" ] || BACKUP_ENCRYPTION_KEY="$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || openssl rand -base64 32)"
    [ -n "${CROWDSEC_BOUNCER_KEY:-}" ] || CROWDSEC_BOUNCER_KEY="$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null || true)"
    [ -n "${BACKUP_REQUIRE_ENCRYPTION:-}" ] || BACKUP_REQUIRE_ENCRYPTION="true"
    # SECURITY: SSH strict host-key checking. Defaults to false (accept-first)
    # for convenience during initial provisioning. Operators managing trusted
    # environments with pre-populated known_hosts should set this to "true".
    [ -n "${SMSLY_STRICT_SSH_HOST_KEY_CHECK:-}" ] || SMSLY_STRICT_SSH_HOST_KEY_CHECK="false"
    # Optional read-replica plumbing (only used when docker-compose.replica.yml
    # is enabled). Initialize empty defaults so set -u doesn't trip on them
    # later in the .env heredoc.
    [ -n "${REPLICATION_PASSWORD:-}" ] || REPLICATION_PASSWORD=""
    [ -n "${DB_REPLICA_HOSTS:-}" ] || DB_REPLICA_HOSTS=""

    # Validate Fernet key format
    if ! echo "$FIELD_ENCRYPTION_KEY" | python3 -c "
import sys
from cryptography.fernet import Fernet
try:
    Fernet(sys.stdin.read().strip().encode())
    print('valid')
except Exception:
    print('invalid')
" 2>/dev/null | grep -q valid; then
        echo -e "${RED}  ✗ CRITICAL: Failed to generate a valid Fernet encryption key.${NC}"
        echo -e "${RED}    Ensure the 'cryptography' package is installed and retry.${NC}"
        echo -e "${RED}    pip3 install cryptography${NC}"
        exit 1
    fi

    echo -e "${GREEN}  ✓ All secrets generated successfully${NC}"



    # Agent-lite nodes must use the master's DB password, not a locally generated one.
    # SSH into the master to fetch the correct POSTGRES_PASSWORD.
    if is_agent_lite_mode && [ -n "${MASTER_IP:-}" ] && [ "$MASTER_IP" != "127.0.0.1" ]; then
        echo -e "${BLUE}  → Fetching master DB password via SSH (master: ${MASTER_IP})...${NC}"
        _master_db_pw="$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes root@${MASTER_IP} \
            "grep '^POSTGRES_PASSWORD=' /opt/smsly-hosting/.env 2>/dev/null | head -1 | cut -d= -f2" 2>/dev/null || true)"
        if [ -n "${_master_db_pw:-}" ]; then
            POSTGRES_PASSWORD="$_master_db_pw"
            echo -e "${GREEN}  ✓ Retrieved master DB password${NC}"
        else
            echo -e "${YELLOW}  ⚠ Could not retrieve master DB password via SSH. DATABASE_URL may not connect.${NC}"
            echo -e "${YELLOW}    Tip: Pass MASTER_DB_PASSWORD=... to the install script.${NC}"
        fi
    fi

    # Create .env (Atomic)
    ENV_TMP="$INSTALL_DIR/.env.tmp"
    ENV_MODE_VALUE="$(mode_env_value)"
    ENV_NODE_TYPE="$INSTALL_MODE"
    ENV_TRAEFIK_HTTP_BIND="127.0.0.1:8081"
    ENV_TRAEFIK_HTTPS_BIND="127.0.0.1:8443"
    ENV_STARTUP_CADDY_SYNC="true"
    if is_agent_lite_mode; then
        ENV_NODE_TYPE="agent-lite"
        ENV_STARTUP_CADDY_SYNC="false"
    elif is_node_mode; then
        ENV_NODE_TYPE="node"
        ENV_TRAEFIK_HTTP_BIND="0.0.0.0:80"
        ENV_TRAEFIK_HTTPS_BIND="0.0.0.0:443"
        ENV_STARTUP_CADDY_SYNC="false"
    fi
    cat <<EOF > "$ENV_TMP"
# SMSLY Hosting Configuration — Generated $(date -Iseconds)
ENVIRONMENT=production
NODE_TYPE=$ENV_NODE_TYPE
MODE=$ENV_MODE_VALUE
# Compose file used by 'install.sh --update' and other orchestrator scripts.
# NOTE: inside an unquoted heredoc (cat <<EOF), bash still expands
# command substitution on comment lines too. Do NOT put unescaped
# dollar-paren or backtick sequences in heredoc comments.
# Master mode: docker-compose.yml (base file with traefik + caddy inlined).
# Agent-lite mode: overridden below to infrastructure/docker/docker-compose.agent-lite.yml.
COMPOSE_FILE=docker-compose.yml
DEBUG=False
SECRET_KEY=$SECRET_KEY
FIELD_ENCRYPTION_KEY=$FIELD_ENCRYPTION_KEY
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
POSTGRES_USER=smsly_admin
POSTGRES_DB=smsly_hosting
DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@pgcat:5432/smsly_hosting
DATABASE_CONNECT_TIMEOUT=5

REDIS_PASSWORD=$REDIS_PASSWORD
RABBITMQ_PASSWORD=$RABBITMQ_PASSWORD
RABBITMQ_DEFAULT_USER=smsly_user
RABBITMQ_DEFAULT_PASS=$RABBITMQ_PASSWORD
REDIS_URL=redis://:$REDIS_PASSWORD@redis:6379/0
# CELERY_ prefix is required for celery-redbeat to read this (see
# backend/config/settings.py: CELERY_REDBEAT_REDIS_URL). Without the prefix
# redbeat falls back to CELERY_BROKER_URL (RabbitMQ AMQP) and redis-py
# crashes with "Redis URL must specify one of the following schemes".
CELERY_REDBEAT_REDIS_URL=redis://:$REDIS_PASSWORD@redis:6379/3
# Optional PostgreSQL streaming-replication password. Required ONLY
# when docker-compose.replica.yml is enabled (opt-in read replica).
# Leave empty to skip replica setup. When set, the
# render_pgcat_config.py generator automatically routes SELECTs to
# the replica(s) listed in DB_REPLICA_HOSTS.
REPLICATION_PASSWORD=${REPLICATION_PASSWORD:-}
# Comma-separated list of read-replica endpoints. Used by pgcat
# to route SELECTs to replicas. Default empty = single-node.
# Example after enabling docker-compose.replica.yml:
#   DB_REPLICA_HOSTS=db-replica:5432
DB_REPLICA_HOSTS=${DB_REPLICA_HOSTS:-}
REDIS_SOCKET_TIMEOUT=5
CELERY_BROKER_URL=amqp://smsly_user:$RABBITMQ_PASSWORD@rabbitmq:5672//

DOMAIN=$DOMAIN
ACME_EMAIL=${ACME_EMAIL:-}
USE_SSL=$USE_SSL

# Inter-service HMAC authentication secret
GATEWAY_SECRET=$GATEWAY_SECRET

# CrowdSec Bouncer Key
CROWDSEC_BOUNCER_KEY=$CROWDSEC_BOUNCER_KEY

# GitHub webhook signature verification
GITHUB_WEBHOOK_SECRET=$GITHUB_WEBHOOK_SECRET

# Security
ALLOWED_HOSTS=$DOMAIN,localhost,127.0.0.1
EOF

    # Build scheme-appropriate origins (avoid https://IP which breaks CORS/CSRF)
    if echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' || [ "$USE_SSL" != "true" ]; then
        DOMAIN_ORIGINS="http://$DOMAIN"
    else
        DOMAIN_ORIGINS="https://$DOMAIN"
    fi
    cat >> "$ENV_TMP" <<EOF
CSRF_TRUSTED_ORIGINS=http://$PUBLIC_IP:8090,$DOMAIN_ORIGINS,http://localhost:8090,http://$PUBLIC_IP
CORS_ALLOWED_ORIGINS=http://$PUBLIC_IP:8090,$DOMAIN_ORIGINS,http://$PUBLIC_IP

# Docker networking
# Ensure addon containers and deployed app containers share the same network for connectivity.
DOCKER_NETWORK=smsly-net

# Wildcard subdomain SSL (Cloudflare DNS challenge)
WILDCARD_SUBDOMAINS=$WILDCARD_SUBDOMAINS
CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN:-}
CADDY_CONFIG_DIR=/caddy-config
PUBLIC_IP=$PUBLIC_IP

# Autoscaler API authentication (shared with smsly-autoscaler.service)
AUTOSCALER_API_TOKEN=$AUTOSCALER_API_TOKEN

# FRP Tunnel Relay Authentication Token
FRP_AUTH_TOKEN=$FRP_AUTH_TOKEN

# PgCat administration password
PGCAT_ADMIN_PASSWORD=$PGCAT_ADMIN_PASSWORD

# Grafana admin password (used by the standalone observability stack)
GRAFANA_PASSWORD=${GRAFANA_PASSWORD:-}

# Grafana external URL for browser embeds (auto-derived from domain)
GRAFANA_EXTERNAL_URL=${DOMAIN_ORIGINS}/grafana

# Direct database connection for migrations (bypasses PgCat pooler)
DIRECT_DATABASE_URL=postgresql://smsly_admin:$POSTGRES_PASSWORD@db:5432/smsly_hosting

# Private Docker registry (push/pull deployment images)
CONTAINER_REGISTRY_URL=127.0.0.1:5000
REGISTRY_USER=smsly-registry

# The installer runs first-boot Django setup explicitly after the stack starts.
# Keep the web container from doing the same work while Compose is waiting on health.
SMSLY_RUN_ENTRYPOINT_TASKS=false

# AppConfig.ready() must stay side-effect free during installs and management commands.
# Edge/proxy sync is performed explicitly by the installer and watcher services.
SMSLY_ENABLE_STARTUP_CADDY_SYNC=$ENV_STARTUP_CADDY_SYNC
TRAEFIK_HTTP_BIND=$ENV_TRAEFIK_HTTP_BIND
TRAEFIK_HTTPS_BIND=$ENV_TRAEFIK_HTTPS_BIND
EOF

    # ─── Dynamic Build Resource Allocation ──────────────────────────────
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        echo -e "${BLUE}  → Lite Agent mode: frontend build is not part of this node.${NC}"
    elif [ "$MODE_NODE" = "true" ]; then
        echo -e "${BLUE}  → Node mode: frontend build is not part of this node.${NC}"
    else
        # Detect physical RAM for optimized build limits
        current_ram_mb=$(free -m | awk '/^Mem:/{print $2}')
        build_mem=2048
        if [ "$current_ram_mb" -ge 16384 ]; then
            build_mem=8192
        elif [ "$current_ram_mb" -ge 8192 ]; then
            build_mem=4096
        fi
        echo "FRONTEND_BUILD_MEMORY_MB=$build_mem" >> "$ENV_TMP"
        echo -e "${BLUE}  → Allocated ${build_mem}MB for frontend build (System RAM: ${current_ram_mb}MB)${NC}"
    fi

    # Derive expected tunnel domain
    if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ] && ! echo "$DOMAIN" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
        EXPECTED_TUNNEL_DOMAIN="tunnel.${DOMAIN}"
    elif [ -n "$PUBLIC_IP" ] && ! echo "$PUBLIC_IP" | grep -qE '^(127\.0\.0\.1|0\.0\.0\.0)$'; then
        EXPECTED_TUNNEL_DOMAIN="tunnel.${PUBLIC_IP}.sslip.io"
    else
        EXPECTED_TUNNEL_DOMAIN="tunnel.localhost"
    fi
    echo "TUNNEL_DOMAIN=$EXPECTED_TUNNEL_DOMAIN" >> "$ENV_TMP"

    # ── Agent Lite Overrides ──────────────────────────────────────
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        apply_agent_lite_env_overrides "$ENV_TMP"
    fi

    # Atomic move and validation
    if validate_env_file "$ENV_TMP"; then
        mv "$ENV_TMP" "$INSTALL_DIR/.env"
        # 664 so the backend container (runs as UID 1000) can read AND write it.
        # This allows the domain-config signal to persist DOMAIN/USE_SSL back to
        # .env when the user updates settings via the web UI — no SSH needed.
        chown root:1000 "$INSTALL_DIR/.env"
        chmod 664 "$INSTALL_DIR/.env"
        # Docker Compose v2+ resolves .env from the compose file's parent directory,
        # not the CWD. Create a symlink so all compose files can find it.
        _compose_env_link="$INSTALL_DIR/infrastructure/docker/.env"
        rm -f "$_compose_env_link" 2>/dev/null || true
        ln -sf ../../.env "$_compose_env_link" 2>/dev/null || true
        echo -e "${GREEN}  ✓ Configuration saved to .env${NC}"
    else
        echo -e "${RED}  x Generated .env failed validation. Aborting install.${NC}"
        rm -f "$ENV_TMP"
        exit 1
    fi
fi
    set_checkpoint "config_generated"
fi
if [ -f "$INSTALL_DIR/.env" ]; then
    ensure_env_runtime_defaults "$INSTALL_DIR/.env"
    apply_agent_lite_env_overrides "$INSTALL_DIR/.env"
    # Ensure .env symlink exists for Docker Compose v2+ .env resolution
    _compose_env_link="$INSTALL_DIR/infrastructure/docker/.env"
    rm -f "$_compose_env_link" 2>/dev/null || true
    ln -sf ../../.env "$_compose_env_link" 2>/dev/null || true
    if ! validate_env_file "$INSTALL_DIR/.env"; then
        echo -e "${RED}x Existing .env is invalid after runtime-default reconciliation.${NC}"
        exit 1
    fi
fi
load_install_env_defaults "$INSTALL_DIR/.env"

# Ensure all variables in .env are exported to the environment so they are inherited by docker compose
if [ -f "$INSTALL_DIR/.env" ]; then
    set -a
    source "$INSTALL_DIR/.env"
    set +a
fi

# -----------------------------------------------------------------------------
# 4. Deployment
# -----------------------------------------------------------------------------
STACK_DEPLOYED_FROM_CHECKPOINT=false
if is_checkpoint_done "stack_deployed"; then
    STACK_DEPLOYED_FROM_CHECKPOINT=true
else
    echo -e "\n${YELLOW}[4/9] Deploying Container Stack...${NC}"

# Ensure networks exist
docker network create smsly-net 2>/dev/null || true
docker network create smsly-proxy 2>/dev/null || true

# Ensure external volumes exist.
# docker-compose.yml marks `caddy_data` as `external: true` with fixed name
# `smsly-hosting_caddy_data`. Compose refuses to create external volumes
# and aborts `up` with `external volume "..." not found` if they are
# missing. Pre-create here (idempotent — Compose / Docker return a benign
# "already exists" error which we swallow).
#
# Note: caddy_config is no longer a separate named volume. The caddy
# container now reads /config from the same ./caddy-config bind mount
# the backend writes the IP self-signed cert to, fixing the
# "open /config/certs/ip.crt: no such file or directory" crash loop.
if command -v docker >/dev/null 2>&1; then
    docker volume create --name smsly-hosting_caddy_data 2>/dev/null || true

    # Caddy container runs as uid 1000 (nextjs user); chown the volume
    # root so the container can read/write its ACME state. Same pattern
    # already used for backups_data in ensure_infrastructure_permissions.
    if docker volume inspect smsly-hosting_caddy_data >/dev/null 2>&1; then
        docker run --rm -v smsly-hosting_caddy_data:/data alpine chown -R 1000:1000 /data 2>/dev/null || true
    fi
fi

# ─── BLINDSPOT FIX: Ensure entrypoint.sh has execute permissions ────────────
# Windows git can strip +x bits. Fix before building.
#
# NOTE: backend/Dockerfile already runs `chmod +x entrypoint.sh` inside the image.
# Avoid mutating the git working tree on the host (file mode flips can block `git pull`).
#

# Both IP and SSL modes use the same compose stack.
# Master exposes public HTTP/HTTPS through Caddy; node/agent modes expose HTTP through Traefik.
# Generate registry TLS cert + htpasswd if missing (required for auth-enabled registry)
echo -e "${BLUE}  → Configuring Docker registry auth and TLS...${NC}"
mkdir -p "$INSTALL_DIR/auth" "$INSTALL_DIR/certs"

# Regenerate registry TLS if EITHER file is missing OR if the existing
# key/cert don't match (e.g. one was rotated independently). The earlier
# `||` check only caught missing files; mismatched pairs caused
# `registry:2.8.3` to crash-loop with "tls: private key does not match
# public key" forever. Regenerating as a matched pair is the only safe
# option — we cannot repair an existing cert without the issuing key.
_regen_registry_tls() {
    echo -e "${BLUE}    Generating self-signed TLS cert for registry...${NC}"
    # openssl req writes key then cert; if key write fails halfway the
    # cert from the prior generation would be orphaned. The atomic
    # rename pattern below ensures consumers (the registry container)
    # never see a half-written pair.
    _tmp_dir="$(mktemp -d)"
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "${_tmp_dir}/registry.key" \
        -out    "${_tmp_dir}/registry.crt" \
        -subj "/CN=registry" \
        -addext "subjectAltName=DNS:registry,DNS:localhost,IP:127.0.0.1,IP:10.100.0.1" 2>/dev/null
    local _rc=$?
    if [ "$_rc" -ne 0 ]; then
        rm -rf "$_tmp_dir"
        echo -e "${YELLOW}    ⚠ Failed to generate registry cert (openssl missing?)${NC}"
        return $_rc
    fi
    mv "${_tmp_dir}/registry.key" "$INSTALL_DIR/certs/registry.key"
    mv "${_tmp_dir}/registry.crt" "$INSTALL_DIR/certs/registry.crt"
    rm -rf "$_tmp_dir"
    chmod 644 "$INSTALL_DIR/certs/registry.crt" "$INSTALL_DIR/certs/registry.key"
}

_registry_tls_ok() {
    [ -f "$INSTALL_DIR/certs/registry.key" ] || return 1
    [ -f "$INSTALL_DIR/certs/registry.crt" ] || return 1
    # openssl x509 -noout -modulus matches the cert's modulus;
    # openssl rsa  -noout -modulus matches the key's modulus. They must
    # be equal for the TLS handshake to succeed.
    local _cmod _kmod
    _cmod="$(openssl x509 -in "$INSTALL_DIR/certs/registry.crt" -noout -modulus 2>/dev/null | openssl sha256)" || return 1
    _kmod="$(openssl rsa  -in "$INSTALL_DIR/certs/registry.key" -noout -modulus 2>/dev/null | openssl sha256)" || return 1
    [ "$_cmod" = "$_kmod" ]
}

if ! _registry_tls_ok; then
    _regen_registry_tls
    if ! _registry_tls_ok; then
        echo -e "${RED}    ✗ Registry TLS cert/key still mismatched or missing after regen attempt${NC}"
        echo -e "${YELLOW}      Manual fix on host: openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \\${NC}"
        echo -e "${YELLOW}        -keyout /opt/smsly-hosting/certs/registry.key \\${NC}"
        echo -e "${YELLOW}        -out    /opt/smsly-hosting/certs/registry.crt \\${NC}"
        echo -e "${YELLOW}        -subj '/CN=registry'${NC}"
    else
        echo -e "${BLUE}    Restarting registry container to pick up new TLS certs...${NC}"
        docker restart smsly-hosting-registry-1 2>/dev/null || true
    fi
fi
if [ ! -f "$INSTALL_DIR/auth/htpasswd" ] || [ -z "${REGISTRY_PASSWORD:-}" ] || [ -z "${REGISTRY_USER:-}" ]; then
    REGISTRY_PASS="${REGISTRY_PASSWORD:-$(python3 -c "import secrets; print(secrets.token_urlsafe(18))" 2>/dev/null || openssl rand -hex 12 2>/dev/null || echo 'auto-generated-change-me')}"
    if command -v htpasswd >/dev/null 2>&1; then
        htpasswd -Bbn "${REGISTRY_USER:-smsly-registry}" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd"
    else
        # Python-based bcrypt fallback
        python3 -c "
import bcrypt, sys
pw = sys.argv[1] if len(sys.argv) > 1 else '$REGISTRY_PASS'
print(f'${REGISTRY_USER:-smsly-registry}:' + bcrypt.hashpw(pw.encode(), bcrypt.gensalt(10)).decode())
" "$REGISTRY_PASS" > "$INSTALL_DIR/auth/htpasswd" 2>/dev/null || \
        echo -e "${YELLOW}    ⚠ Failed to generate htpasswd (neither htpasswd nor python bcrypt available)${NC}"
    fi
    env_set_value "$INSTALL_DIR/.env" "REGISTRY_USER" "${REGISTRY_USER:-smsly-registry}"
    env_set_value "$INSTALL_DIR/.env" "REGISTRY_PASSWORD" "$REGISTRY_PASS"
fi
echo -e "${GREEN}  ✓ Registry auth + TLS configured${NC}"

# Authenticate Docker CLI with the private registry so the daemon can
# pull base images during builds without 403 errors.
docker_login

# Ensure bind-mounted config paths exist before `docker compose up`.
ensure_infrastructure_permissions
if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "${BLUE}  → Lite Agent mode: disabling master-only Caddy services before Traefik bind.${NC}"
    true
elif [ "$MODE_NODE" = "true" ]; then
    echo -e "${BLUE}  → Node mode: deploying prod stack without frontend/Caddy; Traefik binds public HTTP.${NC}"
fi
echo -e "${BLUE}  → Disabling backend entrypoint bootstrap for installer-controlled migrations...${NC}"
env_set_value "$INSTALL_DIR/.env" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
    echo -e "${BLUE}  → Starting App Stack (Build + Deploy)...${NC}"
    cleanup_stale_containers
    ( while true; do sleep 30; echo -e "${BLUE}      ↳ Progress: Deployment in progress... $(date +%H:%M:%S)${NC}"; done ) &
    HEARTBEAT_PID=$!
    # TODO(install): replace set -e toggle with explicit conditional. The
    # conditional rebuild + retry makes a flat `if ! cmd` rewrite risky; the
    # rc-capture pattern is intentionally retained.
    set +e
    compose_stack_build --no-cache
    DEPLOY_RC=$?
    if [ "$DEPLOY_RC" -eq 0 ]; then
        # Scan freshly built images for vulnerabilities
        if command -v trivy >/dev/null 2>&1; then
            echo -e "${BLUE}  → Scanning built images for vulnerabilities...${NC}"
            for _trivy_img in backend frontend; do
                _trivy_tag="smsly/${_trivy_img}:latest"
                if docker image inspect "$_trivy_tag" >/dev/null 2>&1; then
                    echo -e "${BLUE}    ↳ Scanning $_trivy_tag...${NC}"
                    trivy image --severity CRITICAL,HIGH --exit-code 0 --no-progress "$_trivy_tag" 2>/dev/null || true
                fi
            done
            unset _trivy_img _trivy_tag
        fi
        compose_stack_up --remove-orphans
        DEPLOY_RC=$?
    fi
    set -e
    kill $HEARTBEAT_PID 2>/dev/null || true
    wait $HEARTBEAT_PID 2>/dev/null || true
    if [ "$DEPLOY_RC" -ne 0 ]; then
        echo -e "${RED}  ✗ Docker Compose failed during stack deployment (exit $DEPLOY_RC).${NC}"
        echo -e "${YELLOW}  ↳ Re-run with --resume to skip completed steps: sudo bash install.sh --resume${NC}"
        docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true
        docker compose -f "$COMPOSE_FILE" logs --tail=120 2>/dev/null || true
        exit "$DEPLOY_RC"
    fi
    if [ "$MODE_AGENT_LITE" = "true" ]; then
        sync_agent_lite_rabbitmq_password
    else
        echo -e "${BLUE}  → Deploying Observability Stack...${NC}"
        if [ -f "infrastructure/docker/docker-compose.observability.yml" ]; then
            docker compose -f infrastructure/docker/docker-compose.observability.yml pull --ignore-pull-failures || \
                echo -e "${YELLOW}  ⚠ Observability stack pull failed (non-fatal)${NC}"
            docker compose -f infrastructure/docker/docker-compose.observability.yml up -d --pull always || \
                echo -e "${YELLOW}  ⚠ Observability stack start failed (non-fatal)${NC}"
        fi
    fi
    # Deploy docker-labels exporter to all remote nodes and regenerate target files
    if [ "$MODE_AGENT_LITE" != "true" ]; then
        backend_container=$(docker ps --format '{{.Names}}' | grep -E '^smsly-hosting-backend(-1)?$' | head -1)
        if [ -n "$backend_container" ]; then
            docker exec "$backend_container" python manage.py deploy_docker_labels_exporters 2>/dev/null || true
        fi
    fi
    set_checkpoint "stack_deployed"

    # Docker login now that the registry is actually running
    docker_login
fi
if [ "$STACK_DEPLOYED_FROM_CHECKPOINT" = "true" ]; then
    reconcile_compose_stack_after_resume
fi

# -----------------------------------------------------------------------------
# 5. Database Setup
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "database_initialized"; then
    echo -e "\n${YELLOW}[5/9] Initializing Database...${NC}"

if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "${BLUE}  → Lite Agent mode: skipping local database initialization; using Master services.${NC}"
    set_checkpoint "database_initialized"
else
echo -e "${BLUE}  → Waiting for Database...${NC}"
DB_READY=false
for i in $(seq 1 24); do
    if docker compose -f "$COMPOSE_FILE" exec -T db pg_isready -U smsly_admin >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ Database is ready (attempt $i).${NC}"
        DB_READY=true
        break
    fi
    printf "."
    sleep 5
done
echo ""

if [ "$DB_READY" != "true" ]; then
    echo -e "${RED}  ✗ Database failed to become ready after 2 minutes.${NC}"
    echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs db${NC}"
    exit 1
fi

# ─── Sync DB password to match .env (handles volume from previous install) ──
# The DB volume persists with the password from FIRST init.
# Always reset the password inside PostgreSQL to match the current .env.
set -a
source "$INSTALL_DIR/.env" 2>/dev/null || true
set +a
echo -e "${BLUE}  → Syncing database password...${NC}"

# Try local trust auth first (Docker default), then try with PGPASSWORD
if docker compose -f "$COMPOSE_FILE" exec -T db \
    psql -U postgres -c "ALTER USER smsly_admin WITH PASSWORD '${POSTGRES_PASSWORD}';" \
    >/dev/null 2>&1; then
    echo -e "${GREEN}  ✓ Database password synced${NC}"
elif docker compose -f "$COMPOSE_FILE" exec -T -e PGPASSWORD="${POSTGRES_PASSWORD}" db \
    psql -U smsly_admin -d smsly_hosting -c "SELECT 1;" >/dev/null 2>&1; then
    echo -e "${GREEN}  ✓ Database password already matches${NC}"
else
    echo -e "${YELLOW}  ⚠ Password mismatch — resetting via postgres superuser...${NC}"
    # Last resort: the Docker postgres container always accepts local postgres user
    docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U postgres -c "ALTER USER smsly_admin WITH PASSWORD '${POSTGRES_PASSWORD}';" \
        2>&1 || echo -e "${RED}  ✗ Could not sync password. Check pg_hba.conf${NC}"
fi

# ─── Ensure PgCat is fresh and connected ──────────────────────────────────────
if [ -f "${COMPOSE_FILE:-docker-compose.prod.yml}" ] && grep -q "^  *pgcat:" "${COMPOSE_FILE:-docker-compose.prod.yml}" 2>/dev/null && docker compose -f "$COMPOSE_FILE" ps pgcat >/dev/null 2>&1; then
    echo -e "${BLUE}  → Restarting PgCat balancer...${NC}"
    docker compose -f "$COMPOSE_FILE" restart pgcat >/dev/null 2>&1
    sleep 5
fi

# ─── Restart backend so it picks up the correct DB credentials ──────────────
echo -e "${BLUE}  → Restarting backend with synced credentials...${NC}"
docker compose -f "$COMPOSE_FILE" restart backend >/dev/null 2>&1
sleep 5

    echo -e "${BLUE}  → Running Migrations...${NC}"

    # Stop all services that talk to the DB.  Any open connection — even
    # a SELECT — holds a shared lock that blocks the ACCESS EXCLUSIVE
    # lock an ALTER TABLE needs.  Celery, backend health checks, and
    # PgCat connection pools all compete with the migration.
    MIGRATION_STOPPED_SVCS="backend celery celery-deploy celery-fast celery-beat $(grep -q "^  *pgcat:" "${COMPOSE_FILE:-docker-compose.prod.yml}" 2>/dev/null && echo "pgcat")"
    echo -e "${BLUE}    Stopping ${MIGRATION_STOPPED_SVCS} to prevent lock contention...${NC}"
    docker compose -f "$COMPOSE_FILE" stop ${MIGRATION_STOPPED_SVCS} >/dev/null 2>&1 || true
    sleep 3

    # Kill every backend on the database so the migration owns it exclusively
    docker compose -f "$COMPOSE_FILE" exec -T db \
        psql -U smsly_admin -d smsly_hosting \
        -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'" \
        >/dev/null 2>&1 || true
    sleep 2

    echo -e "${BLUE}    Running migrations (database: direct)...${NC}"
    # Note: Do NOT run makemigrations — migrations are committed in the repo.
    MIGRATE_OK=false
    # Migration runs via DIRECT_DATABASE_URL which goes straight to the
    # postgres backend, not through PgCat, so PgCat being stopped is safe.
    if run_backend_migrations 2>&1; then
        MIGRATE_OK=true
    else
        echo -e "${YELLOW}  ⚠ Migration attempt 1 failed — killing stale connections and retrying...${NC}"
        docker compose -f "$COMPOSE_FILE" exec -T db \
            psql -U smsly_admin -d smsly_hosting \
            -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'" \
            >/dev/null 2>&1 || true
        sleep 5
        if run_backend_migrations 2>&1; then
            MIGRATE_OK=true
        fi
    fi

    # Restart everything that was paused
    echo -e "${BLUE}    Restarting ${MIGRATION_STOPPED_SVCS}...${NC}"
    docker compose -f "$COMPOSE_FILE" start ${MIGRATION_STOPPED_SVCS} >/dev/null 2>&1 || true
    sleep 5

    if [ "$MIGRATE_OK" != "true" ]; then
        echo -e "${RED}  ✗ Migrations failed after 2 attempts.${NC}"
        echo -e "${YELLOW}  Check: docker compose -f $COMPOSE_FILE logs backend${NC}"
        echo -e "${YELLOW}  ↳ Tip: Re-run with --resume: sudo bash install.sh --resume${NC}"
        exit 1
    fi

echo -e "${BLUE}  → Collecting Static Files...${NC}"
    # Fix volume ownership — Docker creates named volumes as root
    docker compose -f "$COMPOSE_FILE" exec -T --user root backend chown -R 1000:1000 /app/staticfiles /app/media /app/backups 2>/dev/null || true
    docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py collectstatic --noinput

    sync_platform_domain_state "$INSTALL_DIR/.env"
    set_checkpoint "database_initialized"
fi
fi

# -----------------------------------------------------------------------------
# 6. Admin User (IDEMPOTENT — skips if admin already exists)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "admin_created"; then
    echo -e "\n${YELLOW}[6/9] Creating Admin User...${NC}"

if [ "$MODE_AGENT_LITE" = "true" ]; then
    echo -e "${BLUE}  → Lite Agent mode: skipping master admin and Local Docker provider setup.${NC}"
    set_checkpoint "admin_created"
else
ADMIN_EXISTS=$(echo "from django.contrib.auth import get_user_model; User = get_user_model(); print('1' if User.objects.filter(username='admin').exists() else '0')" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1)

if [ "${ADMIN_EXISTS:-0}" = "1" ]; then
    echo -e "${GREEN}  ✓ Admin user check bypassed or already exists — skipping${NC}"
    if [ -f "$CREDENTIALS_FILE" ]; then
        echo -e "${GREEN}  ✓ Credentials file exists — leaving unchanged${NC}"
    else
        # Best effort: don't overwrite an unknown existing password.
        cat > "$CREDENTIALS_FILE" <<CREDS
# SMSLY Hosting Admin Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE
Username: admin
Password: <existing — not changed by installer>
CREDS
        chmod 600 "$CREDENTIALS_FILE"
    fi
else
    # Production hardening: never ship with a default admin password.
    # Use a shell-safe hex password (avoids quoting issues in manage.py shell).
    if [ "$MODE_AGENT_LITE" = "false" ]; then
        ADMIN_PASS="$(gen_hex_secret 16)"
        echo "
from django.contrib.auth import get_user_model
from rest_framework.authtoken.models import Token
User = get_user_model()
admin = User.objects.create_superuser('admin', 'admin@smsly.cloud', '$ADMIN_PASS')
token = Token.objects.create(user=admin)
print(token.key)
" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1 > "$INSTALL_DIR/.token"
        echo -e "${GREEN}  ✓ Admin user created with API Token${NC}"
        chmod 600 "$INSTALL_DIR/.token"

        # ─── Save credentials to secure file (NOT echoed to terminal) ───────────────
        cat > "$CREDENTIALS_FILE" <<CREDS
# SMSLY Hosting Admin Credentials
# Generated: $(date -Iseconds)
# KEEP THIS FILE SECURE
Username: admin
Password: $ADMIN_PASS
CREDS
        chmod 600 "$CREDENTIALS_FILE"

        # -----------------------------------------------------------------------------
        # 6b. Ensure Local Cloud Provider exists (required for deployments)
        # -----------------------------------------------------------------------------
        echo -e "${BLUE}  → Ensuring Local Docker cloud provider exists...${NC}"
        echo "
from apps.cloud.models import CloudProvider
cp, created = CloudProvider.objects.get_or_create(
    provider_type='LOCAL',
    defaults={'name': 'Local Docker', 'is_active': True}
)
if not created and not cp.is_active:
    cp.is_active = True
    cp.save()
print('CREATED' if created else 'EXISTS')
" | docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell 2>/dev/null | tail -1 >/dev/null
        echo -e "${GREEN}  ✓ Local Docker cloud provider ready${NC}"
    fi
fi
    echo -e "${BLUE}  → Keeping backend entrypoint bootstrap disabled; installer controls migrations...${NC}"
env_set_value "$INSTALL_DIR/.env" "SMSLY_RUN_ENTRYPOINT_TASKS" "false"
if should_manage_caddy; then
    env_set_value "$INSTALL_DIR/.env" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "true"
else
    env_set_value "$INSTALL_DIR/.env" "SMSLY_ENABLE_STARTUP_CADDY_SYNC" "false"
fi

    # ─── Generate Recovery Phrase ─────────────────────────────────────────
    echo -e "${BLUE}  → Generating 12-word recovery phrase...${NC}"
    RECOVERY_PHRASE="$(docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py shell -c "
from apps.deployments.views_recovery import recovery_phrase_generate
from django.test.client import RequestFactory
factory = RequestFactory()
request = factory.get('/api/v1/auth/recovery/generate/')
request.user = __import__('django').contrib.auth.get_user_model().objects.filter(is_superuser=True).first()
from django.contrib.sessions.middleware import SessionMiddleware
from django.middleware.csrf import CsrfViewMiddleware
# Minimal request setup for the view to work
response = recovery_phrase_generate(request)
import json
print(json.dumps(response.data))
" 2>/dev/null | tail -1 || true)"
    if [ -n "$RECOVERY_PHRASE" ]; then
        RECOVERY_PHRASE_TEXT="$(printf '%s' "$RECOVERY_PHRASE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('phrase',''))" 2>/dev/null || true)"
        if [ -n "$RECOVERY_PHRASE_TEXT" ]; then
            echo -e "${GREEN}  ✓ Recovery phrase generated${NC}"
            echo -e "$RECOVERY_PHRASE_TEXT" > "$INSTALL_DIR/.recovery_phrase"
            chmod 600 "$INSTALL_DIR/.recovery_phrase"
            echo -e ""
            echo -e "${YELLOW}════════════════════════════════════════════════════════════${NC}"
            echo -e "${YELLOW}   ⚠  ACCOUNT RECOVERY PHRASE — WRITE THIS DOWN             ${NC}"
            echo -e "${YELLOW}   This is the ONLY time this phrase is displayed.            ${NC}"
            echo -e "${YELLOW}   If all trusted devices are lost, this 12-word phrase       ${NC}"
            echo -e "${YELLOW}   is your last resort to recover admin access.               ${NC}"
            echo -e "${YELLOW}                                                              ${NC}"
            echo -e "${YELLOW}   $RECOVERY_PHRASE_TEXT${NC}"
            echo -e "${YELLOW}                                                              ${NC}"
            echo -e "${YELLOW}   Stored (encrypted) in: $INSTALL_DIR/.recovery_phrase${NC}"
            echo -e "${YELLOW}════════════════════════════════════════════════════════════${NC}"
            echo -e ""
        fi
    fi

    set_checkpoint "admin_created"
fi
fi

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
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate caddy

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
                2>/dev/null || true)
            # If Caddy returns "challenge not found" (404), that means it IS
            # reachable but doesn't have this challenge registered — which is
            # the expected behavior for a staging check.
            if echo "$ACME_CHECK" | grep -qi "challenge"; then
                echo -e "${GREEN}  ✓ ACME HTTP-01 reachable for $DOMAIN (staging)${NC}"
                ACME_OK=true
                break
            fi
            # Also try: just checking port 80 responds
            if curl -fsSo /dev/null --max-time 5 "http://${DOMAIN}/" 2>/dev/null; then
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
    systemctl stop caddy 2>/dev/null || true
    systemctl disable caddy 2>/dev/null || true
    rm -f /etc/systemd/system/caddy.service
    systemctl daemon-reload

    set_checkpoint "caddy_configured"
fi
fi # end Caddy skip for agent-lite/node modes

# -----------------------------------------------------------------------------
# 8. System Memory Hardening (Prevents OOM kills)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "memory_hardened"; then
    echo -e "\n${YELLOW}[8/9] Hardening System Memory...${NC}"

# ─── Swap: Ensure swap is at least 4x RAM ────────────────────────────────────
ensure_system_swap

# ─── Auto-Maintenance: Install OOM Swap Adjuster ─────────────────────────────
OOM_SCRIPT="/opt/smsly/scripts/oom-swap-adjuster.sh"
mkdir -p /opt/smsly/scripts
cat << 'EOF' > "$OOM_SCRIPT"
#!/usr/bin/env bash
# oom-swap-adjuster.sh
#
# Monitors the system for Out Of Memory (OOM) kills. If one is detected within the last
# X minutes, it automatically increases the swap space by 200MB up to a maximum of 4x RAM.
# This serves as an auto-maintenance feature to prevent recurring build crashes.

set -euo pipefail

LOG_FILE="/var/log/smsly-oom-adjuster.log"
MINUTES_BACK=10
SWAPFILE_PREFIX="/swapfile-smsly-auto"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Check for OOM events in the last N minutes using journalctl
OOM_COUNT=$(journalctl -k --since "${MINUTES_BACK} minutes ago" | grep -i "out of memory" | wc -l || true)

if [ "$OOM_COUNT" -eq 0 ]; then
    # No OOM detected recently, exit quietly.
    exit 0

fi

log "Detected $OOM_COUNT OOM events in the last $MINUTES_BACK minutes. Evaluating swap size."

# Get RAM size in MB
RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
CURRENT_SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')

# Maximum allowed swap is 4x RAM
MAX_SWAP_MB=$((RAM_MB * 4))

if [ "$CURRENT_SWAP_MB" -ge "$MAX_SWAP_MB" ]; then
    log "Swap is already at or above the maximum allowed limit (4x RAM = ${MAX_SWAP_MB}MB). No further auto-adjustment will be made."
    exit 0
fi

# Calculate new swap chunk to add (200MB)
ADD_SWAP_MB=200
NEW_TOTAL_MB=$((CURRENT_SWAP_MB + ADD_SWAP_MB))

# Cap at max if we would overshoot
if [ "$NEW_TOTAL_MB" -gt "$MAX_SWAP_MB" ]; then
    ADD_SWAP_MB=$((MAX_SWAP_MB - CURRENT_SWAP_MB))
    NEW_TOTAL_MB=$MAX_SWAP_MB
fi

if [ "$ADD_SWAP_MB" -le 0 ]; then
    exit 0
fi

NEW_SWAPFILE="${SWAPFILE_PREFIX}-$(date '+%s')"
log "Increasing swap by ${ADD_SWAP_MB}MB. Creating ${NEW_SWAPFILE}..."

# Create the new swap file
if fallocate -l ${ADD_SWAP_MB}M "$NEW_SWAPFILE" 2>/dev/null; then
    chmod 600 "$NEW_SWAPFILE"
    mkswap "$NEW_SWAPFILE" >/dev/null 2>&1
    swapon "$NEW_SWAPFILE" 2>/dev/null || true

    # Make it permanent
    if ! grep -q "$NEW_SWAPFILE" /etc/fstab 2>/dev/null; then
        echo "$NEW_SWAPFILE none swap sw 0 0" >> /etc/fstab
    fi

    log "Successfully added ${ADD_SWAP_MB}MB of swap. Total swap is now approx ${NEW_TOTAL_MB}MB."
else
    # Fallback to dd if fallocate fails (e.g. some filesystems don't support it)
    log "fallocate failed, trying dd..."
    if dd if=/dev/zero of="$NEW_SWAPFILE" bs=1M count=$ADD_SWAP_MB status=none; then
        chmod 600 "$NEW_SWAPFILE"
        mkswap "$NEW_SWAPFILE" >/dev/null 2>&1
        swapon "$NEW_SWAPFILE" 2>/dev/null || true

        if ! grep -q "$NEW_SWAPFILE" /etc/fstab 2>/dev/null; then
            echo "$NEW_SWAPFILE none swap sw 0 0" >> /etc/fstab
        fi

        log "Successfully added ${ADD_SWAP_MB}MB of swap via dd. Total swap is now approx ${NEW_TOTAL_MB}MB."
    else
        log "Failed to create swap file."
        rm -f "$NEW_SWAPFILE"
        exit 1
    fi
fi
EOF
chmod +x "$OOM_SCRIPT"

# Add cron job to run the script every 5 minutes
CRON_JOB="*/5 * * * * root $OOM_SCRIPT"
if ! grep -q "$OOM_SCRIPT" /etc/crontab 2>/dev/null; then
    echo "$CRON_JOB" >> /etc/crontab
    echo -e "${GREEN}  ✓ OOM Auto-Adjuster installed and scheduled via cron${NC}"
else
    echo -e "${GREEN}  ✓ OOM Auto-Adjuster already scheduled${NC}"
fi

# ─── Sysctl tuning (idempotent) ──────────────────────────────────────────────
SYSCTL_UPDATED=false

ensure_sysctl() {
    local key="$1" value="$2" desc="$3"
    CURRENT=$(sysctl -n "$key" 2>/dev/null || echo "")
    if [ "$CURRENT" != "$value" ]; then
        sysctl -w "$key=$value" >/dev/null 2>&1 || true
        # Make permanent (idempotent)
        if grep -q "^$key" /etc/sysctl.conf 2>/dev/null; then
            sed -i "s|^$key.*|$key = $value|" /etc/sysctl.conf
        else
            echo "# $desc" >> /etc/sysctl.conf
            echo "$key = $value" >> /etc/sysctl.conf
        fi
        SYSCTL_UPDATED=true
        echo -e "${GREEN}  ✓ $key = $value ($desc)${NC}"
    fi
}

ensure_sysctl "vm.overcommit_memory" "1" "Redis background save fix"
ensure_sysctl "vm.swappiness" "10" "Prefer RAM over swap"
ensure_sysctl "net.core.somaxconn" "511" "Redis connection backlog"
# Security Hardening
ensure_sysctl "net.ipv4.conf.all.rp_filter" "1" "IP spoofing protection"
ensure_sysctl "net.ipv4.conf.default.rp_filter" "1" "IP spoofing protection"
ensure_sysctl "net.ipv4.icmp_echo_ignore_broadcasts" "1" "ICMP flood protection"
ensure_sysctl "net.ipv4.conf.all.accept_source_route" "0" "Disable source routing"
ensure_sysctl "net.ipv4.tcp_syncookies" "1" "SYN flood protection"

if [ "$SYSCTL_UPDATED" = "false" ]; then
    echo -e "${GREEN}  ✓ Sysctl settings already optimal${NC}"
fi

# ─── OOM Protection for critical containers ──────────────────────────────────
echo -e "${BLUE}  → Setting OOM protection for critical containers...${NC}"
if [ "$MODE_AGENT_LITE" = "true" ]; then
    CRITICAL_CONTAINERS=(smsly-hosting-traefik-1 smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-socket-proxy-1)
else
    CRITICAL_CONTAINERS=(smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgcat-1 smsly-hosting-celery-1 smsly-hosting-celery-deploy-1 smsly-hosting-celery-fast-1 smsly-hosting-celery-beat-1 smsly-hosting-socket-proxy-1)
fi
for CONTAINER in "${CRITICAL_CONTAINERS[@]}"; do
    resolved_container="$(resolve_container_target "$CONTAINER")"
    CPID=$(docker inspect --format '{{.State.Pid}}' "$resolved_container" 2>/dev/null || echo "")
    if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
        echo -500 > "/proc/$CPID/oom_score_adj" 2>/dev/null || true
    fi
done
echo -e "${GREEN}  ✓ OOM protection set (${CRITICAL_CONTAINERS[*]})${NC}"

# ─── Firewall Hardening (UFW) ────────────────────────────────────────────────
if command -v ufw >/dev/null 2>&1; then
    echo -e "${BLUE}  → Configuring UFW firewall...${NC}"
    ufw default deny incoming >/dev/null 2>&1 || true
    ufw default allow outgoing >/dev/null 2>&1 || true
    # Allow SSH from master IP specifically (provisioning/updates)
    _master_ip="${MASTER_IP:-}"
    if [ -n "$_master_ip" ] && [ "$_master_ip" != "127.0.0.1" ] && ! echo "$_master_ip" | grep -qE '^(0\.0\.0\.0|localhost)$'; then
        echo -e "${BLUE}  → Allowing master ($_master_ip) SSH access...${NC}"
        ufw allow from "$_master_ip" to any port 22 >/dev/null 2>&1 || true
    fi
    # Fallback: allow SSH from any (in case MASTER_IP is empty)
    ufw allow ssh >/dev/null 2>&1 || true
    
    if [ "${INSTALL_MODE:-}" = "agent-lite" ]; then
        if [ -n "$_master_ip" ] && [ "$_master_ip" != "127.0.0.1" ] && ! echo "$_master_ip" | grep -qE '^(0\.0\.0\.0|localhost)$'; then
            ufw allow from "$_master_ip" to any port 80 >/dev/null 2>&1 || true
        else
            echo -e "${YELLOW}  ⚠ Warning: Agent-Lite missing Master IP. Port 80 not exposed.${NC}"
        fi
    else
        ufw allow 80/tcp >/dev/null 2>&1 || true
        ufw allow 443/tcp >/dev/null 2>&1 || true
    fi
    # Allow FRP if active
    if [ -f "$INSTALL_DIR/.env" ] && grep -q "FRP_AUTH_TOKEN" "$INSTALL_DIR/.env"; then
        ufw allow 7000/tcp >/dev/null 2>&1 || true
    fi
    # Allow Docker Mirror (Option B) if this is the Master/Leader
    if [ -z "${MASTER_IP:-}" ] || [ "$MASTER_IP" = "127.0.0.1" ] || [ "$MASTER_IP" = "$(detect_public_ip)" ]; then
        ufw allow 5001/tcp >/dev/null 2>&1 || true
        # Allow Lite Agents to reach core services — RESTRICTED to WireGuard mesh only.
        # These ports carry database/cache/message-queue traffic and must never be
        # exposed to the public internet. Lite Agents connect via the WireGuard VPN
        # mesh (10.100.0.0/24), so we whitelist that subnet plus the master's own
        # mesh IP. Password auth is the second layer of defense.
        echo -e "${BLUE}  → Master node: Restricting DB/Redis/MQ ports to WireGuard mesh (10.100.0.0/24)${NC}"
        ufw allow from 10.100.0.0/24 to any port 5432 proto tcp >/dev/null 2>&1 || true
        ufw allow from 10.100.0.0/24 to any port 6379 proto tcp >/dev/null 2>&1 || true
        ufw allow from 10.100.0.0/24 to any port 5672 proto tcp >/dev/null 2>&1 || true
        # Also allow localhost (container-to-container on the same host)
        ufw allow from 127.0.0.1 to any port 5432 proto tcp >/dev/null 2>&1 || true
        ufw allow from 127.0.0.1 to any port 6379 proto tcp >/dev/null 2>&1 || true
        ufw allow from 127.0.0.1 to any port 5672 proto tcp >/dev/null 2>&1 || true
        # Allow Docker bridge networks (172.16.0.0/12) for container-to-host communication
        ufw allow from 172.16.0.0/12 to any port 5432 proto tcp >/dev/null 2>&1 || true
        ufw allow from 172.16.0.0/12 to any port 6379 proto tcp >/dev/null 2>&1 || true
        ufw allow from 172.16.0.0/12 to any port 5672 proto tcp >/dev/null 2>&1 || true
    fi
    echo "y" | ufw enable >/dev/null 2>&1 || true
    echo -e "${GREEN}  ✓ Firewall hardened (Inbound blocked, SSH/Web permitted)${NC}"
fi

# ── Infrastructure port firewall (DOCKER-USER chain) ────────────────────
# Docker bypasses UFW by inserting its own iptables rules in the DOCKER
# chain. The DOCKER-USER chain is the official way to add custom rules.
# We lock down all infrastructure ports (registry, DB, Redis, RabbitMQ)
# to trusted sources only: localhost, Docker bridges, and WireGuard mesh.
if command -v iptables >/dev/null 2>&1; then
    echo -e "${BLUE}  → Securing infrastructure ports via iptables (DOCKER-USER chain)...${NC}"

    # Ensure DOCKER-USER chain exists (Docker creates it, but be safe)
    iptables -N DOCKER-USER 2>/dev/null || true

    # Ports to whitelist: registry (5000), PostgreSQL (5432), Redis (6379), RabbitMQ (5672)
    _infra_ports="5000 5432 6379 5672"

    # Flush any previous infrastructure port rules (idempotent re-runs)
    for _port in $_infra_ports; do
        (
            iptables -L DOCKER-USER --line-numbers -n 2>/dev/null | \
                grep "dpt:${_port}" | awk '{print $1}' | sort -rn | \
                while read -r num; do iptables -D DOCKER-USER "$num" 2>/dev/null || true
            done
        ) || true
    done

    for _port in $_infra_ports; do
        # Allow localhost (container-to-container on the same host)
        iptables -I DOCKER-USER -i lo -p tcp --dport "$_port" -j ACCEPT 2>/dev/null || true

        # Allow Docker bridge networks (172.16.0.0/12 covers docker0 + compose nets)
        iptables -I DOCKER-USER -s 172.16.0.0/12 -p tcp --dport "$_port" -j ACCEPT 2>/dev/null || true

        # Allow WireGuard mesh (10.100.0.0/24 is the assigned mesh range)
        iptables -I DOCKER-USER -s 10.100.0.0/24 -p tcp --dport "$_port" -j ACCEPT 2>/dev/null || true

        # Allow known node IPs
        if [ -n "${MASTER_MESH_IP:-}" ]; then
            iptables -I DOCKER-USER -s "${MASTER_MESH_IP}" -p tcp --dport "$_port" -j ACCEPT 2>/dev/null || true
        fi

        # Drop everything else to this port
        iptables -A DOCKER-USER -p tcp --dport "$_port" -j DROP 2>/dev/null || true
    done

    # Return to the DOCKER chain for all other traffic
    iptables -C DOCKER-USER -j RETURN 2>/dev/null || \
        iptables -A DOCKER-USER -j RETURN 2>/dev/null || true

    echo -e "${GREEN}  ✓ Infrastructure ports hardened (5000, 5432, 6379, 5672) — locked to localhost + mesh + docker networks${NC}"

    # Allow remote Promtail → Loki on WireGuard interface (VPN mesh)
    iptables -A INPUT -i wg+ -p tcp --dport 3100 -j ACCEPT 2>/dev/null || true

    # Persist iptables rules across reboots
    if command -v iptables-save >/dev/null 2>&1; then
        mkdir -p /etc/iptables
        iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
        # Create a systemd service to restore rules on boot (before Docker starts)
        if [ ! -f /etc/systemd/system/iptables-restore.service ]; then
            cat > /etc/systemd/system/iptables-restore.service <<'RESTORE_EOF'
[Unit]
Description=Restore iptables rules
Before=docker.service
After=network-pre.target

[Service]
Type=oneshot
ExecStart=/sbin/iptables-restore /etc/iptables/rules.v4
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
RESTORE_EOF
            systemctl daemon-reload 2>/dev/null || true
            systemctl enable iptables-restore 2>/dev/null || true
        fi
        echo -e "${GREEN}  ✓ iptables rules saved to /etc/iptables/rules.v4 for persistence${NC}"
    fi
fi

echo -e "${GREEN}  ✓ System security hardening complete${NC}"
    set_checkpoint "memory_hardened"
fi

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
AGENT_SERVICES="$(docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null || true)"
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
RUNNING_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --status running -q 2>/dev/null | wc -l)
TOTAL_COUNT=$(docker compose -f "$COMPOSE_FILE" ps -q 2>/dev/null | wc -l)
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
    BACKEND_STATUS="$(docker compose -f "$COMPOSE_FILE" ps backend --format "{{.Status}}" 2>/dev/null || true)"
    if docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS http://127.0.0.1:8000/health/live >/dev/null 2>&1; then
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
    docker compose -f "$COMPOSE_FILE" logs --tail=80 backend 2>/dev/null || true
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
    if docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health/live >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    elif curl -sfL --max-time 5 http://127.0.0.1:8090/health/live >/dev/null 2>&1; then
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
    docker compose -f "$COMPOSE_FILE" exec -T backend curl -fsS --max-time 5 http://127.0.0.1:8000/health/ready >/dev/null 2>&1 && READY_OK=true
    if ! $READY_OK && ! curl -sfL --max-time 5 http://127.0.0.1:8090/health/ready >/dev/null 2>&1; then
        echo -e "${YELLOW}  ⚠ Readiness endpoint is still warming; continuing because liveness passed.${NC}"
    fi
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Health check failed after $MAX_ATTEMPTS attempts.${NC}"
    dump_diagnostic_logs
fi

# ─── Check 3: All containers running ──────────────────────────────────────
echo -e "${BLUE}  → [2/4] Checking container status...${NC}"
RUNNING_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --status running -q 2>/dev/null | wc -l)
TOTAL_COUNT=$(docker compose -f "$COMPOSE_FILE" ps -q 2>/dev/null | wc -l)
UNHEALTHY_STATUS="$(docker compose -f "$COMPOSE_FILE" ps --format "{{.Service}}\t{{.Status}}" 2>/dev/null | awk 'tolower($0) ~ /unhealthy/ {print}' || true)"
# Also surface containers stuck in Docker's restart loop. These are not
# "unhealthy" (healthcheck hasn't run yet) but they're crash-looping,
# which is the more dangerous failure mode — print them first so the
# tail of their crash log is visible.
RESTARTING_STATUS="$(docker compose -f "$COMPOSE_FILE" ps --format "{{.Service}}\t{{.Status}}" 2>/dev/null | awk 'tolower($0) ~ /restarting/ {print}' || true)"
if [ -n "$RESTARTING_STATUS" ]; then
    echo -e "${RED}  ✗ One or more containers are crash-looping:${NC}"
    printf '%s\n' "$RESTARTING_STATUS" | sed 's/^/     - /'
    RESTARTING_SERVICES="$(printf '%s\n' "$RESTARTING_STATUS" | awk '{print $1}' | xargs 2>/dev/null || true)"
    if [ -n "$RESTARTING_SERVICES" ]; then
        echo -e "${YELLOW}  ↳ Crash tail of each restarting service (last 40 lines):${NC}"
        for _svc in $RESTARTING_SERVICES; do
            echo -e "${YELLOW}      --- $_svc ---${NC}"
            docker compose -f "$COMPOSE_FILE" logs --tail=40 "$_svc" 2>/dev/null | sed 's/^/        /' || true
        done
    fi
fi
if [ -n "$UNHEALTHY_STATUS" ]; then
    echo -e "${RED}  ✗ One or more containers are unhealthy:${NC}"
    printf '%s\n' "$UNHEALTHY_STATUS" | sed 's/^/     - /'
    UNHEALTHY_SERVICES="$(printf '%s\n' "$UNHEALTHY_STATUS" | awk '{print $1}' | xargs 2>/dev/null || true)"
    if [ -n "$UNHEALTHY_SERVICES" ]; then
        docker compose -f "$COMPOSE_FILE" logs --tail=80 $UNHEALTHY_SERVICES 2>/dev/null || true
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
    if docker inspect -f '{{.State.Running}}' "$caddy_container" 2>/dev/null | grep -q "true"; then
        echo -e "${GREEN}  ✓ Caddy reverse proxy container active${NC}"
        VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Caddy container is not running${NC}"
    fi
else
    echo -e "${BLUE}  → [4/4] Checking Traefik...${NC}"
    TRAEFIK_CHECK_URL="http://127.0.0.1:8081/"
    if is_node_mode; then
        TRAEFIK_CHECK_URL="http://127.0.0.1/health/live"
    fi
    traefik_container="$(resolve_container_target "smsly-hosting-traefik-1")"
    if docker inspect -f '{{.State.Running}}' "$traefik_container" 2>/dev/null | grep -q "true" \
       && curl -fsS --max-time 5 "$TRAEFIK_CHECK_URL" >/dev/null 2>&1; then
        echo -e "${GREEN}  ✓ Traefik edge proxy active (${TRAEFIK_CHECK_URL})${NC}"
        VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Traefik edge proxy check failed (${TRAEFIK_CHECK_URL})${NC}"
    fi
fi
fi

# Show container status
echo -e "\n${BLUE}Container Status:${NC}"
docker compose -f "$COMPOSE_FILE" ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || \
    docker compose -f "$COMPOSE_FILE" ps 2>/dev/null || true

echo -e "\n${BLUE}Verification Score: $VERIFY_PASS_COUNT/$VERIFY_TOTAL${NC}"

# ─── Install Autoscaler as systemd service ──────────────────────────────────
echo -e "${BLUE}  → Installing smsly-autoscaler systemd service...${NC}"
cp "$INSTALL_DIR/scripts/smsly-autoscaler.py" /opt/smsly/autoscaler.py 2>/dev/null || {
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
systemctl enable smsly-autoscaler 2>/dev/null || true
systemctl restart smsly-autoscaler 2>/dev/null || true
echo -e "${GREEN}  ✓ smsly-autoscaler service installed and started${NC}"

# Install infrastructure monitor
if [ -f "$INSTALL_DIR/scripts/monitor_infra.sh" ]; then
    echo -e "${BLUE}  → Installing critical infrastructure monitoring timer...${NC}"
    chmod +x "$INSTALL_DIR/scripts/monitor_infra.sh"
    cp "$INSTALL_DIR/scripts/smsly-infra-monitor.service" /etc/systemd/system/smsly-infra-monitor.service 2>/dev/null || true
    cp "$INSTALL_DIR/scripts/smsly-infra-monitor.timer" /etc/systemd/system/smsly-infra-monitor.timer 2>/dev/null || true
    systemctl daemon-reload
    systemctl enable smsly-infra-monitor.timer 2>/dev/null || true
    systemctl restart smsly-infra-monitor.timer 2>/dev/null || true
    echo -e "${GREEN}  ✓ smsly-infra-monitor timer installed and started${NC}"
fi

# Install platform update watcher and caddy watcher services
if [ -f "$INSTALL_DIR/scripts/smsly-update-watcher.service" ]; then
    echo -e "${BLUE}  → Installing platform update and Caddy config watcher services...${NC}"
    chmod +x "$INSTALL_DIR/scripts/platform-update.sh" "$INSTALL_DIR/scripts/caddy-reload.sh" 2>/dev/null || true
    cp "$INSTALL_DIR/scripts/smsly-update-watcher.service" /etc/systemd/system/smsly-update-watcher.service 2>/dev/null || true
    cp "$INSTALL_DIR/scripts/caddy-watcher.service" /etc/systemd/system/caddy-watcher.service 2>/dev/null || true
    systemctl daemon-reload
    systemctl enable smsly-update-watcher caddy-watcher 2>/dev/null || true
    systemctl restart smsly-update-watcher caddy-watcher 2>/dev/null || true
    echo -e "${GREEN}  ✓ smsly-update-watcher and caddy-watcher services installed and started${NC}"
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
    pip3 install -q --break-system-packages "$INSTALL_DIR/cli" 2>/dev/null || \
        pip3 install -q "$INSTALL_DIR/cli" 2>/dev/null || true

    # Ensure binary is in path (pip usually puts it in /usr/local/bin)
    if command -v smsly &> /dev/null; then
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
if docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py help diagnose_nodes >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" exec -T backend python manage.py diagnose_nodes --fix || true
    echo -e "${GREEN}  ✓ Node registered as Primary (if Master) and API tokens verified${NC}"
else
    echo -e "${YELLOW}  ⚠ diagnose_nodes command not available in this version; skipping.${NC}"
fi

# ─── Final Verification Sync ──────────────────────────────────────────────────
fi

if [ "$MODE_AGENT_LITE" != "true" ] && command -v smsly &> /dev/null; then
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
chmod +x scripts/grid-handshake.sh 2>/dev/null || true
bash scripts/grid-handshake.sh || \
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
        chmod +x "$_replica_script" 2>/dev/null || true
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
if command -v harden_security_verify >/dev/null 2>&1; then
    harden_security_verify
fi

exit 0