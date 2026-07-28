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
if fallocate -l ${ADD_SWAP_MB}M "$NEW_SWAPFILE" ; then
    chmod 600 "$NEW_SWAPFILE"
    mkswap "$NEW_SWAPFILE" 
    swapon "$NEW_SWAPFILE"  || true

    # Make it permanent
    if ! grep -q "$NEW_SWAPFILE" /etc/fstab ; then
        echo "$NEW_SWAPFILE none swap sw 0 0" >> /etc/fstab
    fi

    log "Successfully added ${ADD_SWAP_MB}MB of swap. Total swap is now approx ${NEW_TOTAL_MB}MB."
else
    # Fallback to dd if fallocate fails (e.g. some filesystems don't support it)
    log "fallocate failed, trying dd..."
    if dd if=/dev/zero of="$NEW_SWAPFILE" bs=1M count=$ADD_SWAP_MB status=none; then
        chmod 600 "$NEW_SWAPFILE"
        mkswap "$NEW_SWAPFILE" 
        swapon "$NEW_SWAPFILE"  || true

        if ! grep -q "$NEW_SWAPFILE" /etc/fstab ; then
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
if ! grep -q "$OOM_SCRIPT" /etc/crontab ; then
    echo "$CRON_JOB" >> /etc/crontab
    echo -e "${GREEN}  ✓ OOM Auto-Adjuster installed and scheduled via cron${NC}"
else
    echo -e "${GREEN}  ✓ OOM Auto-Adjuster already scheduled${NC}"
fi

# ─── Sysctl tuning (idempotent) ──────────────────────────────────────────────
SYSCTL_UPDATED=false

ensure_sysctl() {
    local key="$1" value="$2" desc="$3"
    CURRENT=$(sysctl -n "$key"  || echo "")
    if [ "$CURRENT" != "$value" ]; then
        sysctl -w "$key=$value"  || true
        # Make permanent (idempotent)
        if grep -q "^$key" /etc/sysctl.conf ; then
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
    CRITICAL_CONTAINERS=(smsly-hosting-backend-1 smsly-postgres-primary smsly-hosting-pgcat-1 smsly-hosting-celery-1 smsly-hosting-celery-deploy-1 smsly-hosting-celery-fast-1 smsly-hosting-celery-beat-1 smsly-hosting-socket-proxy-1)
fi
for CONTAINER in "${CRITICAL_CONTAINERS[@]}"; do
    resolved_container="$(resolve_container_target "$CONTAINER")"
    CPID=$(docker inspect --format '{{.State.Pid}}' "$resolved_container"  || echo "")
    if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
        echo -500 > "/proc/$CPID/oom_score_adj"  || true
    fi
done
echo -e "${GREEN}  ✓ OOM protection set (${CRITICAL_CONTAINERS[*]})${NC}"

# ─── Firewall Hardening (UFW) ────────────────────────────────────────────────
if command -v ufw ; then
    echo -e "${BLUE}  → Configuring UFW firewall...${NC}"
    ufw default deny incoming  || true
    ufw default allow outgoing  || true
    # Allow SSH from master IP specifically (provisioning/updates)
    _master_ip="${MASTER_IP:-}"
    if [ -n "$_master_ip" ] && [ "$_master_ip" != "127.0.0.1" ] && ! echo "$_master_ip" | grep -qE '^(0\.0\.0\.0|localhost)$'; then
        echo -e "${BLUE}  → Allowing master ($_master_ip) SSH access...${NC}"
        ufw allow from "$_master_ip" to any port 22  || true
    fi
    # Fallback: allow SSH from any (in case MASTER_IP is empty)
    ufw allow ssh  || true
    
    if [ "${INSTALL_MODE:-}" = "agent-lite" ]; then
        if [ -n "$_master_ip" ] && [ "$_master_ip" != "127.0.0.1" ] && ! echo "$_master_ip" | grep -qE '^(0\.0\.0\.0|localhost)$'; then
            ufw allow from "$_master_ip" to any port 80  || true
        else
            echo -e "${YELLOW}  ⚠ Warning: Agent-Lite missing Master IP. Port 80 not exposed.${NC}"
        fi
    else
        ufw allow 80/tcp  || true
        ufw allow 443/tcp  || true
    fi
    # Allow FRP if active
    if [ -f "$INSTALL_DIR/.env" ] && grep -q "FRP_AUTH_TOKEN" "$INSTALL_DIR/.env"; then
        ufw allow 7000/tcp  || true
    fi
    # Allow Docker Mirror (Option B) if this is the Master/Leader
    if [ -z "${MASTER_IP:-}" ] || [ "$MASTER_IP" = "127.0.0.1" ] || [ "$MASTER_IP" = "$(detect_public_ip)" ]; then
        ufw allow 5001/tcp  || true
        # Allow Lite Agents to reach core services — RESTRICTED to WireGuard mesh only.
        # These ports carry database/cache/message-queue traffic and must never be
        # exposed to the public internet. Lite Agents connect via the WireGuard VPN
        # mesh (10.100.0.0/24), so we whitelist that subnet plus the master's own
        # mesh IP. Password auth is the second layer of defense.
        echo -e "${BLUE}  → Master node: Restricting DB/Redis/MQ ports to WireGuard mesh (10.100.0.0/24)${NC}"
        ufw allow from 10.100.0.0/24 to any port 5432 proto tcp  || true
        ufw allow from 10.100.0.0/24 to any port 6379 proto tcp  || true
        ufw allow from 10.100.0.0/24 to any port 5672 proto tcp  || true
        # Also allow localhost (container-to-container on the same host)
        ufw allow from 127.0.0.1 to any port 5432 proto tcp  || true
        ufw allow from 127.0.0.1 to any port 6379 proto tcp  || true
        ufw allow from 127.0.0.1 to any port 5672 proto tcp  || true
        # Allow Docker bridge networks (172.16.0.0/12) for container-to-host communication
        ufw allow from 172.16.0.0/12 to any port 5432 proto tcp  || true
        ufw allow from 172.16.0.0/12 to any port 6379 proto tcp  || true
        ufw allow from 172.16.0.0/12 to any port 5672 proto tcp  || true
    fi
    echo "y" | ufw enable  || true
    echo -e "${GREEN}  ✓ Firewall hardened (Inbound blocked, SSH/Web permitted)${NC}"
fi

# ── Infrastructure port firewall (DOCKER-USER chain) ────────────────────
# Docker bypasses UFW by inserting its own iptables rules in the DOCKER
# chain. The DOCKER-USER chain is the official way to add custom rules.
# We lock down all infrastructure ports (registry, DB, Redis, RabbitMQ)
# to trusted sources only: localhost, Docker bridges, and WireGuard mesh.
if command -v iptables ; then
    echo -e "${BLUE}  → Securing infrastructure ports via iptables (DOCKER-USER chain)...${NC}"

    # Ensure DOCKER-USER chain exists (Docker creates it, but be safe)
    iptables -N DOCKER-USER  || true

    # Ports to whitelist: registry (5000), PostgreSQL (5432), Redis (6379), RabbitMQ (5672)
    _infra_ports="5000 5432 6379 5672"

    # Flush any previous infrastructure port rules (idempotent re-runs)
    for _port in $_infra_ports; do
        (
            iptables -L DOCKER-USER --line-numbers -n  | \
                grep "dpt:${_port}" | awk '{print $1}' | sort -rn | \
                while read -r num; do iptables -D DOCKER-USER "$num"  || true
            done
        ) || true
    done

    for _port in $_infra_ports; do
        # Allow localhost (container-to-container on the same host)
        iptables -I DOCKER-USER -i lo -p tcp --dport "$_port" -j ACCEPT  || true

        # Allow Docker bridge networks (172.16.0.0/12 covers docker0 + compose nets)
        iptables -I DOCKER-USER -s 172.16.0.0/12 -p tcp --dport "$_port" -j ACCEPT  || true

        # Allow WireGuard mesh (10.100.0.0/24 is the assigned mesh range)
        iptables -I DOCKER-USER -s 10.100.0.0/24 -p tcp --dport "$_port" -j ACCEPT  || true

        # Allow known node IPs
        if [ -n "${MASTER_MESH_IP:-}" ]; then
            iptables -I DOCKER-USER -s "${MASTER_MESH_IP}" -p tcp --dport "$_port" -j ACCEPT  || true
        fi

        # Drop everything else to this port
        iptables -A DOCKER-USER -p tcp --dport "$_port" -j DROP  || true
    done

    # Return to the DOCKER chain for all other traffic
    iptables -C DOCKER-USER -j RETURN  || \
        iptables -A DOCKER-USER -j RETURN  || true

    echo -e "${GREEN}  ✓ Infrastructure ports hardened (5000, 5432, 6379, 5672) — locked to localhost + mesh + docker networks${NC}"

    # Allow remote Promtail → Loki on WireGuard interface (VPN mesh)
    iptables -A INPUT -i wg+ -p tcp --dport 3100 -j ACCEPT  || true

    # Persist iptables rules across reboots
    if command -v iptables-save ; then
        mkdir -p /etc/iptables
        iptables-save > /etc/iptables/rules.v4  || true
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
            systemctl daemon-reload  || true
            systemctl enable iptables-restore  || true
        fi
        echo -e "${GREEN}  ✓ iptables rules saved to /etc/iptables/rules.v4 for persistence${NC}"
    fi
fi

echo -e "${GREEN}  ✓ System security hardening complete${NC}"
    set_checkpoint "memory_hardened"
fi
