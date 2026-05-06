# =============================================================================
# FRESH INSTALL — Full setup from scratch
# =============================================================================

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
    if ! git fetch origin "$SMSLY_BRANCH" >/dev/null 2>&1 || ! git reset --hard "origin/$SMSLY_BRANCH" >/dev/null 2>&1; then
        echo -e "${YELLOW}  ⚠️ Git update failed. If this is a private repo, ensure your token is valid.${NC}"
    fi
else
    echo -e "${BLUE}  → Cloning repository ($SMSLY_BRANCH)...${NC}"
    CLONE_SUCCESS=false
    if [ -d "$INSTALL_DIR" ] && [ "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
        echo -e "${YELLOW}  → Destination not empty. Initializing git...${NC}"
        cd "$INSTALL_DIR"
        git init -q
        git remote add origin "$SMSLY_GIT_REMOTE"
        if git fetch origin "$SMSLY_BRANCH" -q >/dev/null 2>&1 && git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" >/dev/null 2>&1; then
            git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
            CLONE_SUCCESS=true
        fi
    else
        if git clone -b "$SMSLY_BRANCH" "$SMSLY_GIT_REMOTE" "$INSTALL_DIR"; then
            CLONE_SUCCESS=true
        fi
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

# Stop conflicting services if present (anything that holds port 80/443)
# NOTE: Don't stop Caddy here — we install/configure it in step 7.
# Stopping it on re-installs breaks the reverse proxy unnecessarily.
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

apt-get update -qq
apt-get install -y curl wget git python3 python3-pip python3-venv openssl ca-certificates gnupg lsb-release dnsutils

# Install Docker if missing
if ! command -v docker &> /dev/null; then
    echo -e "${BLUE}  → Installing Docker...${NC}"
    mkdir -m 0755 -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg --yes
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update -qq
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
    echo -e "${GREEN}  ✓ Docker already installed ($(docker --version | head -c 40))${NC}"
fi

# Ensure docker compose is available
if ! docker compose version >/dev/null 2>&1; then
    echo -e "${BLUE}  → Installing Docker Compose plugin...${NC}"
    apt-get install -y docker-compose-plugin || true
fi

# Apply mirror config if applicable (Only if docker is now present)
if command -v docker &> /dev/null; then
    configure_docker_mirror
fi

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
             if [ -n "${SMSLY_GIT_REMOTE:-}" ]; then
                 git remote set-url origin "$SMSLY_GIT_REMOTE" 2>/dev/null || true
             fi
             git fetch origin "$SMSLY_BRANCH" >/dev/null 2>&1 || true
             git checkout -B "$SMSLY_BRANCH" "origin/$SMSLY_BRANCH" >/dev/null 2>&1 || true
             git branch --set-upstream-to="origin/$SMSLY_BRANCH" "$SMSLY_BRANCH" >/dev/null 2>&1 || true
