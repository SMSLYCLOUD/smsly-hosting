# -----------------------------------------------------------------------------
# 8. System Memory Hardening (Prevents OOM kills)
# -----------------------------------------------------------------------------
if ! is_checkpoint_done "memory_hardened"; then
    echo -e "\n${YELLOW}[8/9] Hardening System Memory...${NC}"

# ─── Swap: Ensure swap is at least 4x RAM ────────────────────────────────────
ensure_system_swap

# ─── Auto-Maintenance: Install Bounded Emergency Swap Adjuster ───────────────
OOM_SCRIPT="/opt/smsly/scripts/oom-swap-adjuster.sh"
mkdir -p /opt/smsly/scripts
cat << 'EOF' > "$OOM_SCRIPT"
#!/usr/bin/env bash
# oom-swap-adjuster.sh
#
# Adds bounded emergency swap ONLY when the host itself is under real memory
# pressure (MemAvailable < 10% of MemTotal) or disk swap is critically low.
# Ignores isolated container cgroup OOM kills when the host has plenty of
# MemAvailable. Caps total disk swap at 4096MB (4GB) and max 2 swapfiles,
# using fixed slot names so /swapfile* can never stack infinitely.

set -euo pipefail

LOG_FILE="/var/log/smsly-oom-adjuster.log"
SWAPFILE_PREFIX="/swapfile-smsly-auto"
MAX_TOTAL_SWAP_MB=4096
MAX_SWAPFILES=2

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

MEM_TOTAL_KB=$(awk '/^MemTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
MEM_AVAIL_KB=$(awk '/^MemAvailable:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
if [ "${MEM_TOTAL_KB:-0}" -le 0 ]; then
    exit 0
fi

MEM_AVAIL_PCT=$((MEM_AVAIL_KB * 100 / MEM_TOTAL_KB))
SWAP_TOTAL_KB=$(awk '/^SwapTotal:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)
SWAP_FREE_KB=$(awk '/^SwapFree:/{print $2}' /proc/meminfo 2>/dev/null || echo 0)

SWAP_CRITICAL=0
if [ "${SWAP_TOTAL_KB:-0}" -gt 0 ]; then
    SWAP_FREE_PCT=$((SWAP_FREE_KB * 100 / SWAP_TOTAL_KB))
    if [ "$SWAP_FREE_PCT" -lt 10 ] && [ "$MEM_AVAIL_PCT" -lt 25 ]; then
        SWAP_CRITICAL=1
    fi
fi

# Only trigger on real host-wide memory pressure (MemAvailable < 10%) or
# critically low swap while MemAvailable < 25%.
if [ "$MEM_AVAIL_PCT" -ge 10 ] && [ "$SWAP_CRITICAL" -eq 0 ]; then
    exit 0
fi

RAM_MB=$((MEM_TOTAL_KB / 1024))
CURRENT_SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')

# Hard cap at 4GB (4096MB) total swap (or 2x RAM on <2GB hosts).
MAX_SWAP_MB=$((RAM_MB * 2))
[ "$MAX_SWAP_MB" -gt "$MAX_TOTAL_SWAP_MB" ] && MAX_SWAP_MB="$MAX_TOTAL_SWAP_MB"

if [ "${CURRENT_SWAP_MB:-0}" -ge "$MAX_SWAP_MB" ]; then
    exit 0
fi

# Cap at max 2 disk swapfiles total (excluding zram) and never infinitely stack /swapfile*
ACTIVE_DISK_SWAPS=$(awk 'NR>1 && $1 !~ /^\/dev\/zram/ {c++} END {print c+0}' /proc/swaps 2>/dev/null || echo 0)
EXISTING_SWAPFILES=$(find / -maxdepth 1 -name 'swapfile*' 2>/dev/null | wc -l || echo 0)
if [ "${ACTIVE_DISK_SWAPS:-0}" -ge "$MAX_SWAPFILES" ] || [ "${EXISTING_SWAPFILES:-0}" -ge "$MAX_SWAPFILES" ]; then
    exit 0
fi

ADD_SWAP_MB=512
NEW_TOTAL_MB=$((CURRENT_SWAP_MB + ADD_SWAP_MB))
if [ "$NEW_TOTAL_MB" -gt "$MAX_SWAP_MB" ]; then
    ADD_SWAP_MB=$((MAX_SWAP_MB - CURRENT_SWAP_MB))
    NEW_TOTAL_MB=$MAX_SWAP_MB
fi
if [ "$ADD_SWAP_MB" -lt 128 ]; then
    exit 0
fi

# Require at least 4GB disk headroom beyond the new swapfile.
AVAIL_DISK_MB=$(df -Pm / | awk 'NR==2{print $4}')
if [ "${AVAIL_DISK_MB:-0}" -lt $((ADD_SWAP_MB + 4096)) ]; then
    log "Insufficient disk space (${AVAIL_DISK_MB}MB free). Skipping emergency swap."
    exit 0
fi

# Pick a deterministic bounded slot (never timestamp-stacked)
NEW_SWAPFILE=""
for _slot in 1 2; do
    _cand="${SWAPFILE_PREFIX}-${_slot}"
    if [ ! -e "$_cand" ]; then
        NEW_SWAPFILE="$_cand"
        break
    fi
done
if [ -z "$NEW_SWAPFILE" ]; then
    exit 0
fi

log "Host MemAvailable=${MEM_AVAIL_PCT}% (swap_critical=${SWAP_CRITICAL}). Adding bounded emergency swap ${ADD_SWAP_MB}MB at ${NEW_SWAPFILE}..."

if ! fallocate -l "${ADD_SWAP_MB}M" "$NEW_SWAPFILE" 2>/dev/null; then
    if ! dd if=/dev/zero of="$NEW_SWAPFILE" bs=1M count="$ADD_SWAP_MB" status=none; then
        log "Failed to create swap file."
        rm -f "$NEW_SWAPFILE"
        exit 1
    fi
fi

chmod 600 "$NEW_SWAPFILE"
# Priority 10 like the installer swapfile: below zram (100), so
# compressed RAM stays the first overflow tier.
if ! mkswap "$NEW_SWAPFILE" >/dev/null 2>&1 || ! (swapon -p 10 "$NEW_SWAPFILE" 2>/dev/null || swapon "$NEW_SWAPFILE" 2>/dev/null); then
    log "Failed to activate swap file ${NEW_SWAPFILE}; removing it to prevent disk leak."
    rm -f "$NEW_SWAPFILE"
    exit 1
fi

if ! grep -q "$NEW_SWAPFILE" /etc/fstab ; then
    echo "$NEW_SWAPFILE none swap sw,pri=10 0 0" >> /etc/fstab
fi

log "Added ${ADD_SWAP_MB}MB emergency swap (${NEW_SWAPFILE}). Total swap is now approx ${NEW_TOTAL_MB}MB."
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

# ─── Host memory tuning: KSM page merging + zram compressed swap ──────
# KSM dedupes identical pages across the Python worker fleet (20-40% of
# worker RSS at <1% CPU); zram compresses cold pages in RAM before the
# kernel touches disk swap. Both are best-effort (VPS kernels without
# KSM/zram skip quietly) and re-applied every boot via systemd.
if [ -f "$INSTALL_DIR/scripts/setup-memory-tuning.sh" ]; then
    chmod +x "$INSTALL_DIR/scripts/setup-memory-tuning.sh"  || true
    bash "$INSTALL_DIR/scripts/setup-memory-tuning.sh" || true
    if [ -f "$INSTALL_DIR/scripts/smsly-memory-tuning.service" ]; then
        cp "$INSTALL_DIR/scripts/smsly-memory-tuning.service" /etc/systemd/system/smsly-memory-tuning.service  || true
        systemctl daemon-reload  || true
        systemctl enable smsly-memory-tuning.service  || echo -e "${YELLOW}    ⚠ smsly-memory-tuning enable failed (non-fatal)${NC}"
        # Already applied live above; `start` (not `restart`) only refreshes
        # RemainAfterExit state and is a no-op when already active.
        systemctl start smsly-memory-tuning.service  || true
        echo -e "${GREEN}  ✓ Host memory tuning (KSM+zram) installed and applied${NC}"
    fi
else
    echo -e "${YELLOW}  ⚠ setup-memory-tuning.sh missing — KSM/zram tuning skipped${NC}"
fi

# Platform integrity guard (hourly): registry TLS pair, egress NIC rules,
# SPIRE containers, edge lockdown, pending migrations, Traefik middleware
# refs. Self-heals or ALERTs to the log; see the script header.
INTEGRITY_SCRIPT="/opt/smsly-hosting/scripts/verify_platform_integrity.sh"
INTEGRITY_CRON="0 * * * * root $INTEGRITY_SCRIPT >> /var/log/smsly-integrity.log 2>&1"
# The script ships non-executable from git on some checkouts; make it
# executable first so the -x gate cannot false-negative (2026-09-12).
chmod +x "$INTEGRITY_SCRIPT" 2>/dev/null || true
if [ -f "$INTEGRITY_SCRIPT" ]; then
    if command -v systemctl >/dev/null 2>&1 && systemctl is-enabled --quiet smsly-integrity.timer 2>/dev/null; then
        echo -e "${GREEN}  ✓ Platform integrity guard handled by systemd timer (smsly-integrity.timer)${NC}"
    elif ! grep -q "verify_platform_integrity.sh" /etc/crontab ; then
        echo "$INTEGRITY_CRON" >> /etc/crontab
        echo -e "${GREEN}  ✓ Platform integrity guard scheduled hourly via cron${NC}"
    else
        echo -e "${GREEN}  ✓ Platform integrity guard already scheduled${NC}"
    fi
else
    echo -e "${YELLOW}  ⚠ verify_platform_integrity.sh missing — integrity guard NOT scheduled${NC}"
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
if [ "${MODE_AGENT_LITE:-false}" = "true" ]; then
    CRITICAL_CONTAINERS=(smsly-hosting-traefik-1 smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-redis-1 smsly-hosting-rabbitmq-1 smsly-hosting-socket-proxy-1)
elif is_node_mode; then
    CRITICAL_CONTAINERS=(smsly-hosting-caddy-1 smsly-hosting-traefik-1 smsly-hosting-backend-1 smsly-hosting-celery-worker-1 smsly-hosting-celery-beat-1 smsly-hosting-redis-1 smsly-hosting-rabbitmq-1 smsly-hosting-socket-proxy-1 smsly-postgres-primary)
else
    CRITICAL_CONTAINERS=(smsly-hosting-backend-1 smsly-postgres-primary smsly-patroni-1 smsly-haproxy smsly-redis-primary smsly-hosting-rabbitmq-1 smsly-hosting-caddy-1 smsly-hosting-traefik-1 smsly-hosting-pgcat-1 smsly-hosting-celery-1 smsly-hosting-celery-deploy-1 smsly-hosting-celery-fast-1 smsly-hosting-celery-beat-1 smsly-hosting-socket-proxy-1)
fi
for CONTAINER in "${CRITICAL_CONTAINERS[@]}"; do
    resolved_container="$(resolve_container_target "$CONTAINER" 2>/dev/null || true)"
    [ -n "$resolved_container" ] || continue
    CPID=$(docker inspect --format '{{.State.Pid}}' "$resolved_container" 2>/dev/null || echo "")
    if [ -n "$CPID" ] && [ "$CPID" != "0" ] && [ -f "/proc/$CPID/oom_score_adj" ]; then
        echo -500 > "/proc/$CPID/oom_score_adj" 2>/dev/null || true
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
    else
        # SECURITY: Fail loud instead of silently opening SSH to everyone.
        echo -e "${YELLOW}  ⚠ WARNING: MASTER_IP is empty or invalid. SSH will be restricted to existing rules.${NC}"
        echo -e "${YELLOW}    Set MASTER_IP in .env to enable master→node SSH access.${NC}"
    fi
    
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
    ufw allow "${WG_PRIMARY_PORT:-51820}/udp"  || true
    ufw allow "${WG_FALLBACK_PORT:-33500}/udp"  || true
    if ip link show wg0 >/dev/null 2>&1; then
        ufw allow in on wg0 to any port 53  || true
        ufw allow in on wg0 to any port 8000 proto tcp  || true
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
    # Allow SSH port 22 on master/standalone before enabling UFW; agent-lite/node
    # restrict SSH to MASTER_IP via the rule above.
    if [ "$MODE_AGENT_LITE" != "true" ] && [ "$MODE_NODE" != "true" ]; then
        ufw allow 22/tcp || true
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
        # Insert DROP at index 1 first so it sits BEFORE Docker's -j RETURN rule;
        # ACCEPT rules inserted at index 1 afterward sit above the DROP.
        iptables -I DOCKER-USER 1 -p tcp --dport "$_port" -j DROP  || true

        # Allow localhost (container-to-container on the same host)
        iptables -I DOCKER-USER 1 -i lo -p tcp --dport "$_port" -j ACCEPT  || true

        # Allow Docker bridge networks (172.16.0.0/12 covers docker0 + compose nets)
        iptables -I DOCKER-USER 1 -s 172.16.0.0/12 -p tcp --dport "$_port" -j ACCEPT  || true

        # Allow WireGuard mesh (10.100.0.0/24 is the assigned mesh range)
        iptables -I DOCKER-USER 1 -s 10.100.0.0/24 -p tcp --dport "$_port" -j ACCEPT  || true

        # Allow known node IPs
        if [ -n "${MASTER_MESH_IP:-}" ]; then
            iptables -I DOCKER-USER 1 -s "${MASTER_MESH_IP}" -p tcp --dport "$_port" -j ACCEPT  || true
        fi
    done

    # Return to the DOCKER chain for all other traffic
    iptables -C DOCKER-USER -j RETURN  || \
        iptables -A DOCKER-USER -j RETURN  || true

    echo -e "${GREEN}  ✓ Infrastructure ports hardened (5000, 5432, 6379, 5672) — locked to localhost + mesh + docker networks${NC}"

    # Allow remote Promtail → Loki on WireGuard interface (VPN mesh)
    iptables -C INPUT -i wg+ -p tcp --dport 3100 -j ACCEPT 2>/dev/null || \
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
