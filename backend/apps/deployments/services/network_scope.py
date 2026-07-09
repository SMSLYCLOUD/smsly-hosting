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

    If any entry in ``allowed_egress_networks`` is ``0.0.0.0/0`` the function
    treats the request as "no restriction" and returns without touching
    iptables — there is nothing meaningful to allow beyond ``0.0.0.0/0``.

    Insertion order matters because ``iptables -I`` prepends:

      1. DROP for the bridge (catch-all)
      2. RETURN for each CIDR in the allowlist
      3. RETURN for DNS (port 53)

    Each subsequent insertion moves to the top of the chain, so by the time
    the loop finishes the chain reads top-down as: DNS-return, cidr-return,
    ... cidr-return, DROP. Specific RETURNs win and pass control back to FORWARD,
    DROP catches everything else, DNS is never shadowed.
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

    # 2. RETURN each CIDR (or 0.0.0.0/0 with RFC1918 blocks).
    if is_unrestricted:
        logger.info(
            "apply_egress_restrictions: %s requested unrestricted egress (0.0.0.0/0); applying RFC1918 and metadata blocking",
            network_name,
        )
        _run(["iptables", "-I", "DOCKER-USER", "-i", bridge_iface, "-d", "0.0.0.0/0", "-j", "RETURN"])
        for drop_cidr in ("192.168.0.0/16", "172.16.0.0/12", "10.0.0.0/8"):
            _run(["iptables", "-I", "DOCKER-USER", "-i", bridge_iface, "-d", drop_cidr, "-j", "DROP"])
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
