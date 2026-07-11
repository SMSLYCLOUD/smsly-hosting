# lib/media-node-ops.sh — Media Node runtime operations
# Sourced by install.sh or called directly for status/restart/logs

[ "${_MEDIA_OPS_LOADED:-}" = "true" ] && return 0
_MEDIA_OPS_LOADED=true

MEDIA_NODE_ENV="/opt/smsly-hosting-media/.env"

# ─── Status ───────────────────────────────────────────────────────────────────
media_status() {
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  SMSLY Media Node — Status${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"

    echo -e "\n${BLUE}  System:${NC}"
    echo "    Host:     $(hostname -f 2>/dev/null || hostname)"
    echo "    Kernel:   $(uname -r)"
    echo "    CPU:      $(nproc) cores | $(awk '/MemTotal/{printf "%.0f MB", $2/1024}' /proc/meminfo)"
    echo "    Public IP: $(detect_public_ip 2>/dev/null || echo 'unknown')"

    echo -e "\n${BLUE}  Config:${NC}"
    if [ -f "$MEDIA_NODE_ENV" ]; then
        echo "    NODE_ID:    $(grep -m1 '^NODE_ID=' "$MEDIA_NODE_ENV" | cut -d= -f2-)"
        echo "    NODE_TYPE:  $(grep -m1 '^NODE_TYPE=' "$MEDIA_NODE_ENV" | cut -d= -f2-)"
        echo "    PUBLIC_IP:  $(grep -m1 '^PUBLIC_IP=' "$MEDIA_NODE_ENV" | cut -d= -f2-)"
        echo "    MASTER_URL: $(grep -m1 '^MASTER_API_URL=' "$MEDIA_NODE_ENV" | cut -d= -f2-)"
    else
        echo "    (no config found — not installed)"
    fi

    echo -e "\n${BLUE}  Services:${NC}"
    for svc in smsly-media-mgmt smsly-voice-api smsly-video livekit-server rtpengine freeswitch kamailio coturn openresty postgresql redis-server; do
        local status="not installed"
        if systemctl is-enabled "$svc" >/dev/null 2>&1; then
            if systemctl is-active "$svc" >/dev/null 2>&1; then
                echo -e "    ${GREEN}✓${NC} $svc (running)"
            else
                echo -e "    ${YELLOW}○${NC} $svc (stopped)"
            fi
        fi
    done

    echo -e "\n${BLUE}  Ports:${NC}"
    ss -tlnp 2>/dev/null | grep -E ':(5060|5061|7880|9090|80|443|3478) ' | awk '{printf "    %-20s %s\n", $4, $6}' || true
    ss -ulnp 2>/dev/null | grep -E ':(5060|22222|30000-31000|3478) ' | awk '{printf "    %-20s %s\n", $4, $6}' || true
}

# ─── Restart ──────────────────────────────────────────────────────────────────
media_restart() {
    echo -e "${BLUE}  → Restarting media services...${NC}"
    local failed=0
    for svc in smsly-media-mgmt livekit-server rtpengine freeswitch kamailio coturn smsly-voice-api smsly-video openresty; do
        if systemctl is-enabled "$svc" >/dev/null 2>&1; then
            if systemctl restart "$svc" 2>/dev/null; then
                echo -e "    ${GREEN}✓${NC} $svc restarted"
            else
                echo -e "    ${RED}✗${NC} $svc restart failed"
                ((failed++))
            fi
        fi
    done
    if [ "$failed" -gt 0 ]; then
        echo -e "${RED}  ✗ $failed service(s) failed to restart${NC}"
        return 1
    fi
    echo -e "${GREEN}  ✓ All media services restarted${NC}"
}

# ─── Logs ─────────────────────────────────────────────────────────────────────
media_logs() {
    local service="${1:-smsly-media-mgmt}"
    local lines="${2:-100}"

    echo -e "${BLUE}  → Tailing $service logs (last $lines lines)...${NC}"
    if command -v journalctl >/dev/null 2>&1; then
        journalctl -u "$service" --no-pager -n "$lines" 2>/dev/null || {
            echo -e "${YELLOW}  ⚠ No logs for $service (service may not exist)${NC}"
        }
    else
        tail -n "$lines" "/var/log/${service}.log" 2>/dev/null || {
            echo -e "${YELLOW}  ⚠ No log file for $service${NC}"
        }
    fi
}

# ─── Health Check ─────────────────────────────────────────────────────────────
media_health() {
    echo -e "${BLUE}  → Checking media node health...${NC}"
    local failures=0

    # Check management daemon
    if curl -sf http://127.0.0.1:9090/health >/dev/null 2>&1; then
        echo -e "    ${GREEN}✓${NC} Management daemon: healthy"
    else
        echo -e "    ${RED}✗${NC} Management daemon: unreachable"
        ((failures++))
    fi

    # Check LiveKit
    if systemctl is-active livekit-server >/dev/null 2>&1; then
        echo -e "    ${GREEN}✓${NC} LiveKit: running"
    else
        echo -e "    ${RED}✗${NC} LiveKit: not running"
        ((failures++))
    fi

    # Check FreeSWITCH
    if command -v fs_cli >/dev/null 2>&1 && fs_cli -x "status" >/dev/null 2>&1; then
        echo -e "    ${GREEN}✓${NC} FreeSWITCH: running"
    else
        echo -e "    ${YELLOW}○${NC} FreeSWITCH: not responding"
    fi

    # Check RTPEngine
    if systemctl is-active rtpengine >/dev/null 2>&1; then
        echo -e "    ${GREEN}✓${NC} RTPEngine: running"
    else
        echo -e "    ${RED}✗${NC} RTPEngine: not running"
        ((failures++))
    fi

    # Check PostgreSQL
    if systemctl is-active postgresql >/dev/null 2>&1; then
        echo -e "    ${GREEN}✓${NC} PostgreSQL: running"
    else
        echo -e "    ${RED}✗${NC} PostgreSQL: not running"
        ((failures++))
    fi

    # Check Redis
    if systemctl is-active redis-server >/dev/null 2>&1; then
        echo -e "    ${GREEN}✓${NC} Redis: running"
    else
        echo -e "    ${RED}✗${NC} Redis: not running"
        ((failures++))
    fi

    if [ "$failures" -gt 0 ]; then
        echo -e "\n${RED}  ✗ $failures component(s) unhealthy${NC}"
        return 1
    fi

    echo -e "\n${GREEN}  ✓ All components healthy${NC}"
    return 0
}

# ─── Dump diagnostics ────────────────────────────────────────────────────────
media_diagnose() {
    media_status
    echo ""
    media_health
    echo ""
    echo -e "${BLUE}  → Recent systemd failures:${NC}"
    systemctl --failed --no-pager 2>/dev/null | head -20 || true
    echo ""
    echo -e "${BLUE}  → Listening ports:${NC}"
    ss -tlnp 2>/dev/null | head -30 || netstat -tlnp 2>/dev/null | head -30 || true
}
