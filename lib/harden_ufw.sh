#!/bin/bash

_harden_ufw_wg0_rules() {
    # Allow internal registry (5000), mesh DNS (53), and node control API
    # (8000) ONLY over WireGuard (wg0) / explicit allowlisted node IPs.
    if ip link show wg0 >/dev/null 2>&1; then
        local _ufw_v=""
        _ufw_v="$(ufw status verbose 2>/dev/null || true)"
        echo "$_ufw_v" | grep -qE "(^|[[:space:]])5000(/tcp)?([[:space:]]|$).*on wg0|on wg0.*(^|[[:space:]])5000(/tcp)?([[:space:]]|$)" \
            || ufw allow in on wg0 to any port 5000 proto tcp \
            || echo -e "${YELLOW}    ⚠ ufw allow registry on wg0 failed${NC}"
        echo "$_ufw_v" | grep -qE "(^|[[:space:]])53(/tcp|/udp)?([[:space:]]|$).*on wg0|on wg0.*(^|[[:space:]])53(/tcp|/udp)?([[:space:]]|$)" \
            || ufw allow in on wg0 to any port 53 \
            || echo -e "${YELLOW}    ⚠ ufw allow mesh dns on wg0 failed${NC}"
        echo "$_ufw_v" | grep -qE "(^|[[:space:]])8000(/tcp)?([[:space:]]|$).*on wg0|on wg0.*(^|[[:space:]])8000(/tcp)?([[:space:]]|$)" \
            || ufw allow in on wg0 to any port 8000 proto tcp \
            || echo -e "${YELLOW}    ⚠ ufw allow node api on wg0 failed${NC}"
        local _cryptd_gate="${CRYPTD_DB_GATE_ENABLED:-}"
        if [ -z "$_cryptd_gate" ] && [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
            _cryptd_gate="$(grep -E '^CRYPTD_DB_GATE_ENABLED=' "${INSTALL_DIR:-/opt/smsly-hosting}/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '\r"' | tr '[:upper:]' '[:lower:]' || true)"
        fi
        if [ "$_cryptd_gate" = "true" ]; then
            echo "$_ufw_v" | grep -qE "(^|[[:space:]])15432(/tcp)?([[:space:]]|$).*on wg0|on wg0.*(^|[[:space:]])15432(/tcp)?([[:space:]]|$)" \
                || ufw allow in on wg0 to any port 15432 proto tcp \
                || echo -e "${YELLOW}    ⚠ ufw allow cryptd pg gate (15432) on wg0 failed${NC}"
            echo "$_ufw_v" | grep -qE "(^|[[:space:]])16379(/tcp)?([[:space:]]|$).*on wg0|on wg0.*(^|[[:space:]])16379(/tcp)?([[:space:]]|$)" \
                || ufw allow in on wg0 to any port 16379 proto tcp \
                || echo -e "${YELLOW}    ⚠ ufw allow cryptd redis gate (16379) on wg0 failed${NC}"
        fi
    fi
    if [ -n "${NODE_REGISTRY_ALLOW_IPS:-}" ]; then
        local _nip=""
        for _nip in ${NODE_REGISTRY_ALLOW_IPS//,/ }; do
            [ -n "$_nip" ] || continue
            ufw allow from "$_nip" to any port 5000 proto tcp \
                || echo -e "${YELLOW}    ⚠ ufw allow registry from $_nip failed${NC}"
        done
    fi
}

_harden_ufw_media_rules() {
    if [ "${SMSLY_NODE_ROLE:-}" = "media" ] || [ "${MODE_MEDIA_NODE:-false}" = "true" ]; then
        local _mport=""
        for _mport in 5060/tcp 5060/udp 3478/tcp 3478/udp 7880/tcp 7881/tcp 30000:31000/udp; do
            ufw allow "$_mport" || echo -e "${YELLOW}    ⚠ ufw allow $_mport failed${NC}"
        done
    fi
}

_harden_ufw_master_ip_valid() {
    local _ip="${1:-}"
    [ -n "$_ip" ] && [ "$_ip" != "127.0.0.1" ] && ! echo "$_ip" | grep -qE '^(0\.0\.0\.0|localhost)$'
}

_harden_ufw_bootstrap() {
    command -v ufw  || apt_run apt-get install -y ufw  || true
    command -v ufw  || return 1

    local _is_agent_lite=false
    if [ "${MODE_AGENT_LITE:-false}" = "true" ] || [ "${INSTALL_MODE:-}" = "agent-lite" ]; then
        _is_agent_lite=true
    fi
    local _master_ip="${MASTER_IP:-}"
    if [ -z "$_master_ip" ] && [ -f "${INSTALL_DIR:-/opt/smsly-hosting}/.env" ]; then
        _master_ip="$(grep -E '^MASTER_IP=' "${INSTALL_DIR:-/opt/smsly-hosting}/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '\r"' || true)"
    fi
    local _lockdown_master=false
    if _harden_ufw_master_ip_valid "$_master_ip"; then
        _lockdown_master=true
    fi

    # Already active — just verify ports are open, then bail
    if ufw status  | grep -qi "active"; then
        local _ufw_cur=""
        _ufw_cur="$(ufw status verbose 2>/dev/null || true)"
        if [ "$_is_agent_lite" = "true" ] && [ "$_lockdown_master" = "true" ]; then
            echo "$_ufw_cur" | grep -qE "22(/tcp)?[[:space:]]+ALLOW.*${_master_ip}" \
                || ufw allow from "$_master_ip" to any port 22 || echo -e "${YELLOW}    ⚠ ufw allow 22 from $_master_ip failed${NC}"
            echo "$_ufw_cur" | grep -qE "80(/tcp)?[[:space:]]+ALLOW.*${_master_ip}" \
                || ufw allow from "$_master_ip" to any port 80 || echo -e "${YELLOW}    ⚠ ufw allow 80 from $_master_ip failed${NC}"
            for port in 51820/udp 33500/udp; do
                echo "$_ufw_cur" | grep -qE "${port}.*ALLOW" || ufw allow "$port" || echo -e "${YELLOW}    ⚠ ufw allow port $port failed${NC}"
            done
        elif [ "$_lockdown_master" = "true" ]; then
            echo "$_ufw_cur" | grep -qE "22(/tcp)?[[:space:]]+ALLOW.*${_master_ip}" \
                || ufw allow from "$_master_ip" to any port 22 || echo -e "${YELLOW}    ⚠ ufw allow 22 from $_master_ip failed${NC}"
            for port in 80/tcp 443/tcp 51820/udp 33500/udp; do
                echo "$_ufw_cur" | grep -qE "${port}.*ALLOW" || ufw allow "$port" || echo -e "${YELLOW}    ⚠ ufw allow port $port failed${NC}"
            done
        else
            for port in 22 80 443 51820 33500; do
                echo "$_ufw_cur" | grep -qE "${port}(/tcp|/udp)?.*ALLOW" || ufw allow "$port" || echo -e "${YELLOW}    ⚠ ufw allow port $port failed${NC}"
            done
        fi
        _harden_ufw_wg0_rules
        _harden_ufw_media_rules
        # Allow only DNS (:53) on Docker bridges (never blanket-allow tenant bridges to host ports)
        for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
            ip link show "$iface" >/dev/null 2>&1 || continue
            ufw allow in on "$iface" to any port 53 || echo -e "${YELLOW}    ⚠ ufw allow DNS on $iface failed${NC}"
        done
        return 0
    fi

    # Inactive — configure and enable (INPUT default deny, FORWARD stays open for Docker)
    ufw --force default deny incoming || echo -e "${YELLOW}    s ufw default deny incoming failed${NC}"
    ufw --force default allow outgoing || echo -e "${YELLOW}    s ufw default allow outgoing failed${NC}"

    local ssh_port=""
    ssh_port=$(ss -tlnp 2>/dev/null | grep sshd | awk '{print $4}' | awk -F: '{print $NF}' | head -1)
    if [ "$_lockdown_master" = "true" ]; then
        ufw allow from "$_master_ip" to any port "${ssh_port:-22}" proto tcp || echo -e "${YELLOW}    ⚠ ufw allow ssh from $_master_ip failed${NC}"
    elif [ -n "$ssh_port" ]; then
        ufw allow "$ssh_port/tcp" || echo -e "${YELLOW}    s ufw allow ssh port $ssh_port failed${NC}"
    else
        ufw allow ssh || echo -e "${YELLOW}    s ufw allow ssh failed${NC}"
    fi
    if [ "$_is_agent_lite" = "true" ]; then
        if [ "$_lockdown_master" = "true" ]; then
            ufw allow from "$_master_ip" to any port 80 proto tcp || echo -e "${YELLOW}    ⚠ ufw allow 80 from $_master_ip failed${NC}"
        else
            echo -e "${YELLOW}    ⚠ Agent-Lite missing MASTER_IP — skipping blanket port 80/443${NC}"
        fi
    else
        ufw allow 80/tcp || echo -e "${YELLOW}    ⚠ ufw allow 80/tcp failed${NC}"
        ufw allow 443/tcp || echo -e "${YELLOW}    ⚠ ufw allow 443/tcp failed${NC}"
    fi
    ufw allow 51820/udp || echo -e "${YELLOW}    ⚠ ufw allow 51820/udp failed${NC}"
    ufw allow 33500/udp || echo -e "${YELLOW}    ⚠ ufw allow 33500/udp failed${NC}"

    _harden_ufw_wg0_rules
    _harden_ufw_media_rules

    for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
        ip link show "$iface" >/dev/null 2>&1 || continue
        ufw allow in on "$iface" to any port 53 || echo -e "${YELLOW}    ⚠ ufw allow DNS on $iface failed${NC}"
    done
    ufw --force enable || echo -e "${YELLOW}    ⚠ ufw enable failed${NC}"
    # Verify it actually came up
    for _i in $(seq 1 5); do
        ufw status  | grep -qi "active" && break
        sleep 2
    done
}

_harden_ufw_verify() {
    command -v ufw  || { _harden_log warn "ufw — not installed"; return 1; }
    if ufw status  | grep -qi "active"; then
        _harden_log ok "ufw active (host INPUT hardened)"
        return 0
    fi
    _harden_log warn "ufw not active — check ufw status"
    return 1
}
