"""Internal wake-on-request for sablier scale-to-zero.

External traffic wakes sleeping containers because it enters through a
Traefik router carrying the sablier middleware (Caddy -> Traefik :80 ->
router chain). Internal traffic never touches such a router:

* direct container-to-container dials over Docker DNS (service A dials
  ``http://service-b:8000`` on a shared/scoped bridge) bypass Traefik
  entirely — and Docker DNS cannot even resolve a stopped container;
* mTLS mesh traffic flows sidecar-to-sidecar (Envoy), also bypassing
  Traefik, and the provider Envoy requires client certificates that
  Traefik cannot present — so mesh dials can NEVER be routed through a
  wake router.

This module closes the first gap and fail-closeds the second:

1. Every sablier-enabled service gets an INTERNAL Traefik router
   (``Host(`<name>.internal`)``) carrying the same sablier blocking +
   wakeretry chain. Traefik already rides every scoped bridge
   (``ensure_scoped_network`` attaches the edge proxies), so it only
   needs a per-service DNS alias (``<name>.internal``) on those
   networks — added idempotently at spawn.
2. Local env overlays rewrite plain-``http`` sibling-service URLs to
   the ``.internal`` name on Traefik's entrypoint port, so internal
   dials flow Traefik -> wake -> proxy with no app changes.
3. Mesh-served apps (Envoy sidecar attached) are EXCLUDED from sleep
   enrollment: no sablier labels, sleeper skips them. There is no
   in-band wake path for sidecar-to-sidecar dials, so sleeping such a
   provider would strand mesh callers with no recovery. Staying up is
   the fail-closed direction (availability over RAM savings).

``.internal`` never leaves the host (Docker DNS only): no Caddy, TLS,
or public-DNS involvement.
"""

import logging

logger = logging.getLogger(__name__)

INTERNAL_WAKE_SUFFIX = ".internal"

#: Entrypoint internal wake dials must arrive on (Traefik ``web``).
INTERNAL_WAKE_PORT = 80

#: Traefik container candidates (mirror of network_scope edge list).
TRAEFIK_ALIAS_CANDIDATES = (
    "smsly-hosting-traefik-1",
    "traefik",
)


def internal_wake_host(service_name: str) -> str:
    """Stable internal hostname for a service (Docker-DNS safe)."""
    base = str(service_name or "").strip().lower().rstrip(".")
    return f"{base}{INTERNAL_WAKE_SUFFIX}" if base else ""


def service_has_mesh_sidecar(service) -> bool:
    """True when an Envoy sidecar is attached (mesh serves this app).

    Fail-open False: when the lookup itself errors we keep current
    behavior (sablier labels as before) rather than changing
    enrollment on a failed read.
    """
    try:
        from apps.mtls.models import MtlsConfig
        svc_id = getattr(service, "id", None)
        if not svc_id:
            return False
        return MtlsConfig.objects.filter(
            service_id=svc_id, sidecar_enabled=True,
        ).exists()
    except Exception:
        return False


def ensure_internal_wake_alias(service_name: str, network_name: str) -> bool:
    """Give Traefik a ``<service>.internal`` DNS alias on a network.

    Makes the internal wake hostname resolvable to Traefik from every
    container on that network. Idempotent: inspects first, connects
    (or disconnect/reconnect when the endpoint exists without the
    alias — Docker has no in-place alias update). Best-effort: never
    raises, returns whether the alias resolves afterwards.
    """
    import subprocess

    host = internal_wake_host(service_name)
    net = str(network_name or "").strip()
    if not host or not net:
        return False
    try:
        import docker as _docker
        client = _docker.from_env(timeout=10)
    except Exception as exc:
        logger.debug("internal-wake alias skipped (no docker): %s", exc)
        return False
    target = None
    for candidate in TRAEFIK_ALIAS_CANDIDATES:
        try:
            target = client.containers.get(candidate)
            break
        except Exception:
            continue
    if target is None:
        logger.debug("internal-wake alias skipped (no traefik container)")
        return False
    try:
        target.reload()
        nets = (getattr(target, "attrs", None) or {}).get(
            "NetworkSettings", {}).get("Networks", {}) or {}
        entry = nets.get(net, {}) or {}
        aliases = list(entry.get("Aliases") or [])
        if host in aliases:
            return True
        if net in nets:
            # Endpoint exists without our alias: recreate it with the
            # alias added (preserving existing aliases).
            try:
                client.networks.get(net).disconnect(target, force=True)
            except Exception as exc:
                logger.debug("internal-wake disconnect skipped: %s", exc)
                return False
            aliases.append(host)
        else:
            aliases = [host]
        client.networks.get(net).connect(
            target, aliases=list(dict.fromkeys(aliases)),
        )
        logger.info("internal-wake: alias %s -> traefik on %s", host, net)
        return True
    except Exception as exc:
        logger.debug("internal-wake alias failed for %s on %s: %s", host, net, exc)
        return False


def _local_sablier_siblings(exclude_service) -> set[str]:
    """Names of sablier-enabled services on THIS host (wake targets)."""
    names: set[str] = set()
    try:
        from apps.deployments.models import Service
        rows = Service.objects.filter(sablier_enabled=True).only(
            "id", "name", "server", "active_target_type",
        )
        exclude_id = str(getattr(exclude_service, "id", "") or "")
        for svc in rows:
            if exclude_id and str(getattr(svc, "id", "")) == exclude_id:
                continue
            try:
                target = str(getattr(svc, "active_target_type", "") or "").lower()
                server = getattr(svc, "server", None)
                if target in ("remote", "lite_agent") or (
                    server is not None and not getattr(server, "is_primary", True)
                ):
                    continue
            except Exception:
                continue
            name = str(getattr(svc, "name", "") or "").strip().lower()
            if name:
                names.add(name)
    except Exception:
        pass
    return names


def rewrite_env_for_internal_wake(service, env: dict) -> dict:
    """Overlay: point sibling-service http(s) URLs at the internal wake router.

    For every http OR https URL value whose hostname exactly matches a
    sablier-enabled local sibling, rewrite host to ``<sibling>.internal``
    as plain http on Traefik's entrypoint port. https is included
    deliberately: nothing serves TLS on app ports inside the platform
    (Caddy terminates at the edge), so an ``https://sibling:port``
    dial can never succeed today — and ``.internal`` names never leave
    the Docker bridge, the same trust zone as the plaintext
    service-to-addon traffic already flowing there. Mesh-rewritten IPs,
    public domains, and non-siblings pass through untouched. Never
    mutates rows — returns an overlay dict (empty when nothing
    applies). Never raises.
    """
    try:
        base = dict(env or {})
    except Exception:
        return {}
    try:
        from urllib.parse import urlparse as _up, urlunparse as _uu
        siblings = _local_sablier_siblings(service)
        if not siblings:
            return {}
        overlay: dict[str, str] = {}
        rewritten_keys: list[str] = []
        for key, val in base.items():
            if not isinstance(val, str) or "://" not in val:
                continue
            text = val.strip()
            lowered = text.lower()
            if not (lowered.startswith("http://") or lowered.startswith("https://")):
                continue
            try:
                parsed = _up(text)
            except Exception:
                continue
            host = str(parsed.hostname or "").strip().lower().rstrip(".")
            if not host or host not in siblings:
                continue
            if host == f"{str(getattr(service, 'name', '') or '').strip().lower()}":
                continue
            new_host = internal_wake_host(host)
            try:
                port = parsed.port
            except Exception:
                continue
            # Entry point is always plain http :80; the service LB port
            # lives on the Traefik service definition. Preserve userinfo.
            userinfo = ""
            if parsed.username:
                userinfo = parsed.username
                if parsed.password:
                    userinfo += f":{parsed.password}"
                userinfo += "@"
            if port is None:
                new_netloc = f"{userinfo}{new_host}" if not userinfo else f"{userinfo}{new_host}"
            else:
                new_netloc = f"{userinfo}{new_host}:{INTERNAL_WAKE_PORT}"
            overlay[key] = _uu(parsed._replace(scheme="http", netloc=new_netloc))
            rewritten_keys.append(str(key))
        if rewritten_keys:
            logger.info(
                "internal-wake: rewrote %d env URL(s) to .internal: %s",
                len(rewritten_keys), ",".join(sorted(rewritten_keys)[:8]),
            )
        return overlay
    except Exception as exc:
        logger.debug("internal-wake rewrite skipped: %s", exc)
        return {}
