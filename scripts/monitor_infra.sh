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
    logger -t "$LOG_TAG" "$*"  || true
    printf '[%s] %s\n' "$LOG_TAG" "$*"
}

# ─── Guard: Exit if installer/updater is active ───────────────────────────
if [ -f "/tmp/smsly-install.lock" ]; then
    log "Installer/Updater lock detected (/tmp/smsly-install.lock). Exiting to prevent race conditions."
    exit 0
fi

# ─── Guard: never overlap with a previous tick ──────────────────────────
# Under a wedged daemon each docker call can hang for minutes; without
# this, minute-ticks pile up and amplify the load storm they monitor
# (2026-09-16: load 178 with overlapping monitor runs).
exec 200>/tmp/smsly-infra-monitor.lock  || true
if ! flock -n 200 2>/dev/null; then
    log "Previous monitor run still active, skipping this tick."
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
    "db" "pgcat" "redis-primary" "redis-replica" "redis-sentinel-1" "redis-sentinel-2" "redis-sentinel-3"
    "registry" "rabbitmq"
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
zombies=$(ps -eo pid=,ppid=,stat=,comm=  | awk '$3 ~ /^Z/ {print $1":"$2":"$4}' || true)
if [ -n "$zombies" ]; then
    zombie_count=$(echo "$zombies" | wc -l)
    log "Zombie processes: $zombie_count. Sending SIGCHLD to parents..."
    echo "$zombies" | while IFS=: read -r pid ppid comm; do
        kill -s SIGCHLD "$ppid"  || true
    done
    sleep 1
    remaining=$(ps -eo pid=,stat=  | awk '$2 ~ /^Z/' | wc -l)
    if [ "$remaining" -gt 0 ]; then
        log "Warning: $remaining zombie(s) remain. Unreapable zombies:"
        ps -eo pid=,ppid=,stat=,comm=  | awk '$3 ~ /^Z/ {print "  PID="$1" PPID="$2" CMD="$4}' | while read -r line; do
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
if docker info ; then
    DOCKER_OK=true
else
    log "Alert: Docker daemon is not responding. Attempting restart..."
    systemctl restart docker || log "Warning: Docker restart attempted — check result above"
    sleep 5
    if docker info ; then
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
    if systemctl is-enabled "$svc" ; then
        state=$(systemctl is-active "$svc"  || echo "unknown")
        if [ "$state" != "active" ]; then
            log "Alert: systemd service $svc is $state. Restarting..."
            systemctl restart "$svc" || log "Warning: Service $svc restart attempted — check result above"
        fi
    fi
done

# Systemd timers — must stay active even if the triggered service is oneshot
for tmr in "${SYSTEMD_TIMERS[@]}"; do
    if systemctl is-enabled "$tmr" ; then
        tmr_state=$(systemctl is-active "$tmr"  || echo "unknown")
        if [ "$tmr_state" != "active" ]; then
            log "Alert: systemd timer $tmr is $tmr_state. Restarting..."
            systemctl restart "$tmr" || log "Warning: Timer $tmr restart attempted — check result above"
        fi
    fi
done

# WireGuard interfaces — check if any are configured and running
if command -v wg ; then
    wg_ifaces=$(wg show interfaces  || true)
    if [ -n "$wg_ifaces" ]; then
        for iface in $wg_ifaces; do
            wg_state=$(systemctl is-active "wg-quick@${iface}.service"  || echo "unknown")
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
if command -v iptables ; then
    # Ensure remote Promtail → Loki is allowed on WireGuard interfaces
    if command -v wg  && wg show interfaces  | grep -q .; then
        iptables -C INPUT -i wg+ -p tcp --dport 3100 -j ACCEPT  || \
            iptables -A INPUT -i wg+ -p tcp --dport 3100 -j ACCEPT  || true
    fi
    rule_count=$(iptables -L INPUT -n  | grep -cE '^ACCEPT|^DROP|^REJECT' || echo "0")
    if [ "$rule_count" -eq 0 ] ; then
        if [ -f /etc/iptables/rules.v4 ]; then
            log "Alert: iptables INPUT chain has 0 rules. Restoring from /etc/iptables/rules.v4..."
            iptables-restore < /etc/iptables/rules.v4  || \
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

    container_id=$(docker compose -f "$compose_file" ps -q "$service"  || true)

    if [ -z "$container_id" ]; then
        log "Warning: Container for service '$service' is missing. Attempting to start..."
        docker compose -f "$compose_file" up -d "$service" || log "Warning: Failed to start service $service"
        return
    fi

    inspect_data=$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id"  || true)

    if [ -z "$inspect_data" ]; then
        log "Warning: Failed to inspect container '$container_id' for service '$service'. Attempting restart..."
        docker compose -f "$compose_file" restart "$service" || log "Warning: Failed to restart service $service"
        return
    fi

    status=$(echo "$inspect_data" | awk '{print $1}')
    health=$(echo "$inspect_data" | awk '{print $2}')

    if [ "$status" != "running" ]; then
        # NOTE: `up -d`, not `restart`. A container stuck in Created
        # (recreated but never started — 2026-09-16 backend outage)
        # cannot be restarted; `up -d` starts Created AND restarts
        # Stopped/Exited uniformly.
        log "Alert: Container for service '$service' is not running (status: $status). Starting..."
        docker compose -f "$compose_file" up -d "$service" || log "Warning: Failed to start service $service (not running)"
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

# â”€â”€â”€ Host pressure tripwire (load + steal + restart storms) â”€â”€â”€â”€â”€â”€â”€â”€â”€
# 2026-09-16: Vultr capped account CPU after sustained burn nobody
# watched (load 40â†’300 over days while every dashboard stayed green).
# This logs ALERT lines (journal) when pressure exceeds safe bounds so
# the next storm pages attention BEFORE the provider does. Alert-only:
# never stops services, never changes config. Probes are /proc plus
# one bounded docker call; the tick flock above prevents overlap.
check_host_pressure() {
    local cpus
    cpus=$(nproc 2>/dev/null || echo 8)
    case "$cpus" in
        ''|*[!0-9]*) cpus=8 ;;
    esac
    if [ "$cpus" -lt 1 ]; then
        cpus=8
    fi
    # 1. Load: ALERT past 4x CPUs on the 1-minute average. Spikes from
    # single builds are normal; sustained 4x is burn-out territory.
    local load1
    load1=$(awk '{print int($1)}' /proc/loadavg 2>/dev/null || echo 0)
    case "$load1" in
        ''|*[!0-9]*) load1=0 ;;
    esac
    if [ "$load1" -gt $((cpus * 4)) ]; then
        log "ALERT: host load ${load1} > 4x CPUs (${cpus}) â€” burn-out risk, check Vultr CPU graphs and `ps` top burners"
    fi
    # 2. Steal: ALERT past 25% across the tick interval, measured via a
    # statefile delta (no sleep inside the monitor). Sustained steal
    # means the hypervisor is throttling us â€” provider ticket, not tuning.
    local state_file="/tmp/smsly-pressure-cpu"
    local cur_steal
    local cur_total
    cur_steal=$(awk '/^cpu /{print $8}' /proc/stat 2>/dev/null || echo "")
    cur_total=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8}' /proc/stat 2>/dev/null || echo "")
    if [ -n "$cur_steal" ] && [ -n "$cur_total" ] && [ -f "$state_file" ]; then
        local prev_steal
        local prev_total
        prev_steal=$(cut -d' ' -f1 "$state_file" 2>/dev/null || echo "")
        prev_total=$(cut -d' ' -f2 "$state_file" 2>/dev/null || echo "")
        case "${prev_steal}${prev_total}" in
            ''|*[!0-9]*)
                ;;
            *)
                if [ "$cur_total" -gt "$prev_total" ]; then
                    local steal_pct
                    steal_pct=$(( (cur_steal - prev_steal) * 100 / (cur_total - prev_total) ))
                    if [ "$steal_pct" -gt 25 ]; then
                        log "ALERT: CPU steal ${steal_pct}% over last tick â€” hypervisor throttling suspected, provider ticket territory"
                    fi
                fi
                ;;
        esac
    fi
    if [ -n "$cur_steal" ] && [ -n "$cur_total" ]; then
        echo "$cur_steal $cur_total" > "$state_file"  || true
    fi
    # 3. Restart storms: any smsly container restarting in a tight loop
    # (2026-09-15: falco hit 400+ restarts while reporting healthy) or
    # sitting on a chronic high count. Compared against the previous
    # tick via statefile; a fresh statefile (reboot) only surfaces
    # chronic >= 20 counts, never jump-detections without a baseline.
    local restart_state="/tmp/smsly-pressure-restarts"
    local current
    current=$(timeout 25 docker ps --format '{{.Names}} {{.RestartCount}}' 2>/dev/null | grep -E '^(smsly|envoy)-' || true)
    if [ -n "$current" ]; then
        local line
        local name
        local count
        local prev_raw
        while IFS= read -r line; do
            name=$(echo "$line" | awk '{print $1}')
            count=$(echo "$line" | awk '{print $2}')
            case "$count" in
                ''|*[!0-9]*) continue ;;
            esac
            prev_raw=""
            if [ -f "$restart_state" ]; then
                prev_raw=$(grep -E "^${name} " "$restart_state" 2>/dev/null | awk '{print $2}' || true)
            fi
            case "$prev_raw" in
                ''|*[!0-9]*)
                    # No baseline for this container: only a chronic
                    # count is worth one alert; jumps need history.
                    if [ "$count" -ge 20 ]; then
                        log "ALERT: chronic restarter (no baseline): $name at ${count} restarts"
                    fi
                    continue
                    ;;
            esac
            if [ "$count" -ge "$((prev_raw + 2))" ]; then
                log "ALERT: restart storm: $name restarted ${count}x (was ${prev_raw} last tick)"
            elif [ "$prev_raw" -lt 20 ] && [ "$count" -ge 20 ]; then
                log "ALERT: chronic restarter: $name crossed ${count} restarts"
            fi
        done <<< "$current"
        echo "$current" > "$restart_state"  || true
    fi
}

check_host_pressure
