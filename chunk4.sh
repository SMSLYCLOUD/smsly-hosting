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
    set_checkpoint "admin_created"
fi

# -----------------------------------------------------------------------------
# 7. Caddy Reverse Proxy (Public Access)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "caddy_configured"; then
    echo -e "\n${YELLOW}[7/9] Setting up Caddy Reverse Proxy...${NC}"

if [ "$RUST_TWIN_MODE" = "true" ]; then
    echo -e "${BLUE}  → Formatting Rust Twin Caddyfile...${NC}"
    cd rust_twin && export DOMAIN && export ACME_EMAIL && caddy fmt --overwrite Caddyfile 2>/dev/null || true
    cd ..
    # Swap the default Caddyfile path to point to the Rust Twin version
    cp rust_twin/Caddyfile /etc/caddy/Caddyfile 2>/dev/null || true
fi

# ─── Build Caddy with Cloudflare DNS plugin ───────────────────────────────────
# Always build custom Caddy with Cloudflare DNS support, even in IP mode.
# This ensures users can enable SSL + wildcard from the web UI later without SSH.
if caddy list-modules 2>/dev/null | grep -q 'dns.providers.cloudflare'; then
    echo -e "${GREEN}  ✓ Caddy already has cloudflare DNS module${NC}"
elif command -v caddy &> /dev/null; then
    echo -e "${BLUE}  → Caddy found but missing Cloudflare DNS plugin — rebuilding...${NC}"
    _BUILD_CADDY=true
else
    echo -e "${BLUE}  → Installing Caddy with Cloudflare DNS plugin...${NC}"
    _BUILD_CADDY=true
fi

if [ "${_BUILD_CADDY:-}" = "true" ]; then
    if ! command -v xcaddy &> /dev/null; then
        # xcaddy needs Go 1.21+. Ubuntu apt repos ship Go 1.18 which is
        # too old (go.mod 'toolchain' directive is unsupported). Use snap
        # or direct binary download to get a compatible version.
        _GO_OK=false
        if command -v go &> /dev/null; then
            _GO_VER=$(go version | grep -oP 'go1\.(\d+)' | grep -oP '\d+$')
            [ "${_GO_VER:-0}" -ge 21 ] && _GO_OK=true
        fi
        if [ "$_GO_OK" != "true" ]; then
            echo -e "${BLUE}  → Installing Go 1.22 (xcaddy requires Go 1.21+)...${NC}"
            GO_TAR="go1.22.10.linux-amd64.tar.gz"
            curl -fsSL "https://go.dev/dl/$GO_TAR" -o "/tmp/$GO_TAR"
            rm -rf /usr/local/go
            tar -C /usr/local -xzf "/tmp/$GO_TAR"
            rm -f "/tmp/$GO_TAR"
            export PATH="/usr/local/go/bin:$PATH"
            echo -e "${GREEN}  ✓ Go $(go version | awk '{print $3}') installed${NC}"
        fi
        export GOPATH="${GOPATH:-/root/go}"
        export PATH="$PATH:$GOPATH/bin"
        go install github.com/caddyserver/xcaddy/cmd/xcaddy@latest
    fi

    # Build custom Caddy with Cloudflare DNS
    CADDY_TMP=$(mktemp -d)
    cd "$CADDY_TMP"
    if xcaddy build --with github.com/caddy-dns/cloudflare 2>&1 | tail -5; then
        # Replace system Caddy
        systemctl stop caddy 2>/dev/null || true
        mv ./caddy /usr/bin/caddy
        chmod +x /usr/bin/caddy
        echo -e "${GREEN}  ✓ Custom Caddy built with Cloudflare DNS plugin${NC}"
    else
        echo -e "${YELLOW}  ⚠ Custom Caddy build failed — trying pre-built download...${NC}"
        # Fallback 1: Download pre-built Caddy with Cloudflare DNS from Caddy's download API
        if curl -fsSL -o /usr/bin/caddy \
            "https://caddyserver.com/api/download?os=linux&arch=amd64&p=github.com/caddy-dns/cloudflare" 2>/dev/null; then
            chmod +x /usr/bin/caddy
            echo -e "${GREEN}  ✓ Pre-built Caddy with Cloudflare DNS downloaded${NC}"
        elif ! command -v caddy &> /dev/null; then
            # Fallback 2: Install stock Caddy from apt (no wildcard SSL, but basic HTTPS works)
            echo -e "${YELLOW}  ⚠ Download also failed — installing stock Caddy (no wildcard SSL)...${NC}"
            apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null 2>&1
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null
            curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
            apt-get update >/dev/null 2>&1
            apt-get install -y caddy >/dev/null 2>&1
        fi
    fi
    cd "$INSTALL_DIR"
    rm -rf "$CADDY_TMP"
fi

if ! systemctl list-unit-files caddy.service >/dev/null 2>&1; then
    echo -e "${BLUE}  → Installing Caddy systemd service...${NC}"
    # Add a dedicated caddy user and group
    groupadd --system caddy 2>/dev/null || true
    useradd --system --gid caddy --create-home --home-dir /var/lib/caddy \
        --shell /usr/sbin/nologin --comment "Caddy web server" caddy 2>/dev/null || true

    cat > /etc/systemd/system/caddy.service <<'CADDYSRV'
[Unit]
Description=Caddy
Documentation=https://caddyserver.com/docs/
After=network.target network-online.target
Requires=network-online.target

[Service]
Type=exec
User=caddy
Group=caddy
ExecStart=/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
LimitNPROC=512
PrivateTmp=true
ProtectSystem=full
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
CADDYSRV
    systemctl daemon-reload
    echo -e "${GREEN}  ✓ Caddy systemd service installed${NC}"
fi

# ─── Configure Caddyfile ──────────────────────────────────────────────────────
echo -e "${BLUE}  → Configuring Caddyfile...${NC}"
mkdir -p /var/log/caddy
mkdir -p /etc/caddy
touch /var/log/caddy/access.log
if id caddy >/dev/null 2>&1; then
    chown -R caddy:caddy /var/log/caddy
fi
chmod 755 /var/log/caddy
chmod 640 /var/log/caddy/access.log

CADDY_OVERRIDE_DIR="/etc/systemd/system/caddy.service.d"
CADDY_OVERRIDE_FILE="$CADDY_OVERRIDE_DIR/override.conf"

if [ "$USE_SSL" = "true" ] && [ -n "$DOMAIN" ] && [ "$DOMAIN" != "$PUBLIC_IP" ]; then
    # Ensure token is sourced from .env if present (idempotent runs)
    if [ -z "$CLOUDFLARE_API_TOKEN" ] && [ -f "$INSTALL_DIR/.env" ]; then
        CLOUDFLARE_API_TOKEN="$(grep -m1 '^CLOUDFLARE_API_TOKEN=' "$INSTALL_DIR/.env" 2>/dev/null | cut -d= -f2- || true)"
    fi

    if [ "$WILDCARD_SUBDOMAINS" = "true" ] && [ -n "$CLOUDFLARE_API_TOKEN" ]; then
        # ─── Full wildcard mode: domain + *.domain with Cloudflare DNS ────
        cat > /etc/caddy/Caddyfile <<CADDYEOF
# CloudNeuron Reverse Proxy — Auto-generated
# Domain: $DOMAIN → HTTPS (auto Let's Encrypt)
# Wildcard: *.$DOMAIN → HTTPS (Cloudflare DNS challenge)

$DOMAIN {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

*.$DOMAIN {
    tls {
        dns cloudflare {env.CLOUDFLARE_API_TOKEN}
    }
    handle {
        rewrite * /notice
        reverse_proxy localhost:8090
    }
}

:80 {
    handle {
        rewrite * /notice
        reverse_proxy localhost:8090
    }
}
CADDYEOF

        # Set Cloudflare token in systemd environment
        mkdir -p "$CADDY_OVERRIDE_DIR"
        cat > "$CADDY_OVERRIDE_FILE" <<ENVEOF
[Service]
ExecStart=
ExecStart=/usr/bin/caddy run --config /etc/caddy/Caddyfile
Environment="CLOUDFLARE_API_TOKEN=$CLOUDFLARE_API_TOKEN"
ENVEOF
        chmod 600 "$CADDY_OVERRIDE_FILE"
        systemctl daemon-reload

        echo -e "${GREEN}  ✓ Caddy configured: HTTPS ($DOMAIN) + Wildcard (*.$DOMAIN) + HTTP fallback → 8090${NC}"
    else
        # ─── Standard SSL (no wildcard) ──────────────────────────────────
        cat > /etc/caddy/Caddyfile.tmp <<CADDYEOF
# CloudNeuron Reverse Proxy — Auto-generated
# Domain: $DOMAIN → HTTPS (auto Let's Encrypt)

$DOMAIN {
    reverse_proxy localhost:8090
    encode gzip
    log {
        output file /var/log/caddy/access.log
    }
}

:80 {
    handle {
        rewrite * /notice
        reverse_proxy localhost:8090
    }
}
CADDYEOF
        if [ -f "$CADDY_OVERRIDE_FILE" ]; then
            rm -f "$CADDY_OVERRIDE_FILE"
            rmdir "$CADDY_OVERRIDE_DIR" 2>/dev/null || true
            systemctl daemon-reload
        fi
        mv /etc/caddy/Caddyfile.tmp /etc/caddy/Caddyfile
        echo -e "${GREEN}  ✓ Caddy configured: HTTPS ($DOMAIN) + HTTP (:80 fallback) → 8090${NC}"
    fi
else
    cat > /etc/caddy/Caddyfile.tmp <<CADDYEOF
# CloudNeuron Reverse Proxy — Auto-generated
:80 {
    reverse_proxy localhost:8090
}
CADDYEOF
    if [ -f "$CADDY_OVERRIDE_FILE" ]; then
        rm -f "$CADDY_OVERRIDE_FILE"
        rmdir "$CADDY_OVERRIDE_DIR" 2>/dev/null || true
        systemctl daemon-reload
    fi
    mv /etc/caddy/Caddyfile.tmp /etc/caddy/Caddyfile
    echo -e "${GREEN}  ✓ Caddy configured for HTTP: :80 → 8090${NC}"
fi

# ─── Create caddy-config volume directory for Settings UI writes ──────────────
ensure_infrastructure_permissions

# ─── Install caddy-watcher service (picks up UI-driven Caddyfile changes) ─────
if [ -f "$INSTALL_DIR/scripts/caddy-reload.sh" ]; then
    chmod +x "$INSTALL_DIR/scripts/caddy-reload.sh"
    cat > /etc/systemd/system/caddy-watcher.service <<WATCHEREOF
[Unit]
Description=Caddy Config Watcher (SMSLY)
After=caddy.service

[Service]
Type=simple
ExecStart=$INSTALL_DIR/scripts/caddy-reload.sh /opt/smsly-hosting/caddy-config
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
WATCHEREOF
    systemctl daemon-reload
    systemctl enable caddy-watcher >/dev/null 2>&1
    systemctl restart caddy-watcher
    echo -e "${GREEN}  ✓ Caddy watcher service installed and running${NC}"
fi

# ─── Install update-watcher service (picks up UI-driven platform updates) ─────
if [ -f "$INSTALL_DIR/scripts/platform-update.sh" ]; then
    chmod +x "$INSTALL_DIR/scripts/platform-update.sh"
    cat > /etc/systemd/system/smsly-update-watcher.service <<UPDATEWATCHEREOF
[Unit]
Description=Platform Update Watcher (SMSLY)
After=network.target

[Service]
Type=simple
ExecStart=$INSTALL_DIR/scripts/platform-update.sh /opt/smsly-hosting/caddy-config
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UPDATEWATCHEREOF
    systemctl daemon-reload
    systemctl enable smsly-update-watcher >/dev/null 2>&1
    systemctl restart smsly-update-watcher
    echo -e "${GREEN}  ✓ Platform update watcher service installed and running${NC}"
fi

# Kill non-Caddy/non-Docker processes holding port 80/443 before Caddy binds
for port in 80 443; do
    PID=$(lsof -ti :$port 2>/dev/null || ss -tlnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' || true)
    if [ -n "$PID" ] && [ "$PID" != "0" ]; then
        PNAME=$(ps -p "$PID" -o comm= 2>/dev/null || echo "unknown")
        # Don't kill Caddy or Docker processes
        if [[ "$PNAME" != "caddy" ]] && [[ "$PNAME" != "docker"* ]]; then
            echo -e "${YELLOW}  → Killing $PNAME (PID: $PID) holding port $port${NC}"
            kill -9 $PID 2>/dev/null || true
            sleep 1
        fi
    fi
done

if systemctl is-active --quiet caddy; then
    systemctl reload caddy
else
    systemctl restart caddy
fi
systemctl enable caddy >/dev/null 2>&1

# Verify Caddy is running
sleep 2
if systemctl is-active --quiet caddy; then
    echo -e "${GREEN}  ✓ Caddy reverse proxy active${NC}"
    fi
    
    safe_refresh_runtime_services
    set_checkpoint "caddy_configured"
fi

# -----------------------------------------------------------------------------
# 8. System Memory Hardening (Prevents OOM kills)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "memory_hardened"; then
    echo -e "\n${YELLOW}[8/9] Hardening System Memory...${NC}"

# ─── Swap: Ensure swap is at least 2x RAM ────────────────────────────────────
CURRENT_RAM_MB=$(free -m | awk '/^Mem:/{print $2}')
TARGET_SWAP_MB=$((CURRENT_RAM_MB * 2))
CURRENT_SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')

if [ "$CURRENT_SWAP_MB" -lt "$TARGET_SWAP_MB" ]; then
    NEEDED_MB=$((TARGET_SWAP_MB - CURRENT_SWAP_MB))
    echo -e "${BLUE}  → Current swap: ${CURRENT_SWAP_MB}MB. Adding ${NEEDED_MB}MB to reach 2x RAM (${TARGET_SWAP_MB}MB)...${NC}"
    SWAPFILE="/swapfile-smsly"

    # If the file already exists but is too small, we need to recreate it
    if [ -f "$SWAPFILE" ]; then
        swapoff "$SWAPFILE" 2>/dev/null || true
        rm -f "$SWAPFILE"
        # Since we removed the old file, we need to create the full target amount
        NEEDED_MB=$TARGET_SWAP_MB
    fi

    fallocate -l ${NEEDED_MB}M "$SWAPFILE" 2>/dev/null || dd if=/dev/zero of="$SWAPFILE" bs=1M count=$NEEDED_MB status=none
    chmod 600 "$SWAPFILE"
    mkswap "$SWAPFILE" >/dev/null 2>&1
    swapon "$SWAPFILE" 2>/dev/null || true
    # Make permanent (idempotent)
    if ! grep -q "$SWAPFILE" /etc/fstab 2>/dev/null; then
        echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab
    fi
    echo -e "${GREEN}  ✓ Swap file created and activated (${NEEDED_MB}MB)${NC}"
else
    echo -e "${GREEN}  ✓ Swap already sufficient (${CURRENT_SWAP_MB}MB, >= 2x RAM)${NC}"
fi

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
for CONTAINER in smsly-hosting-nginx-1 smsly-hosting-backend-1 smsly-hosting-db-1 smsly-hosting-pgcat-1; do
    CPID=$(docker inspect --format '{{.State.Pid}}' "$CONTAINER" 2>/dev/null || echo "")
    if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
        echo -500 > "/proc/$CPID/oom_score_adj" 2>/dev/null || true
    fi
done
echo -e "${GREEN}  ✓ OOM protection set (nginx, backend, db, pgcat)${NC}"

# ─── Firewall Hardening (UFW) ────────────────────────────────────────────────
if command -v ufw >/dev/null 2>&1; then
    echo -e "${BLUE}  → Configuring UFW firewall...${NC}"
    ufw default deny incoming >/dev/null 2>&1 || true
    ufw default allow outgoing >/dev/null 2>&1 || true
    ufw allow ssh >/dev/null 2>&1 || true
    ufw allow 80/tcp >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    # Allow FRP if active
    if [ -f "$INSTALL_DIR/.env" ] && grep -q "FRP_AUTH_TOKEN" "$INSTALL_DIR/.env"; then
        ufw allow 7000/tcp >/dev/null 2>&1 || true
    fi
    # Allow Docker Mirror (Option B) if this is the Master/Leader
    if [ -z "${MASTER_IP:-}" ] || [ "$MASTER_IP" = "127.0.0.1" ] || [ "$MASTER_IP" = "$(detect_public_ip)" ]; then
        ufw allow 5001/tcp >/dev/null 2>&1 || true
    fi
    echo "y" | ufw enable >/dev/null 2>&1 || true
    echo -e "${GREEN}  ✓ Firewall hardened (Inbound blocked, SSH/Web permitted)${NC}"
fi

echo -e "${GREEN}  ✓ System security hardening complete${NC}"
    set_checkpoint "memory_hardened"
fi

# -----------------------------------------------------------------------------
# 9. Verification
# -----------------------------------------------------------------------------
echo -e "\n${YELLOW}[9/9] Verifying Deployment...${NC}"
VERIFY_PASS_COUNT=0
VERIFY_TOTAL=5
sleep 5

# ─── Check 1: Verify nginx loaded custom config (not default) ──────────────
echo -e "${BLUE}  → [1/5] Verifying nginx configuration...${NC}"
NGINX_CONFIG_CHECK=$(docker exec smsly-hosting-nginx-1 head -1 /etc/nginx/nginx.conf 2>/dev/null || echo "FAIL")
if echo "$NGINX_CONFIG_CHECK" | grep -q "events"; then
    echo -e "${GREEN}  ✓ Nginx config verified (custom proxy config loaded)${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  ⚠ Nginx may have default config — force-recreating...${NC}"
    docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps nginx
    docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true
    sleep 3
    NGINX_CONFIG_CHECK=$(docker exec smsly-hosting-nginx-1 head -1 /etc/nginx/nginx.conf 2>/dev/null || echo "FAIL")
    if echo "$NGINX_CONFIG_CHECK" | grep -q "events"; then
        echo -e "${GREEN}  ✓ Nginx config fixed after force-recreate${NC}"
        VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    else
        echo -e "${RED}  ✗ Nginx config still incorrect. Manual fix needed.${NC}"
    fi
fi

# ─── Check 2: Health check ─────────────────────────────────────────────────
echo -e "${BLUE}  → [2/5] Running health check...${NC}"
HEALTH_OK=false
# ZH-012 HARDENING: Increased from 12 (1m) to 36 attempts (3m) for slow VPS I/O
MAX_ATTEMPTS=36
for attempt in $(seq 1 $MAX_ATTEMPTS); do
    if curl -sfL http://127.0.0.1/health >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    fi
    echo -ne "\r${YELLOW}  → Health check attempt $attempt/$MAX_ATTEMPTS — waiting...${NC}"
    if [ "$attempt" -eq 5 ]; then
        echo -e "\n${BLUE}  → Restarting Nginx to ensure upstream binding...${NC}"
        docker compose -f "$COMPOSE_FILE" restart nginx >/dev/null 2>&1 || true
    fi
    sleep 5
done
echo ""

if [ "$HEALTH_OK" = "true" ]; then
    echo -e "${GREEN}  ✓ Health Check Passed!${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Health check failed after $MAX_ATTEMPTS attempts.${NC}"
    dump_diagnostic_logs
fi

# ─── Check 3: All containers running ──────────────────────────────────────
echo -e "${BLUE}  → [3/5] Checking container status...${NC}"
RUNNING_COUNT=$(docker compose -f "$COMPOSE_FILE" ps --status running -q 2>/dev/null | wc -l)
TOTAL_COUNT=$(docker compose -f "$COMPOSE_FILE" ps -q 2>/dev/null | wc -l)
if [ "$RUNNING_COUNT" -eq "$TOTAL_COUNT" ] && [ "$TOTAL_COUNT" -gt 0 ]; then
    echo -e "${GREEN}  ✓ All $TOTAL_COUNT containers running${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Only $RUNNING_COUNT/$TOTAL_COUNT containers running${NC}"
fi

# ─── Check 4: Swap is sufficient ──────────────────────────────────────────
echo -e "${BLUE}  → [4/5] Checking swap...${NC}"
SWAP_TOTAL=$(free -m | awk '/^Swap:/{print $2}')
if [ "$SWAP_TOTAL" -ge 1500 ]; then
    echo -e "${GREEN}  ✓ Swap sufficient (${SWAP_TOTAL}MB)${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${YELLOW}  ⚠ Swap low (${SWAP_TOTAL}MB) — recommend 2GB+${NC}"
fi

# ─── Check 5: Caddy running ───────────────────────────────────────────────
echo -e "${BLUE}  → [5/5] Checking Caddy...${NC}"
if systemctl is-active --quiet caddy 2>/dev/null; then
    echo -e "${GREEN}  ✓ Caddy reverse proxy active${NC}"
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
else
    echo -e "${RED}  ✗ Caddy is not running${NC}"
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

# -----------------------------------------------------------------------------
# 10. CLI Integration
# -----------------------------------------------------------------------------
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
        if [ -n "$DOMAIN" ] && [ "$DOMAIN" != "localhost" ]; then
            URL_SCHEME="https" && [ "$USE_SSL" != "true" ] && URL_SCHEME="http"
            API_URL="${URL_SCHEME}://${DOMAIN}"
        else
            API_URL="http://127.0.0.1:8090"
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

# ─── Final Verification Sync ──────────────────────────────────────────────────
if command -v smsly &> /dev/null; then
    VERIFY_PASS_COUNT=$((VERIFY_PASS_COUNT + 1))
    VERIFY_TOTAL=$((VERIFY_TOTAL + 1))
fi

# ─── Remove rollback trap (installation succeeded) ─────────────────────────
trap - EXIT

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
echo -e "\n${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   ✓ INSTALLATION SUCCESSFUL!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"

if [ "$USE_SSL" = "true" ]; then
    echo -e "   URL:         https://$DOMAIN"
else
    echo -e "   URL:         http://$PUBLIC_IP"
fi
echo -e "   Admin:       /admin"
echo -e "   Credentials: $CREDENTIALS_FILE"
echo -e "   Install Log: $LOG_FILE"
echo -e "   Location:    $INSTALL_DIR"
echo -e "   Memory:      $(free -m | awk '/^Mem:/{print $7}')MB available"
echo -e "   Swap:        $(free -m | awk '/^Swap:/{print $2}')MB total"
echo -e "   CLI:         'smsly services list'${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${YELLOW}  View credentials:   cat $CREDENTIALS_FILE${NC}"
echo -e "${YELLOW}  View logs:          cat $LOG_FILE${NC}"
echo -e "${YELLOW}  Update frontend:    sudo bash install.sh --update-frontend${NC}"
echo -e "${YELLOW}  Update backend:     sudo bash install.sh --update-backend${NC}"
echo -e "${YELLOW}  Full update:        sudo bash install.sh --update${NC}"
echo -e "${YELLOW}  Runtime refresh:    sudo bash install.sh --refresh${NC}"
echo -e "${YELLOW}  Runtime recovery:   sudo bash install.sh --recover${NC}"
echo -e "${YELLOW}  Debug snapshot:     sudo bash install.sh --debug${NC}"
echo -e "${YELLOW}  Wipe install:       sudo bash install.sh --wipe${NC}"

# ─── Conditional Auto-Reboot (only if ALL checks passed) ────────────────────
if [ "$VERIFY_PASS_COUNT" -eq "$VERIFY_TOTAL" ]; then
    echo -e "\n${GREEN}  ✓ All $VERIFY_TOTAL/$VERIFY_TOTAL verification checks passed.${NC}"
    # Normalize NON_INTERACTIVE to true/false for easier shell testing
    _IS_NON_INTERACTIVE=false
    if [[ "${NON_INTERACTIVE:-}" =~ ^(1|true|yes)$ ]]; then _IS_NON_INTERACTIVE=true; fi

    if [ -e /dev/tty ] && [ -z "${SKIP_REBOOT:-}" ] && [ "$_IS_NON_INTERACTIVE" != "true" ]; then
        echo -e "${YELLOW}  System will reboot in 30 seconds to apply sysctl changes.${NC}"
        echo -e "${YELLOW}  Press Ctrl+C to cancel, or wait...${NC}"
        for i in $(seq 30 -1 1); do
            printf "\r${YELLOW}  Rebooting in %2d seconds... ${NC}" "$i"
            sleep 1
        done
        echo -e "\n${BLUE}  → Rebooting now...${NC}"
        reboot
    else
        echo -e "${YELLOW}  Non-interactive mode — skipping auto-reboot.${NC}"
        echo -e "${YELLOW}  Run 'sudo reboot' manually to apply sysctl changes.${NC}"
    fi
else
    echo -e "\n${RED}  ⚠ Only $VERIFY_PASS_COUNT/$VERIFY_TOTAL checks passed — skipping auto-reboot.${NC}"
    echo -e "${YELLOW}  Fix the failed checks above, then run: sudo reboot${NC}"
    if [ "${SMSLY_STRICT_VERIFY:-0}" = "1" ]; then
        echo -e "${RED}  ✗ Strict verification is enabled; failing installation.${NC}"
        exit 1
    fi
fi
