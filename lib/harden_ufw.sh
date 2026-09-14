#!/bin/bash

_harden_ufw_bootstrap() {
    command -v ufw  || apt_run apt-get install -y ufw  || true
    command -v ufw  || return 1

    # Already active — just verify ports are open, then bail
    if ufw status  | grep -qi "active"; then
        for port in 22 80 443 51820 33500; do
            ufw status verbose  | grep -qE "${port}(/tcp|/udp)?.*ALLOW" || ufw allow "$port" || echo -e "${YELLOW}    ⚠ ufw allow port $port failed${NC}"
        done
        # Multi-node registry access (see the inactive branch below for
        # the security rationale — wg0 mesh or explicit node IPs only).
        if ip link show wg0 >/dev/null 2>&1; then
            ufw status verbose | grep -q "5000/tcp.*ALLOW" \
                || ufw allow in on wg0 to any port 5000 proto tcp \
                || echo -e "${YELLOW}    ⚠ ufw allow registry on wg0 failed${NC}"
        fi
        if [ -n "${NODE_REGISTRY_ALLOW_IPS:-}" ]; then
            local _nip
            for _nip in ${NODE_REGISTRY_ALLOW_IPS//,/ }; do
                [ -n "$_nip" ] || continue
                ufw allow from "$_nip" to any port 5000 proto tcp \
                    || echo -e "${YELLOW}    ⚠ ufw allow registry from $_nip failed${NC}"
            done
        fi
        # Whitelist Docker bridges
        for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
            ip link show "$iface" >/dev/null 2>&1 || continue
            ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
        done
        return 0
    fi

    # Inactive — configure and enable (INPUT default deny, FORWARD stays open for Docker)
    ufw --force default deny incoming || echo -e "${YELLOW}    s ufw default deny incoming failed${NC}"
    ufw --force default allow outgoing || echo -e "${YELLOW}    s ufw default allow outgoing failed${NC}"

    local ssh_port=$(ss -tlnp 2>/dev/null | grep sshd | awk '{print $4}' | awk -F: '{print $NF}' | head -1)
    if [ -n "$ssh_port" ]; then
        ufw allow "$ssh_port/tcp" || echo -e "${YELLOW}    s ufw allow ssh port $ssh_port failed${NC}"
    else
        ufw allow ssh || echo -e "${YELLOW}    s ufw allow ssh failed${NC}"
    fi
    ufw allow 80/tcp || echo -e "${YELLOW}    ⚠ ufw allow 80/tcp failed${NC}"
    ufw allow 443/tcp || echo -e "${YELLOW}    ⚠ ufw allow 443/tcp failed${NC}"
    ufw allow 51820/udp || echo -e "${YELLOW}    ⚠ ufw allow 51820/udp failed${NC}"
    ufw allow 33500/udp || echo -e "${YELLOW}    ⚠ ufw allow 33500/udp failed${NC}"

    # ── Multi-node registry access (port 5000) ─────────────────────────
    # NEVER open 5000 to the world. The registry holds every tenant's
    # images. Two secure paths:
    #   1. WireGuard mesh (preferred): wg0 interface allow — encrypted,
    #      firewalled to configured peers.
    #   2. NODE_REGISTRY_ALLOW_IPS (optional): explicit per-node public
    #      IPs when WireGuard is unavailable.
    # The public bind IP remains BLOCKED for everything else.
    if ip link show wg0 >/dev/null 2>&1; then
        ufw allow in on wg0 to any port 5000 proto tcp \
            || echo -e "${YELLOW}    ⚠ ufw allow registry on wg0 failed${NC}"
    fi
    if [ -n "${NODE_REGISTRY_ALLOW_IPS:-}" ]; then
        local _nip
        for _nip in ${NODE_REGISTRY_ALLOW_IPS//,/ }; do
            [ -n "$_nip" ] || continue
            ufw allow from "$_nip" to any port 5000 proto tcp \
                || echo -e "${YELLOW}    ⚠ ufw allow registry from $_nip failed${NC}"
        done
    fi

    for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
        ip link show "$iface" >/dev/null 2>&1 || continue
        ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
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
