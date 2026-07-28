#!/bin/bash

_harden_ufw_bootstrap() {
    command -v ufw  || apt_run apt-get install -y ufw  || true
    command -v ufw  || return 1

    # Already active — just verify ports are open, then bail
    if ufw status  | grep -qi "active"; then
        for port in 22 80 443 51820; do
            ufw status verbose  | grep -qE "${port}(/tcp|/udp)?.*ALLOW" || ufw allow "$port" || echo -e "${YELLOW}    ⚠ ufw allow port $port failed${NC}"
        done
        # Whitelist Docker bridges
        for iface in docker0 $(ls /sys/class/net 2>/dev/null | grep '^br-'); do
            ip link show "$iface" >/dev/null 2>&1 || continue
            ufw allow in on "$iface" || echo -e "${YELLOW}    ⚠ ufw allow in on $iface failed${NC}"
        done
        return 0
    fi

    # Inactive — configure and enable (INPUT default deny, FORWARD stays open for Docker)
    ufw --force default deny incoming || echo -e "${YELLOW}    ⚠ ufw default deny incoming failed${NC}"
    ufw --force default allow outgoing || echo -e "${YELLOW}    ⚠ ufw default allow outgoing failed${NC}"
    ufw allow ssh || echo -e "${YELLOW}    ⚠ ufw allow ssh failed${NC}"
    ufw allow 80/tcp || echo -e "${YELLOW}    ⚠ ufw allow 80/tcp failed${NC}"
    ufw allow 443/tcp || echo -e "${YELLOW}    ⚠ ufw allow 443/tcp failed${NC}"
    ufw allow 51820/udp || echo -e "${YELLOW}    ⚠ ufw allow 51820/udp failed${NC}"
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
