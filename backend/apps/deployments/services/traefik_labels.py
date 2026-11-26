# pylint: disable=line-too-long,too-many-arguments,R0917
"""Traefik Labels module."""
"""
Traefik Label Helpers for SMSLY Hosting.

Generates Docker container labels for Traefik routing configuration.
Enables automatic service discovery, SSL termination, and custom domains.
"""


import os
import re


# Sablier scale-to-zero endpoint (docker DNS; the sablier service shares a
# network with traefik — see docker-compose.prod.yml).
SABLIER_URL = "http://sablier:10000"
_SABLIER_DURATION_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")
_SABLIER_MIN_SECONDS = 30
_SABLIER_MAX_SECONDS = 7 * 86400


def normalize_sablier_session(
    raw: object, *, default: str = "10m", strict: bool = False,
) -> str:
    """Validate and normalize a Go-style Sablier ``sessionDuration`` string.

    Bare positive integers (e.g. ``"10"``) normalize to minutes (``"10m"``).
    Invalid or out-of-range durations raise ``ValueError`` when ``strict=True``
    and fall back to *default* when ``strict=False``.
    """
    text = str(raw or "").strip().lower()
    if not text:
        if strict:
            raise ValueError("Idle session duration cannot be empty.")
        return default
    if text.isdigit():
        text = f"{int(text)}m"
    m = _SABLIER_DURATION_RE.fullmatch(text)
    if not m or not any(m.groups()):
        if strict:
            raise ValueError(
                f"Invalid sablier session duration '{raw}'. Use e.g. '5m', '10m', '1h', or '1h30m'."
            )
        return default
    hours = int(m.group(1) or 0)
    mins = int(m.group(2) or 0)
    secs = int(m.group(3) or 0)
    total = hours * 3600 + mins * 60 + secs
    if total < _SABLIER_MIN_SECONDS or total > _SABLIER_MAX_SECONDS:
        if strict:
            raise ValueError(
                "sablier_session must be between 30s and 168h (7d)."
            )
        return default
    return text


def sablier_middleware_ref(router_name: str) -> str:
    """Router-chain entry for a service's Sablier middleware."""
    return f"{router_name}-sablier"


def sablier_retry_ref(router_name: str) -> str:
    """Router-chain entry for a service's wake-retry middleware."""
    return f"{router_name}-wakeretry"


_ROUTER_RULE_KEY_RE = re.compile(r"^traefik\.http\.routers\.([^.]+)\.rule$")


def service_supports_sablier(service=None) -> tuple[bool, str]:
    """Return (allowed, reason) for Sablier scale-to-zero enrollment.

    Lite Agents (agent-lite) do not run the Sablier daemon or load Traefik's
    sablier plugin; attaching sablier plugin middlewares on a lite agent
    drops the entire HTTP router (503). Mesh-sidecar services are also
    excluded because sidecar-to-sidecar mTLS calls bypass Traefik.
    """
    if str(os.environ.get("MODE_AGENT_LITE", "") or "").strip().lower() == "true":
        return False, "agent-lite host does not run Sablier"
    if service is None:
        return True, ""
    if str(getattr(service, "active_target_type", "") or "").strip().lower() == "lite_agent":
        return False, "lite_agent target does not run Sablier"
    server = getattr(service, "server", None)
    if server is None:
        try:
            from apps.deployments.services.caddy_manager.config_generation import (
                _resolve_effective_server,
            )
            server = _resolve_effective_server(service)
        except Exception:
            server = None
    if server is not None and getattr(server, "is_lite_agent", False) is True:
        return False, "assigned server is a lite agent without Sablier"
    try:
        from apps.deployments.services.internal_wake import service_has_mesh_sidecar
        if service_has_mesh_sidecar(service):
            return False, "mesh sidecar attached"
    except Exception:
        pass
    return True, ""


def sync_sablier_labels_on_dict(labels: dict[str, str], service) -> dict[str, str]:
    """Synchronize live container Traefik/Sablier labels to *service* DB state.

    Mutates and returns *labels* so toggling ``sablier_enabled`` or updating
    ``sablier_session`` takes effect on recreate without requiring a full rebuild.
    """
    from apps.deployments.services.internal_wake import internal_wake_host

    svc_name = str(getattr(service, "name", "") or "").strip()
    routers: list[str] = []
    for key in list(labels.keys()):
        if not isinstance(key, str):
            continue
        m = _ROUTER_RULE_KEY_RE.match(key)
        if not m:
            continue
        rname = m.group(1)
        if rname.endswith("-internal") or rname.endswith("-staging"):
            continue
        if rname not in routers:
            routers.append(rname)
    if not routers and str(labels.get("traefik.enable", "") or "").strip().lower() == "true" and svc_name:
        routers.append(svc_name.replace(".", "-").replace("_", "-"))

    ok_sablier, _ = service_supports_sablier(service)
    want_sablier = (getattr(service, "sablier_enabled", False) is True) and ok_sablier

    labels.pop("sablier.enable", None)
    labels.pop("sablier.group", None)
    if not want_sablier:
        labels.pop("traefik.docker.allownonrunning", None)

    for rtr in routers:
        sab_prefix = f"traefik.http.middlewares.{rtr}-sablier."
        retry_prefix = f"traefik.http.middlewares.{rtr}-wakeretry."
        internal_prefix = f"traefik.http.routers.{rtr}-internal."
        internal_mw_sab = f"traefik.http.middlewares.{rtr}-internal-sablier."
        internal_mw_retry = f"traefik.http.middlewares.{rtr}-internal-wakeretry."
        for k in [
            k for k in labels
            if isinstance(k, str) and (
                k.startswith(sab_prefix)
                or k.startswith(retry_prefix)
                or k.startswith(internal_prefix)
                or k.startswith(internal_mw_sab)
                or k.startswith(internal_mw_retry)
            )
        ]:
            labels.pop(k, None)

        mw_key = f"traefik.http.routers.{rtr}.middlewares"
        if mw_key in labels:
            strip_set = {
                sablier_retry_ref(rtr),
                f"{sablier_retry_ref(rtr)}@docker",
                sablier_middleware_ref(rtr),
                f"{sablier_middleware_ref(rtr)}@docker",
            }
            cleaned = [
                item.strip()
                for item in str(labels.get(mw_key, "") or "").split(",")
                if item.strip() and item.strip() not in strip_set
            ]
            if cleaned:
                labels[mw_key] = ",".join(cleaned)
            else:
                labels.pop(mw_key, None)

    if want_sablier and svc_name:
        session = normalize_sablier_session(
            getattr(service, "sablier_session", "") or "10m",
            default="10m",
        )
        labels["sablier.enable"] = "true"
        labels["sablier.group"] = svc_name
        labels["traefik.docker.allownonrunning"] = "true"
        for rtr in routers:
            service_ref = str(
                labels.get(f"traefik.http.routers.{rtr}.service")
                or (
                    f"{rtr}-service"
                    if f"traefik.http.services.{rtr}-service.loadbalancer.server.port" in labels
                    else rtr
                )
            ).strip()
            labels.update(sablier_service_labels(
                router_name=rtr, group=svc_name, session=session,
            ))
            labels.update(sablier_retry_labels(router_name=rtr))
            labels.update(internal_wake_labels(
                router_name=rtr,
                service_ref=service_ref,
                internal_host=internal_wake_host(svc_name),
                group=svc_name,
                session=session,
            ))
            hc_prefix = f"traefik.http.services.{service_ref}.loadbalancer.healthcheck."
            for hk in [k for k in labels if isinstance(k, str) and k.startswith(hc_prefix)]:
                labels.pop(hk, None)
            mw_key = f"traefik.http.routers.{rtr}.middlewares"
            cur = [m.strip() for m in str(labels.get(mw_key, "") or "").split(",") if m.strip()]
            for item in (sablier_retry_ref(rtr), sablier_middleware_ref(rtr)):
                if item not in cur:
                    cur.append(item)
            labels[mw_key] = ",".join(cur)
    elif not want_sablier:
        hc_path = str(getattr(service, "health_check_path", "") or "").strip()
        if hc_path:
            for rtr in routers:
                service_ref = str(
                    labels.get(f"traefik.http.routers.{rtr}.service")
                    or (
                        f"{rtr}-service"
                        if f"traefik.http.services.{rtr}-service.loadbalancer.server.port" in labels
                        else rtr
                    )
                ).strip()
                if f"traefik.http.services.{service_ref}.loadbalancer.server.port" in labels:
                    labels.setdefault(
                        f"traefik.http.services.{service_ref}.loadbalancer.healthcheck.path",
                        hc_path.split(",")[0].strip() or "/health",
                    )
                    labels.setdefault(
                        f"traefik.http.services.{service_ref}.loadbalancer.healthcheck.interval",
                        "20s",
                    )
                    labels.setdefault(
                        f"traefik.http.services.{service_ref}.loadbalancer.healthcheck.timeout",
                        "8s",
                    )
    return labels


def internal_wake_labels(
    router_name: str, service_ref: str, internal_host: str, group: str,
    session: str = "10m",
) -> dict[str, str]:
    """Internal wake-on-request router (see internal_wake.py).

    Second router on the ``web`` entrypoint matching
    ``Host(`<service>.internal`)`` with the SAME sablier blocking +
    wakeretry chain as the edge router, bound to the SAME Traefik
    service (``service_ref`` differs per generator: ``<router>-service``
    in generate_traefik_labels, ``<router>`` in compose mode). No
    CrowdSec/auth middlewares — Docker-DNS-only names never leave the
    host, and the mesh path (which needs client certs) must never
    match here. ``group`` is the sablier wake target (service name).
    """
    irouter = f"{router_name}-internal"
    session = normalize_sablier_session(session, default="10m")
    labels = {
        f"traefik.http.routers.{irouter}.rule": f"Host(`{internal_host}`)",
        f"traefik.http.routers.{irouter}.entrypoints": "web",
        f"traefik.http.routers.{irouter}.service": service_ref,
        # The wake chain MUST be attached to the router itself: without
        # this line the router proxies straight at the (possibly stopped)
        # container and Traefik 503s with no wake attempt (2026-10-17:
        # every .internal dial to a sleeping service 503'd). Order
        # mirrors the edge chain: retry outer, sablier inner.
        f"traefik.http.routers.{irouter}.middlewares": (
            f"{irouter}-wakeretry,{irouter}-sablier"
        ),
        f"traefik.http.middlewares.{irouter}-sablier.plugin.sablier.group": group,
        f"traefik.http.middlewares.{irouter}-sablier.plugin.sablier.sablierUrl": SABLIER_URL,
        f"traefik.http.middlewares.{irouter}-sablier.plugin.sablier.sessionDuration": session,
        f"traefik.http.middlewares.{irouter}-sablier.plugin.sablier.blocking.timeout": "1m",
    }
    labels.update(sablier_retry_labels(irouter))
    return labels


def sablier_retry_labels(router_name: str) -> dict[str, str]:
    """Retry wrapper for the Sablier wake path (Traefik v3 built-in).

    Traefik v3.6 OSS Retry middleware supports only ``attempts`` and
    ``initialinterval`` (it rejects ``retryexpression`` and ``status`` with
    ``field not found``, which caused Traefik to drop the entire router).
    Built-in retry handles network dial/EOF errors while the woken container
    starts listening.
    """
    return {
        f"traefik.http.middlewares.{router_name}-wakeretry.retry.attempts": "5",
        f"traefik.http.middlewares.{router_name}-wakeretry.retry.initialinterval": "2s",
    }


def sablier_service_labels(
    router_name: str, group: str, session: str = "10m",
) -> dict[str, str]:
    """Single source of truth for Sablier scale-to-zero labels.

    Every generator (traefik_labels, local adapter, pipeline
    compose_networking) must use this — a drifted group name or URL
    means wake-on-request silently stops working for that path.

    Blocking strategy (no waiting page): the request simply holds
    until the container is ready (up to the timeout), then proxies
    normally. There is deliberately NO `dynamic` block — declaring
    both strategies is rejected, and any waiting page would leak
    container/group names to the public.
    """
    session = normalize_sablier_session(session, default="10m")
    group = str(group or router_name or "").strip() or router_name
    return {
        f"traefik.http.middlewares.{router_name}-sablier.plugin.sablier.group": group,
        f"traefik.http.middlewares.{router_name}-sablier.plugin.sablier.sablierUrl": SABLIER_URL,
        f"traefik.http.middlewares.{router_name}-sablier.plugin.sablier.sessionDuration": session,
        f"traefik.http.middlewares.{router_name}-sablier.plugin.sablier.blocking.timeout": "1m",
        "sablier.enable": "true",
        "sablier.group": group,
        "traefik.docker.allownonrunning": "true",
    }


def _normalize_health_path(path: str) -> str:
    value = str(path or "/").strip()
    if not value.startswith("/"):
        value = f"/{value}"
    value = re.sub(r'[^a-zA-Z0-9._/\-]', '', value)
    if not value:
        value = "/"
    return value


def _health_paths(primary_path: str | None) -> list[str]:
    """Same fallback logic as Docker health check in cloud/adapters/local.py"""
    values = []
    if primary_path:
        values.append(_normalize_health_path(primary_path))

    raw = os.environ.get(
        "DOCKER_HEALTHCHECK_FALLBACK_PATHS",
        "/,/health,/healthz,/ready,/live,/status,/api/health",
    )
    for chunk in str(raw).split(","):
        path = _normalize_health_path(chunk.strip())
        if path and path not in values:
            values.append(path)

    if not values:
        values = ["/"]
    return values


def generate_traefik_labels(
    service_name: str,
    domain: str | None = None,
    internal_port: int = 8000,
    enable_tls: bool = True,
    rate_limit_avg: int = 100,
    rate_limit_burst: int = 200,
    health_check_path: str | None = None,
    host_aliases: list | None = None,
    service=None,
) -> dict[str, str]:
    """
    Generate Traefik labels for a deployed service container.

    Args:
        service_name: Unique service identifier (used in router names)
        domain: Public domain (e.g., "myapp.smsly.cloud").
                If None, uses subdomain pattern: {service_name}.apps.smsly.cloud
        internal_port: Port the container listens on
        enable_tls: Whether to enable SSL via Let's Encrypt
        rate_limit_avg: Average requests per second allowed
        rate_limit_burst: Maximum burst requests allowed
        health_check_path: Primary health check path (e.g., "/health").
                           Falls back to common paths if not specified.
        host_aliases: List of alias dicts [{"host": "alias.example.com"}, ...] or strings.
    """
    # Sanitize service name for use in router names
    router_name = service_name.replace("-", "_").replace(".", "_").lower()

    # Default to subdomain if no custom domain specified
    if not domain:
        domain = f"{service_name}.apps.smsly.cloud"

    # Build list of all domains for Host() rule
    all_domains = [domain]
    for item in (host_aliases or []):
        if isinstance(item, dict):
            alias = str(item.get("host") or "").strip().lower()
        else:
            alias = str(item or "").strip().lower()
        if alias and alias not in all_domains:
            all_domains.append(alias)

    # Use same fallback logic as Docker health check
    hc_paths = _health_paths(health_check_path)
    hc_path_primary = hc_paths[0] if hc_paths else health_check_path or "/"

    host_rule = " || ".join(f"Host(`{d}`)" for d in all_domains)

    labels = {
        # Enable Traefik for this container
        "traefik.enable": "true",

        # HTTP Router configuration
        # NOTE: Always use the 'web' entrypoint because Caddy handles SSL
        # termination in production and forwards plain HTTP to Traefik:8081.
        # Traefik does NOT have a 'websecure' entrypoint in production.
        f"traefik.http.routers.{router_name}.rule": host_rule,
        f"traefik.http.routers.{router_name}.entrypoints": "web",
        f"traefik.http.routers.{router_name}.service": f"{router_name}-service",

        # Load balancer configuration
        f"traefik.http.services.{router_name}-service.loadbalancer.server.port": str(internal_port),

        # Health check — use the primary path (Traefik health checks take a single path)
        f"traefik.http.services.{router_name}-service.loadbalancer.healthcheck.path": hc_path_primary,
        f"traefik.http.services.{router_name}-service.loadbalancer.healthcheck.interval": "20s",
        f"traefik.http.services.{router_name}-service.loadbalancer.healthcheck.timeout": "8s",
    }

    # NOTE: TLS labels removed — Caddy handles SSL termination in production.
    # Traefik only listens on the 'web' entrypoint (port 80) behind Caddy.

    # Middlewares chain: crowdsec (platform default) + shared strict
    # secure-headers bundle (file provider, always discovered) +
    # per-router ratelimit. Opt-in sablier/forward-auth appended.
    # Ensure the file-provider bundle exists — a missing edge-shared.yml
    # drops every router carrying these refs.
    try:
        from apps.deployments.services.traefik_manager.edge_shared_file import (
            ensure_edge_shared_file,
        )
        ensure_edge_shared_file()
    except Exception:
        pass
    middlewares = ["secure-headers-strict@file"]
    try:
        from apps.deployments.models.core import PlatformConfig
        waf_on = bool(PlatformConfig.load().enable_crowdsec_waf)
    except Exception:
        waf_on = False
    if waf_on:
        middlewares.insert(0, "crowdsec-bouncer@docker")
    middlewares.append(f"{router_name}-ratelimit")
    labels[f"traefik.http.routers.{router_name}.middlewares"] = ",".join(
        middlewares)

    # Rate limiting middleware
    labels[f"traefik.http.middlewares.{router_name}-ratelimit.ratelimit.average"] = str(
        rate_limit_avg)
    labels[f"traefik.http.middlewares.{router_name}-ratelimit.ratelimit.burst"] = str(
        rate_limit_burst)
    labels[f"traefik.http.middlewares.{router_name}-ratelimit.ratelimit.period"] = "1s"

    try:
        sablier_on = bool(getattr(service, "sablier_enabled", False))
        jwt_on = bool(getattr(service, "edge_jwt_required", False))
        session = normalize_sablier_session(
            getattr(service, "sablier_session", "") or "10m",
            default="10m",
        )
    except Exception:
        sablier_on = False
        jwt_on = False
        session = "10m"

    extra_chain = []
    sablier_enrolled = False
    if sablier_on:
        from apps.deployments.services.internal_wake import (
            internal_wake_host,
        )
        allowed_sablier, disallow_reason = service_supports_sablier(service)
        if not allowed_sablier:
            # Stay up fail-closed: lite agents lack Sablier plugin and
            # mesh-served providers cannot wake over sidecar-to-sidecar mTLS.
            logger = __import__("logging").getLogger(__name__)
            logger.warning(
                "sablier deferred for %s: %s, service stays up",
                service_name,
                disallow_reason,
            )
        else:
            sablier_enrolled = True
            labels.update(sablier_service_labels(
                router_name=router_name, group=service_name, session=session,
            ))
            labels.update(sablier_retry_labels(router_name=router_name))
            labels.update(internal_wake_labels(
                router_name=router_name,
                service_ref=f"{router_name}-service",
                internal_host=internal_wake_host(service_name),
                group=service_name,
                session=session,
            ))
            extra_chain.append(sablier_retry_ref(router_name))
            extra_chain.append(sablier_middleware_ref(router_name))
    if sablier_enrolled:
        # Sablier-managed containers stop/start between requests; omit
        # active Traefik healthcheck probes so probes on a sleeping or
        # booting container do not mark the backend DOWN and return 503.
        hc_prefix = f"traefik.http.services.{router_name}-service.loadbalancer.healthcheck."
        for hk in [k for k in labels if k.startswith(hc_prefix)]:
            labels.pop(hk, None)
    if jwt_on:
        extra_chain.append("edge-forward-auth@file")
    if extra_chain:
        labels[f"traefik.http.routers.{router_name}.middlewares"] = (
            labels[f"traefik.http.routers.{router_name}.middlewares"] + "," + ",".join(extra_chain)
        )

    return labels


def generate_preview_labels(
    parent_service_name: str,
    pr_number: int,
    internal_port: int = 8000,
) -> dict[str, str]:
    """
    Generate Traefik labels for a PR preview environment.

    Preview environments get domains like: pr-123.myapp.preview.smsly.cloud
    """
    preview_name = f"{parent_service_name}-pr-{pr_number}"
    domain = f"pr-{pr_number}.{parent_service_name}.preview.smsly.cloud"

    return generate_traefik_labels(
        service_name=preview_name,
        domain=domain,
        internal_port=internal_port,
        enable_tls=True,
        # Lower rate limits for previews
        rate_limit_avg=50,
        rate_limit_burst=100,
    )


def labels_to_docker_args(labels: dict[str, str]) -> str:
    """
    Convert labels dict to docker run --label arguments.

    Returns:
        String like: --label "key1=value1" --label "key2=value2"
    """
    return " ".join([f'--label "{k}={v}"' for k, v in labels.items()])


def labels_to_compose_dict(labels: dict[str, str]) -> list:
    """
    Convert labels dict to docker-compose labels format.

    Returns:
        List like: ["key1=value1", "key2=value2"]
    """
    return [f"{k}={v}" for k, v in labels.items()]
