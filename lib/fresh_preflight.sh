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
    docker system prune -f  || true
    docker builder prune -f  || true
    DISK_AVAIL_MB=$(df -BM / | tail -1 | awk '{print $4}' | tr -d 'M')
    if [ "$DISK_AVAIL_MB" -lt 1500 ]; then
        echo -e "${RED}  ✗ Insufficient disk space (${DISK_AVAIL_MB}MB). Need at least 1.5GB for fresh install.${NC}"
        exit 1
    fi
    echo -e "${GREEN}  ✓ After cleanup: ${DISK_AVAIL_MB}MB available${NC}"
fi

# ─── Git Initialization & Sync ──────────────────────────────────────────────
SMSLY_BRANCH="${SMSLY_BRANCH:-master}"
SMSLY_GIT_REMOTE="${SMSLY_GIT_REMOTE:-https://github.com/SMSLYCLOUD/smsly-hosting.git}"

if [ -d "$INSTALL_DIR/.git" ]; then
    echo -e "${BLUE}  → Updating existing repository ($SMSLY_BRANCH)...${NC}"
    cd "$INSTALL_DIR"
    ensure_local_ignores
    if [ -n "$(git status --porcelain )" ]; then
        echo -e "${YELLOW}  ! Local changes detected - stashing before repository sync${NC}"
        git stash push --include-untracked -m "install-sync-$(date +%s)"  || true
    fi
    if ! git fetch origin "$SMSLY_BRANCH"  || ! git reset --hard "origin/$SMSLY_BRANCH" ; then
        echo -e "${RED}  ✗ Git update failed for $SMSLY_BRANCH. SSL verification is always enforced — check network or CA certificates.${NC}"
    fi
else
    echo -e "${BLUE}  → Cloning repository ($SMSLY_BRANCH)...${NC}"
    CLONE_SUCCESS=false
    if [ -f "$INSTALL_DIR/.env" ]; then
        echo -e "${YELLOW}  → Existing .env found — preserving configuration${NC}"
        cp "$INSTALL_DIR/.env" /tmp/smsly-env-backup  || true
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
            cp -rv "${SMSLY_INSTALL_WORKDIR}/"* "$INSTALL_DIR/"  || true
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
