#!/bin/bash
# Mesh DNS resolver — point node hosts at the master's CoreDNS over wg0.
#
# The master serves `*.mesh.internal` (MESH_DNS_DOMAIN) from its coredns
# container on the mesh IP (default 10.100.0.1:53). Nodes configure a
# per-link resolver on wg0 so those names resolve WITHOUT touching the
# host's global DNS path (`~domain` = routing-only in systemd-resolved).

configure_mesh_dns_resolver() {
    # Master serves the zone itself (coredns container + mesh bind); lite
    # agents address the master explicitly and need no resolver change.
    if ! is_node_mode; then
        return 0
    fi
    local mesh_domain="${MESH_DNS_DOMAIN:-mesh.internal}"
    local dns_ip="${MASTER_MESH_IP:-10.100.0.1}"

    if ! ip link show wg0 >/dev/null 2>&1; then
        echo -e "${YELLOW}  ⚠ Mesh DNS resolver skipped — wg0 not present (CoreDNS names will resolve once the mesh is up; re-run install --resume or configure manually)${NC}"
        return 0
    fi

    if command -v resolvectl >/dev/null 2>&1; then
        if resolvectl dns wg0 "$dns_ip" && resolvectl domain wg0 "~${mesh_domain}"; then
            echo -e "${GREEN}  ✓ Mesh DNS: *.${mesh_domain} → ${dns_ip} via wg0${NC}"
        else
            echo -e "${YELLOW}  ⚠ resolvectl mesh DNS config failed — set manually: resolvectl dns wg0 ${dns_ip}; resolvectl domain wg0 ~${mesh_domain}${NC}"
        fi
        return 0
    fi

    # No systemd-resolved: do NOT rewrite the global /etc/resolv.conf —
    # that would reroute all host DNS through the mesh. Print the manual
    # step instead (operator decision).
    echo -e "${YELLOW}  ⚠ resolvectl not found — cannot scope mesh DNS to wg0.${NC}"
    echo -e "${YELLOW}    Add the master's CoreDNS to your resolver for *.${mesh_domain} (e.g. dnsmasq server=/.${mesh_domain}/${dns_ip}), or install systemd-resolved.${NC}"
    return 0
}
