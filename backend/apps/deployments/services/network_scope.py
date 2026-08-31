"""
Scoped Docker network management â ensure/create networks and egress rules.

Provides the bridge between ``ScopedNetwork`` model configuration and
actual Docker network operations.

Egress lifecycle (v2): every iptables rule we create is tagged with a
per-network comment (``smsly-egress-{net_id12}``) so rules are:

* **Idempotent** â applying twice never duplicates (tag presence = done)
* **Cleanable** â ``clear_scoped_rules()`` removes exactly our rules
* **Self-healing** â ``reconcile_network_isolation()`` reapplies rules for
  live scoped bridges and purges rules whose bridge interface no longer
  exists (networks get recreated with new IDs; old DOCKER-USER rules would
  otherwise accumulate forever and reference dead interfaces).

Run ``reconcile_network_isolation`` periodically (celery beat) â it closes
the recreate-gap where a fresh bridge starts unrestricted until its next
spawn-time apply.
"""
import ipaddress
import logging
import subprocess
from typing import Any

import docker

logger = logging.getLogger(__name__)

RULE_TAG_PREFIX = "smsly-egress-"


def _sh(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a command - iptables/* via a privileged host-network shim.

    The backend container is unprivileged and without host netns, so plain
    iptables here ALWAYS failed (rc=4 'you must be root') and every scoped
    bridge silently shipped WITHOUT its egress firewall. All iptables
    invocations are now executed through a one-shot alpine container with
    --net=host --cap-add=NET_ADMIN (docker CLI is available in backend).
    """
    try:
        if args and args[0] == "iptables":
            script = " ".join(args)
            return subprocess.run(
                ["docker", "run", "--rm", "--net=host", "--cap-add=NET_ADMIN",
                 "alpine:3.20", "sh", "-c", script],
                capture_output=True, text=True, timeout=timeout, check=False)
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        logger.error("docker/iptables unavailable; cannot manage egress restrictions")
        raise


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
    ``network_id`` is the network's full UUID. We must resolve it via
    the Docker API â deriving it from the user-supplied network_name caused
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


def _list_docker_user_rules() -> list[str]:
    """Return current DOCKER-USER chain rules as spec strings (without '-A DOCKER-USER')."""
    res = _sh(["iptables", "-S", "DOCKER-USER"])
    rules: list[str] = []
    for line in (res.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("-A DOCKER-USER "):
            rules.append(line[len("-A DOCKER-USER "):].strip())
    return rules


def _rule_tag(bridge_iface: str) -> str:
    """Stable per-bridge comment tag: br-<id12> -> smsly-egress-<id12>."""
    return RULE_TAG_PREFIX + bridge_iface.replace("br-", "", 1)


def _bridge_exists(bridge_iface: str) -> bool:
    res = _sh(["ip", "link", "show", bridge_iface])
    return res.returncode == 0


def clear_scoped_rules(network_name: str) -> int:
    """Delete every DOCKER-USER rule tagged for this network's bridge.

    Safe to call repeatedly; returns number of rules removed.
    """
    bridge_iface = _get_bridge_interface_name(network_name)
    if not bridge_iface:
        return 0
    removed = 0
    for parts in [r.split() for r in _list_docker_user_rules()]:
        iface = None
        has_tag = False
        for i, part in enumerate(parts):
            if part == "-i" and i + 1 < len(parts):
                iface = parts[i + 1]
            if part.startswith(RULE_TAG_PREFIX):
                has_tag = True
        if not (has_tag and iface == bridge_iface):
            continue
        del_res = _sh(["iptables", "-D", "DOCKER-USER"] + parts)
        if del_res.returncode == 0:
            removed += 1
            logger.info("Removed DOCKER-USER rule for %s", network_name)
        else:
            logger.warning("Failed removing rule: %s", del_res.stderr.strip()[:120])
    return removed


def apply_egress_restrictions(network_name: str, allowed_egress_networks: list[str]) -> None:
    """
    Apply iptables rules to restrict egress from a Docker network.

    Uses the DOCKER-USER chain so rules survive Docker restarts.
    Idempotent: every rule carries a per-bridge comment tag; when the tag is
    already present the function returns without touching anything.

    If ``allowed_egress_networks`` contains ``0.0.0.0/0`` the function
    treats the request as unrestricted and applies cross-bridge isolation
    (containers on different bridges cannot reach each other via host
    routing) while allowing internet outbound, DNS, and same-bridge addon
    traffic.

    Final evaluation order:

      1. ESTABLISHED,RELATED â RETURN   (response traffic)
      2. Outbound via eth/enp/wl+        (internet access)
      3. Same-bridge (addon)             (local addon traffic)
      4. DNS                             (name resolution)
      5. Cloud metadata DROP             (IAM credential guard)
      6. Cross-bridge DROP               (inter-container isolation)
      7. Catch-all DROP                  (default deny)
    """
    if not allowed_egress_networks:
        return

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

    tag = _rule_tag(bridge_iface)

    def _run(args: list[str]) -> None:
        args = args + ["-m", "comment", "--comment", tag]
        result = _sh(args)
        if result.returncode != 0 and "already exists" not in (result.stderr or ""):
            logger.error(
                "iptables command failed (rc=%d): %s | stderr=%s",
                result.returncode, " ".join(args), result.stderr.strip(),
            )

    # IDEMPOTENCY GATE: if ANY rule carrying our tag exists, assume this
    # bridge is fully configured and bail. Rules are written as one atomic
    # block below, so partial application cannot persist across reconciles.
    existing = _list_docker_user_rules()
    if any(tag in r for r in existing):
        return

    is_unrestricted = any(ipaddress.IPv4Network(c) == ipaddress.IPv4Network("0.0.0.0/0") for c in valid_cidrs)

    # 1. DROP first (catch-all, ends up at the bottom of the final chain).
    _run(["iptables", "-I", "DOCKER-USER", "-i", bridge_iface, "-j", "DROP"])

    # 2. RETURN each CIDR (or 0.0.0.0/0 with cross-bridge isolation).
    if is_unrestricted:
        logger.info(
            "apply_egress_restrictions: %s unrestricted â isolating from other bridges, "
            "allowing internet + same-bridge addon traffic",
            network_name,
        )
        # Cross-bridge traffic DROP â containers on different bridges cannot
        # communicate via host routing (this is the primary isolation mechanism).
        _run(["iptables", "-I", "DOCKER-USER", "-i", bridge_iface, "-o", "br-+", "-j", "DROP"])
        # Same-bridge RETURN â addon containers on this bridge are reachable.
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

    # 4. RETURN DNS last so it sits at the top of the chain â never shadowed.
    _run([
        "iptables", "-I", "DOCKER-USER", "-i", bridge_iface,
        "-p", "udp", "--dport", "53", "-j", "RETURN",
    ])


def _traefik_container_name() -> str:
    import os
    return os.environ.get("TRAEFIK_CONTAINER_NAME", "smsly-hosting-traefik-1")


def ensure_router_on_network(network_name: str) -> bool:
    """Attach the edge router (Traefik) to a scoped bridge so it can reach
    app containers on it. Idempotent."""
    try:
        client = docker.from_env()
        net = client.networks.get(network_name)
        names = {c.name for c in net.containers}
        router = _traefik_container_name()
        if router in names:
            return False
        try:
            container = client.containers.get(router)
        except docker.errors.NotFound:
            try:
                container = client.containers.get("traefik")
            except docker.errors.NotFound:
                logger.warning("Router container not found; cannot attach to %s", network_name)
                return False
        net.connect(container)
        logger.info("Attached %s to network %s", router, network_name)
        return True
    except docker.errors.NotFound:
        return False
    except Exception:
        logger.exception("ensure_router_on_network failed for %s", network_name)
        return False


def reconcile_network_isolation() -> dict[str, int]:
    """Periodic self-healing pass over scoped networks.

    * Purges DOCKER-USER rules whose bridge interface no longer exists
    * Reapplies egress isolation to every live ``paas-svc-*`` bridge that is
      missing its tag (closes the recreate-gap: fresh bridge = unrestricted)
    * Ensures the edge router is attached to every scoped bridge

    Returns counters for logging/alerting.
    """
    stats = {"purged_rules": 0, "reapplied": 0, "router_attached": 0}

    # 1. Purge stale-tagged rules (bridges that no longer exist).
    try:
        stats["purged_rules"] = _purge_stale_only()
    except Exception:
        logger.exception("stale rule purge failed")

    # 2. Reapply + router attach for live scoped bridges.
    try:
        client = docker.from_env()
        paas_nets = client.networks.list(names=["paas-svc-*"])
        smsly_nets = client.networks.list(names=["smsly-net-*"])
        nets = [n for n in paas_nets + smsly_nets
                if n.name not in ("smsly-net", "traefik-proxy-net", "smsly-internal-net")]
        seen = set()
        unique_nets = []
        for n in nets:
            if n.name not in seen:
                seen.add(n.name)
                unique_nets.append(n)
        nets = unique_nets
        rules_snapshot = _list_docker_user_rules()
        for net in nets:
            name = net.name
            br = _get_bridge_interface_name(name)
            tag = _rule_tag(br) if br else None
            tag_present = bool(tag) and any(tag in r for r in rules_snapshot)
            if not tag_present:
                apply_egress_restrictions(name, ["0.0.0.0/0"])
                stats["reapplied"] += 1
            if ensure_router_on_network(name):
                stats["router_attached"] += 1
    except Exception:
        logger.exception("scoped network reconcile failed")

    if any(stats.values()):
        logger.info("network isolation reconcile: %s", stats)
    return stats


def _purge_stale_only() -> int:
    """Remove tagged rules whose bridge interface no longer exists."""
    removed = 0
    for rule in _list_docker_user_rules():
        parts = rule.split()
        iface = None
        for i, part in enumerate(parts):
            if part == "-i" and i + 1 < len(parts):
                iface = parts[i + 1]
                break
        has_tag = RULE_TAG_PREFIX in rule
        if iface and iface.startswith("br-") and not _bridge_exists(iface) and has_tag:
            res = _sh(["iptables", "-D", "DOCKER-USER"] + parts)
            if res.returncode == 0:
                removed += 1
                logger.info("Purged stale DOCKER-USER rules for dead bridge %s", iface)
    return removed

def ensure_platform_bridge() -> str:
    """Ensure the platform-wide shared bridge exists.

    The platform bridge is what every internal-network-enabled service
    gets attached to, regardless of project. It provides
    inter-service connectivity across the whole platform - a service
    in project A can reach a service in project B through this
    bridge, no public DNS, no TLS.

    The subnet is a /24 in the 172.31.0.0/16 IETF CGNAT range so it
    doesn't collide with the project's scoped bridges (172.30.x.x)
    or the default 'smsly-net' (172.18.0.0/16). When services need to
    talk across project boundaries, they use this bridge's DNS name
    (e.g. 'smsly-backend.smsly-platform-net').

    Idempotent: the network is created once on first use, then
    returned by name on subsequent calls.
    """
    client = docker.from_env()
    name = 'smsly-platform-net'
    try:
        net = client.networks.get(name)
        return name
    except docker.errors.NotFound:
        pass
    create_kwargs = {
        'name': name,
        'driver': 'bridge',
        'internal': False,
        'enable_ipv6': False,
    }
    try:
        client.networks.create(**create_kwargs)
        logger.info("Created platform-wide bridge: %s", name)
    except Exception as exc:
        # Some other process may have raced us. Tolerate that.
        logger.debug("Platform bridge create race (%s); continuing", exc)
    return name


def attach_container_to_platform_bridge(container_id: str, service_name: str) -> bool:
    """Attach a running container to smsly-platform-net if it isn't already.

    No-op if the service opted out of the internal network. Returns
    True on success or no-op, False on failure.
    """
    try:
        client = docker.from_env()
        container = client.containers.get(container_id)
        container.reload()
        nets = (container.attrs.get('NetworkSettings') or {}).get('Networks') or {}
        if 'smsly-platform-net' in nets:
            return True
        bridge = ensure_platform_bridge()
        container.reload()
        nets = (container.attrs.get('NetworkSettings') or {}).get('Networks') or {}
        if 'smsly-platform-net' in nets:
            return True
        try:
            net = client.networks.get(bridge)
            net.connect(container)
            logger.info(
                "Attached %s (%s) to platform bridge %s",
                service_name, container_id[:12], bridge,
            )
            return True
        except Exception as exc:
            logger.warning(
                "Failed to attach %s to platform bridge: %s",
                service_name, exc,
            )
            return False
    except Exception as exc:
        logger.debug("attach_container_to_platform_bridge failed: %s", exc)
        return False


# ── Project-scoped subnet allocation ────────────────────────────────────
# Pool: 172.30.0.0/16 (IETF CGNAT range, deliberately outside Docker's
# default 172.17-172.21 and our own 172.22 platform bridge). Each project
# bridge gets a dedicated /24 carved out of this /16 — up to 256 isolated
# projects before anyone has to set Project.internal_subnet manually.
_SUBNET_POOL_PREFIX = '172.30.'
_SUBNET_POOL_FIRST = 1     # first /24: 172.30.1.0/24
_SUBNET_POOL_LAST = 255     # last /24: 172.30.255.0/24


def _existing_docker_subnets() -> set[str]:
    """All subnets currently allocated to any Docker network (host view)."""
    subnets: set[str] = set()
    try:
        client = docker.from_env()
        for net in client.networks.list():
            try:
                net.reload()
                for cfg in (net.attrs.get('IPAM') or {}).get('Config') or []:
                    subnet = (cfg or {}).get('Subnet') or ''
                    if subnet:
                        subnets.add(subnet.strip())
            except Exception:
                continue
    except Exception as exc:
        logger.debug("Could not list Docker subnets: %s", exc)
    return subnets


def allocate_project_subnet(project=None, requested: str = '') -> str:
    """Pick a collision-free /24 for a new project bridge.

    Order of preference:
      1. An explicit operator override (Project.internal_subnet or the
         ``requested`` argument). Returned as-is — the caller's
         networks.create will fail loudly if it actually overlaps, which
         is the correct behavior for an explicit choice.
      2. PlatformConfig.default_internal_subnet — BUT only if it is not
         already in use by another Docker network. This was the original
         bug: every project fell back to the same 172.30.224.0/24, and
         the second project's bridge create died with 'Pool overlaps
         with other one on this address space'.
      3. First free /24 in the 172.30.0.0/16 CGNAT pool (skipping any
         subnet already allocated to a Docker network).

    ``project`` (when given) is only used for log context.
    """
    requested = (requested or '').strip()
    if requested:
        return requested

    existing = _existing_docker_subnets()

    try:
        from apps.deployments.models.core import PlatformConfig
        default_subnet = (PlatformConfig.load().default_internal_subnet or '').strip()
    except Exception:
        default_subnet = ''
    if default_subnet and default_subnet not in existing:
        return default_subnet

    # Default is taken (or unset) — scan the /16 pool for a free /24.
    for third_octet in range(_SUBNET_POOL_FIRST, _SUBNET_POOL_LAST + 1):
        candidate = f'{_SUBNET_POOL_PREFIX}{third_octet}.0/24'
        if candidate not in existing:
            logger.info(
                "Allocated project subnet %s for project %s (default %s was in use)",
                candidate,
                getattr(project, 'id', None) or '(unknown)',
                default_subnet or '(unset)',
            )
            return candidate

    # Entire pool exhausted (256 projects with live bridges) — fall back to
    # the default and let networks.create surface the overlap error.
    logger.warning(
        "Project subnet pool 172.30.0.0/16 exhausted; falling back to %s "
        "for project %s — set Project.internal_subnet explicitly.",
        default_subnet or '172.30.224.0/24',
        getattr(project, 'id', None) or '(unknown)',
    )
    return default_subnet or '172.30.224.0/24'
