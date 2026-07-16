#!/usr/bin/env bash
# Monitor critical SMSLY hosting infrastructure on the primary server:
#   - Docker daemon
#   - Systemd services (autoscaler, wireguard)
#   - iptables firewall rules
#   - All production + observability Docker containers
#   - Zombie processes
#
# Runs every 60 seconds via smsly-infra-monitor.timer

set -euo pipefail

INSTALL_DIR="/opt/smsly-hosting"
COMPOSE_FILE="$INSTALL_DIR/docker-compose.prod.yml"
OBS_COMPOSE_FILE="$INSTALL_DIR/infrastructure/docker/docker-compose.observability.yml"
LOG_TAG="smsly-infra-monitor"

log() {
    logger -t "$LOG_TAG" "$*" 2>/dev/null || true
    printf '[%s] %s\n' "$LOG_TAG" "$*"
}

# ─── Guard: Exit if installer/updater is active ───────────────────────────
if [ -f "/tmp/smsly-install.lock" ]; then
    log "Installer/Updater lock detected (/tmp/smsly-install.lock). Exiting to prevent race conditions."
    exit 0
fi

# ─── Systemd services to keep alive ────────────────────────────────────
SYSTEMD_SERVICES=(
    "smsly-autoscaler.service"
    "smsly-domain-ssl.service"
)

# ─── Systemd timers that must be active ────────────────────────────────
SYSTEMD_TIMERS=(
    "smsly-domain-ssl.timer"
)

# ─── Production stack ──────────────────────────────────────────────────
PROD_SERVICES=(
    "db" "pgcat" "redis" "registry" "rabbitmq"
    "backend" "celery" "celery-fast" "celery-deploy" "celery-beat"
    "frontend" "socket-proxy" "caddy"
)

# ─── Observability stack (separate compose file) ────────────────────────
OBS_SERVICES=(
    "loki" "promtail" "prometheus" "docker-labels" "grafana"
    "cadvisor" "node-exporter"
)

# ══════════════════════════════════════════════════════════════════════════
# 1. Zombie Process Cleanup
# ══════════════════════════════════════════════════════════════════════════
zombies=$(ps -eo pid=,ppid=,stat=,comm= 2>/dev/null | awk '$3 ~ /^Z/ {print $1":"$2":"$4}' || true)
if [ -n "$zombies" ]; then
    zombie_count=$(echo "$zombies" | wc -l)
    log "Zombie processes: $zombie_count. Sending SIGCHLD to parents..."
    echo "$zombies" | while IFS=: read -r pid ppid comm; do
        kill -s SIGCHLD "$ppid" 2>/dev/null || true
    done
    sleep 1
    remaining=$(ps -eo pid=,stat= 2>/dev/null | awk '$2 ~ /^Z/' | wc -l)
    if [ "$remaining" -gt 0 ]; then
        log "Warning: $remaining zombie(s) remain. Unreapable zombies:"
        ps -eo pid=,ppid=,stat=,comm= 2>/dev/null | awk '$3 ~ /^Z/ {print "  PID="$1" PPID="$2" CMD="$4}' | while read -r line; do
            log "$line"
        done
    else
        log "All zombie processes reaped successfully"
    fi
fi

# ══════════════════════════════════════════════════════════════════════════
# 2. Docker Daemon Health (MUST run before container checks)
# ══════════════════════════════════════════════════════════════════════════
DOCKER_OK=false
if docker info >/dev/null 2>&1; then
    DOCKER_OK=true
else
    log "Alert: Docker daemon is not responding. Attempting restart..."
    systemctl restart docker || log "Warning: Docker restart attempted — check result above"
    sleep 5
    if docker info >/dev/null 2>&1; then
        log "Docker daemon recovered after restart"
        DOCKER_OK=true
    else
        log "CRITICAL: Docker daemon failed to restart. Skipping container checks."
    fi
fi

# ══════════════════════════════════════════════════════════════════════════
# 3. Systemd Service Health
# ══════════════════════════════════════════════════════════════════════════
for svc in "${SYSTEMD_SERVICES[@]}"; do
    if systemctl is-enabled "$svc" >/dev/null 2>&1; then
        state=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
        if [ "$state" != "active" ]; then
            log "Alert: systemd service $svc is $state. Restarting..."
            systemctl restart "$svc" || log "Warning: Service $svc restart attempted — check result above"
        fi
    fi
done

# Systemd timers — must stay active even if the triggered service is oneshot
for tmr in "${SYSTEMD_TIMERS[@]}"; do
    if systemctl is-enabled "$tmr" >/dev/null 2>&1; then
        tmr_state=$(systemctl is-active "$tmr" 2>/dev/null || echo "unknown")
        if [ "$tmr_state" != "active" ]; then
            log "Alert: systemd timer $tmr is $tmr_state. Restarting..."
            systemctl restart "$tmr" || log "Warning: Timer $tmr restart attempted — check result above"
        fi
    fi
done

# WireGuard interfaces — check if any are configured and running
if command -v wg >/dev/null 2>&1; then
    wg_ifaces=$(wg show interfaces 2>/dev/null || true)
    if [ -n "$wg_ifaces" ]; then
        for iface in $wg_ifaces; do
            wg_state=$(systemctl is-active "wg-quick@${iface}.service" 2>/dev/null || echo "unknown")
            if [ "$wg_state" != "active" ] && [ "$wg_state" != "unknown" ]; then
                log "Alert: WireGuard interface $iface service is $wg_state. Restarting..."
                systemctl restart "wg-quick@${iface}.service" || log "Warning: WireGuard $iface restart attempted — check result above"
            fi
        done
    fi
fi

# ══════════════════════════════════════════════════════════════════════════
# 4. iptables Firewall Rule Verification
# ══════════════════════════════════════════════════════════════════════════
if command -v iptables >/dev/null 2>&1; then
    # Ensure remote Promtail → Loki is allowed on WireGuard interfaces
    if command -v wg >/dev/null 2>&1 && wg show interfaces 2>/dev/null | grep -q .; then
        iptables -C INPUT -i wg+ -p tcp --dport 3100 -j ACCEPT 2>/dev/null || \
            iptables -A INPUT -i wg+ -p tcp --dport 3100 -j ACCEPT 2>/dev/null || true
    fi
    rule_count=$(iptables -L INPUT -n 2>/dev/null | grep -cE '^ACCEPT|^DROP|^REJECT' || echo "0")
    if [ "$rule_count" -eq 0 ] 2>/dev/null; then
        if [ -f /etc/iptables/rules.v4 ]; then
            log "Alert: iptables INPUT chain has 0 rules. Restoring from /etc/iptables/rules.v4..."
            iptables-restore < /etc/iptables/rules.v4 2>/dev/null || \
                log "Warning: Failed to restore iptables rules"
        else
            log "Warning: iptables INPUT chain empty and /etc/iptables/rules.v4 not found"
        fi
    fi
fi

# ══════════════════════════════════════════════════════════════════════════
# 5. Docker Container Health
# ══════════════════════════════════════════════════════════════════════════
if [ "$DOCKER_OK" != "true" ]; then
    log "Skipping container checks — Docker daemon is not available"
    exit 0
fi

check_and_heal() {
    local compose_file=$1
    local service=$2

    container_id=$(docker compose -f "$compose_file" ps -q "$service" 2>/dev/null || true)

    if [ -z "$container_id" ]; then
        log "Warning: Container for service '$service' is missing. Attempting to start..."
        docker compose -f "$compose_file" up -d "$service" || log "Warning: Failed to start service $service"
        return
    fi

    inspect_data=$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null || true)

    if [ -z "$inspect_data" ]; then
        log "Warning: Failed to inspect container '$container_id' for service '$service'. Attempting restart..."
        docker compose -f "$compose_file" restart "$service" || log "Warning: Failed to restart service $service"
        return
    fi

    status=$(echo "$inspect_data" | awk '{print $1}')
    health=$(echo "$inspect_data" | awk '{print $2}')

    if [ "$status" != "running" ]; then
        log "Alert: Container for service '$service' is not running (status: $status). Restarting..."
        docker compose -f "$compose_file" restart "$service" || log "Warning: Failed to restart service $service (not running)"
    elif [ "$health" = "unhealthy" ]; then
        log "Alert: Container for service '$service' is running but UNHEALTHY. Restarting..."
        docker compose -f "$compose_file" restart "$service" || log "Warning: Failed to restart service $service (unhealthy)"
    fi
}

for service in "${PROD_SERVICES[@]}"; do
    check_and_heal "$COMPOSE_FILE" "$service"
done

if [ -f "$OBS_COMPOSE_FILE" ]; then
    for service in "${OBS_SERVICES[@]}"; do
        check_and_heal "$OBS_COMPOSE_FILE" "$service"
    done
fi
