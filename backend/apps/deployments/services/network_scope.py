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


def apply_egress_restrictions(network_name: str, allowed_egress_networks: list[str]) -> None:
    """
    Apply iptables rules to restrict egress from a Docker network.

    Uses the DOCKER-USER chain so rules survive Docker restarts.
    """
    if not allowed_egress_networks:
        return

    for cidr in allowed_egress_networks:
        try:
            ipaddress.IPv4Network(cidr)
        except ValueError:
            logger.warning("Invalid egress CIDR: %s", cidr)
            continue

    # Allow DNS (port 53) always
    subprocess.run(
        ["iptables", "-I", "DOCKER-USER", "-i", f"br-{network_name[:12]}",
         "-p", "udp", "--dport", "53", "-j", "ACCEPT"],
        capture_output=True,
    )

    # Default drop for this bridge
    subprocess.run(
        ["iptables", "-I", "DOCKER-USER", "-i", f"br-{network_name[:12]}",
         "-j", "DROP"],
        capture_output=True,
    )

    # Allow each CIDR
    for cidr in allowed_egress_networks:
        subprocess.run(
            ["iptables", "-I", "DOCKER-USER", "-i", f"br-{network_name[:12]}",
             "-d", cidr, "-j", "ACCEPT"],
            capture_output=True,
        )
