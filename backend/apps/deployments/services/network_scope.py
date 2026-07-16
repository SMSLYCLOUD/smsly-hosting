"""
Scoped Docker network management — ensure/create networks and egress rules.

Provides the bridge between ``ScopedNetwork`` model configuration and
actual Docker network operations.
"""

import ipaddress
import logging
import subprocess
from typing import Any

import docker

logger = logging.getLogger(__name__)


def ensure_scoped_network(network_config: dict[str, Any]) -> str:
    """
    Ensure a Docker network exists matching the scoped config.

    Returns the network name.
    """
    name = network_config.get("name", "smsly-net")
    driver = network_config.get("driver", "bridge")
    internal = network_config.get("internal", False)
    enable_ipv6 = network_config.get("enable_ipv6", False)
    subnet = network_config.get("subnet", "")

    client = docker.from_env()
    try:
        client.networks.get(name)
        return name
    except docker.errors.NotFound:
        pass

    create_kwargs: dict[str, Any] = {
        "name": name,
        "driver": driver,
        "internal": internal,
        "enable_ipv6": enable_ipv6,
    }

    if subnet:
        try:
            ipaddress.IPv4Network(subnet)
            create_kwargs["ipam"] = docker.types.IPAMConfig(
                driver="default",
                pool_configs=[docker.types.IPAMPool(subnet=subnet)],
            )
        except ValueError:
            logger.warning("Invalid subnet %r for network %s, skipping IPAM", subnet, name)

    logger.info("Creating scoped Docker network: %s (driver=%s, isolated=%s)", name, driver, network_config.get("isolated"))
    net = client.networks.create(**create_kwargs)
    return net.name


def _get_bridge_interface_name(network_name: str) -> str | None:
    """Return the Docker bridge interface name (e.g. ``br-abc123...``) for *network_name*.

    Docker generates the bridge name as ``br-<network_id[:12]>`` where
    ``network_id`` is the network's full UUID. We must resolve it via the
    Docker API — deriving it from the user-supplied network_name caused
    iptables rules to collide for any two networks whose first 12 characters
    matched (e.g. two services whose short IDs share a prefix).
    """
    try:
        client = docker.from_env()
        net = client.networks.get(network_name)
        net_id = net.attrs.get("Id")
        if not net_id:
            logger.warning("Docker network %s has no Id attr; cannot resolve bridge", network_name)
            return None
        return f"br-{net_id.replace('-', '')[:12]}"
    except docker.errors.NotFound:
        logger.warning("Docker network %s not found when resolving bridge", network_name)
        return None
    except Exception:
        logger.exception("Failed to resolve bridge interface for %s", network_name)
        return None


def apply_egress_restrictions(network_name: str, allowed_egress_networks: list[str]) -> None:
    """
    Apply iptables rules to restrict egress from a Docker network.

    Uses the DOCKER-USER chain so rules survive Docker restarts.

    If ``allowed_egress_networks`` contains ``0.0.0.0/0`` the function
    treats the request as unrestricted and applies cross-bridge isolation
    (containers on different bridges cannot reach each other via host
    routing) while allowing internet outbound, DNS, and same-bridge addon
    traffic.

    If specific CIDRs are given, only those destinations are allowed (plus
    DNS), and everything else is dropped.  Cloud metadata is always blocked.

    Insertion order is important — the function inserts rules from
    bottom-of-chain to top-of-chain so the final evaluation order is:

      1. ESTABLISHED,RELATED → RETURN   (response traffic)
      2. Outbound via eth/enp/wl+        (internet access)
      3. Same-bridge (addon)             (local addon traffic)
      4. DNS                             (name resolution)
      5. Cloud metadata DROP             (IAM credential guard)
      6. Cross-bridge DROP               (inter-container isolation)
      7. Catch-all DROP                  (default deny)
    """
    if not allowed_egress_networks:
        return

    # Validate every CIDR up front; drop invalid entries so a single typo
    # cannot silently disable the whole chain.
    valid_cidrs: list[str] = []
    for cidr in allowed_egress_networks:
        try:
            net = ipaddress.IPv4Network(cidr)
        except ValueError:
            logger.warning("Invalid egress CIDR: %s", cidr)
            continue
        valid_cidrs.append(str(net))

    if not valid_cidrs:
        return

    bridge_iface = _get_bridge_interface_name(network_name)
    if bridge_iface is None:
        # Already logged in the helper; bail rather than guess the iface name.
        return

    def _run(args: list[str]) -> None:
        try:
            result = subprocess.run(args, capture_output=True, text=True, check=False)
            if result.returncode != 0:
                logger.error(
                    "iptables command failed (rc=%d): %s | stderr=%s",
                    result.returncode, " ".join(args), result.stderr.strip(),
                )
        except FileNotFoundError:
            logger.error("iptables binary not found; cannot apply egress restrictions")

    is_unrestricted = any(ipaddress.IPv4Network(c) == ipaddress.IPv4Network("0.0.0.0/0") for c in valid_cidrs)

    # 1. DROP first (catch-all, ends up at the bottom of the final chain).
    _run(["iptables", "-I", "DOCKER-USER", "-i", bridge_iface, "-j", "DROP"])

    # 2. RETURN each CIDR (or 0.0.0.0/0 with cross-bridge isolation).
    if is_unrestricted:
        logger.info(
            "apply_egress_restrictions: %s unrestricted — isolating from other bridges, "
            "allowing internet + same-bridge addon traffic",
            network_name,
        )
        # Cross-bridge traffic DROP — containers on different bridges cannot
        # communicate via host routing (this is the primary isolation mechanism).
        _run(["iptables", "-I", "DOCKER-USER", "-i", bridge_iface, "-o", "br-+", "-j", "DROP"])
        # Same-bridge RETURN — addon containers on this bridge are reachable.
        _run(["iptables", "-I", "DOCKER-USER", "-i", bridge_iface, "-o", bridge_iface, "-j", "RETURN"])
        # Internet outbound via physical interfaces.
        for phys in ("wl+", "enp+", "eth+"):
            _run(["iptables", "-I", "DOCKER-USER", "-i", bridge_iface, "-o", phys, "-j", "RETURN"])
        # ESTABLISHED,RELATED must come BEFORE the cross-bridge DROP so that
        # responses (e.g. from Traefik or internet) reach the container.
        _run(["iptables", "-I", "DOCKER-USER", "-i", bridge_iface, "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "RETURN"])
    else:
        for cidr in valid_cidrs:
            _run([
                "iptables", "-I", "DOCKER-USER", "-i", bridge_iface,
                "-d", cidr, "-j", "RETURN",
            ])

    # 3. Always DROP cloud metadata service (169.254.169.254/32) to prevent IAM theft.
    _run(["iptables", "-I", "DOCKER-USER", "-i", bridge_iface, "-d", "169.254.169.254/32", "-j", "DROP"])

    # 4. RETURN DNS last so it sits at the top of the chain — never shadowed.
    _run([
        "iptables", "-I", "DOCKER-USER", "-i", bridge_iface,
        "-p", "udp", "--dport", "53", "-j", "RETURN",
    ])
