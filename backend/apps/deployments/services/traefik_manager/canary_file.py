"""Traefik file-provider writer for weighted canary splits.

Architecture: Caddy keeps exactly ONE upstream (``traefik:80``) per site.
The split lives in Traefik as a file-provider ``weighted`` service:

* router ``<svc>-canary`` mirrors the live router's rule with a higher
  priority, so it deterministically captures the live hostnames while
  the file exists (removing the file instantly restores plain routing —
  no container recreates, ever),
* service ``<svc>-canary-wrr`` balances ``<live>@docker`` /
  ``<green>@docker`` by weight (hot-updated by rewriting the file).

Because traffic still flows Caddy → Traefik → container, every Traefik
middleware on the live router (CrowdSec bouncer, rate limits, header
rewrites) applies to BOTH variants — the file router mirrors them.

``weighted`` services are File-provider-only in Traefik (Docker labels
cannot express them), hence this writer. Files live in
``TRAEFIK_DYNAMIC_DIR`` (``/traefik-dynamic``, bind-mounted into the
Traefik container at ``/etc/traefik/dynamic`` with
``--providers.file.watch=true``).

Staleness discipline: a file that references a vanished green breaks
its router, so EVERY green-removal path calls :func:`remove_canary_file`
(promote, cancel, reaper, supersede, service delete, abort). Weight
changes only rewrite; service names are stable across green recreates.
"""

import logging
import os
import re

import yaml

logger = logging.getLogger(__name__)

TRAEFIK_DYNAMIC_DIR = os.environ.get("TRAEFIK_DYNAMIC_DIR", "/traefik-dynamic")
CANARY_FILE_PREFIX = "canary-"
CANARY_ROUTER_SUFFIX = "-canary"
CANARY_WRR_SUFFIX = "-canary-wrr"

# Priority bump over the live router (ai-router services run at 1000).
CANARY_PRIORITY_BUMP = 100

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9-]")


class CanaryFileError(Exception):
    """Raised when a canary file cannot be written (maps to 409/500)."""


def sanitize_router_name(name: str) -> str:
    """Same sanitization the adapter applies to router names."""
    return _SANITIZE_RE.sub("-", str(name or "").strip())


def canary_file_path(service) -> str:
    """Absolute path of this service's canary file (by service id)."""
    service_id = str(getattr(service, "id", "") or "").strip()
    safe = re.sub(r"[^A-Za-z0-9-]", "-", service_id) or "unknown"
    return os.path.join(TRAEFIK_DYNAMIC_DIR, f"{CANARY_FILE_PREFIX}{safe}.yml")


def _container_labels(container) -> dict:
    try:
        container.reload()
    except Exception:
        pass
    try:
        return dict((container.attrs.get("Config") or {}).get("Labels") or {})
    except Exception:
        return {}


def _iter_router_services(labels: dict):
    """Yield (router, service) pairs verified against service labels."""
    routers: dict[str, dict] = {}
    services: set[str] = set()
    for key, value in labels.items():
        if not isinstance(key, str):
            continue
        m = re.match(r"traefik\.http\.routers\.([^.]+)\.rule$", key)
        if m:
            routers.setdefault(m.group(1), {})["rule"] = str(value or "")
            continue
        m = re.match(r"traefik\.http\.services\.([^.]+)\.loadbalancer\.server\.port$", key)
        if m:
            services.add(m.group(1))
    for router in routers:
        if router in services:
            yield router, router


def _router_service_from_labels(labels: dict, host_substr: str) -> tuple[str, str, dict] | None:
    """Find (router, service, router_attrs) whose rule mentions host_substr.

    Service binding follows the platform convention (service name == router
    name); verified against the labels before returning.
    """
    for router, service in _iter_router_services(labels):
        rule = str(labels.get(f"traefik.http.routers.{router}.rule", "") or "")
        if host_substr and host_substr not in rule:
            continue
        return router, service, {"rule": rule}
    return None


def _label(labels: dict, router: str, field: str, default: str = "") -> str:
    return str(labels.get(f"traefik.http.routers.{router}.{field}", default) or default)


def _has_child_healthcheck(labels: dict, service: str) -> bool:
    prefix = f"traefik.http.services.{service}.loadbalancer.healthcheck."
    return any(
        isinstance(k, str) and k.startswith(prefix)
        for k in labels.keys()
    )


def resolve_canary_topology(service, deployment, live_container, green_container) -> dict:
    """Read live/green Docker labels into a split descriptor.

    Returns ``{'live_router', 'live_rule', 'live_priority', 'entrypoints',
    'middlewares', 'tls_resolver', 'live_svc', 'green_svc',
    'healthcheck_both'}``. Raises :class:`CanaryFileError` when the live
    router cannot be determined.
    """
    live_labels = _container_labels(live_container)
    green_labels = _container_labels(green_container)

    public_domain = str(getattr(service, "public_domain", "") or "").strip().lower()
    found = _router_service_from_labels(live_labels, public_domain)
    if found is None:
        # Fall back to any Host() router on the live container.
        found = _router_service_from_labels(live_labels, "Host(")
    if found is None:
        raise CanaryFileError(
            f"No Traefik router found on live container for service "
            f"{getattr(service, 'name', '?')} — cannot steal routing."
        )
    live_router, live_svc, _ = found

    # Prefer the staging router explicitly: label insertion order is
    # arbitrary and preview-neutralize labels can add extra routers.
    green_svc = None
    first_svc = None
    for router, svc_name in _iter_router_services(green_labels):
        if first_svc is None:
            first_svc = svc_name
        if router.endswith("-staging"):
            green_svc = svc_name
            break
    if green_svc is None:
        green_svc = first_svc
    if not green_svc:
        # Green staging service follows the adapter convention.
        green_svc = f"{sanitize_router_name(getattr(service, 'name', ''))}-staging"
    if green_svc == live_svc:
        raise CanaryFileError("Green service collides with the live service name.")

    try:
        live_priority = int(_label(live_labels, live_router, "priority", "100") or 100)
    except ValueError:
        live_priority = 100
    entrypoints = [
        e.strip() for e in _label(live_labels, live_router, "entrypoints", "web").split(",")
        if e.strip()
    ] or ["web"]
    middlewares = [
        m.strip() for m in _label(live_labels, live_router, "middlewares", "").split(",")
        if m.strip()
    ]
    tls_resolver = _label(live_labels, live_router, "tls.certresolver", "").strip()
    try:
        port = int(getattr(service, "internal_port", None) or 8000)
    except (TypeError, ValueError):
        port = 8000
    return {
        "live_router": live_router,
        "live_rule": _label(live_labels, live_router, "rule", ""),
        "live_priority": live_priority,
        "entrypoints": entrypoints,
        "middlewares": middlewares,
        "tls_resolver": tls_resolver,
        "live_svc": live_svc,
        "green_svc": green_svc,
        "port": port,
        "healthcheck_both": (
            _has_child_healthcheck(live_labels, live_svc)
            and _has_child_healthcheck(green_labels, green_svc)
        ),
        "deployment_id": str(getattr(deployment, "id", "")),
        "commit_hash": str(getattr(deployment, "commit_hash", "") or ""),
    }


def build_canary_config(
    topology: dict, live_weight: int, staging_weight: int,
    get_only: bool = False, sticky: bool = False,
) -> dict:
    """Pure: file-provider dynamic config for the split. Unit tested.

    ``get_only`` restricts the steal to GET/HEAD (writes stay on live).
    ``sticky`` pins clients to one variant via a sticky cookie (for
    stateful sessions that break when bounced between versions).
    """
    base = sanitize_router_name(topology.get("live_router", "svc"))
    router_name = f"{base}{CANARY_ROUTER_SUFFIX}"
    wrr_name = f"{base}{CANARY_WRR_SUFFIX}"
    live_rule = str(topology.get("live_rule", "") or "").strip()
    rule = f"({live_rule}) && Method(`GET`, `HEAD`)" if get_only else live_rule
    router: dict = {
        "rule": rule,
        "priority": int(topology.get("live_priority", 100)) + CANARY_PRIORITY_BUMP,
        "entryPoints": list(topology.get("entrypoints", ["web"])),
        "service": wrr_name,
    }
    if topology.get("middlewares"):
        router["middlewares"] = list(topology["middlewares"])
    if topology.get("tls_resolver"):
        router["tls"] = {"certResolver": topology["tls_resolver"]}
    weighted: dict = {
        "services": [
            {"name": f"{topology['live_svc']}@docker", "weight": int(live_weight)},
            {"name": f"{topology['green_svc']}@docker", "weight": int(staging_weight)},
        ],
    }
    if sticky:
        weighted["sticky"] = {"cookie": {}}
    # Parent-level health awareness requires BOTH children to carry
    # healthchecks — otherwise Traefik refuses to create the service.
    if topology.get("healthcheck_both"):
        weighted["healthCheck"] = {}
    return {
        "http": {
            "routers": {router_name: router},
            "services": {wrr_name: {"weighted": weighted}},
        }
    }


def validate_canary_config(config: dict, live_priority: int | None = None) -> list[str]:
    """Fail-closed structural validation before writing. Pure.

    ``live_priority`` (when known) additionally enforces that the canary
    router strictly exceeds it — otherwise the priority steal is not
    deterministic. Without it, only structural sanity is checked.
    """
    errors: list[str] = []
    if not isinstance(config, dict):
        return ["canary config must be a mapping"]
    http = config.get("http")
    if not isinstance(http, dict):
        return ["canary config is missing http root"]
    routers = http.get("routers", {})
    services = http.get("services", {})
    if len(routers) != 1:
        errors.append(f"expected exactly 1 canary router, found {len(routers)}")
    if len(services) != 1:
        errors.append(f"expected exactly 1 canary service, found {len(services)}")
    for rname, router in (routers or {}).items():
        if not isinstance(router, dict):
            errors.append(f"router {rname} must be a mapping")
            continue
        if not str(router.get("rule", "")).strip():
            errors.append(f"router {rname} has an empty rule")
        try:
            prio = int(router.get("priority", 0))
        except (TypeError, ValueError):
            errors.append(f"router {rname} has a non-integer priority")
            prio = 0
        if prio <= 0:
            errors.append(f"router {rname} has a non-positive priority")
        elif live_priority is not None and prio <= live_priority:
            errors.append(
                f"router {rname} priority {prio} must exceed the live router "
                f"priority {live_priority} so the steal is deterministic"
            )
        if not router.get("entryPoints"):
            errors.append(f"router {rname} has no entryPoints")
    for sname, service in (services or {}).items():
        weighted = (service or {}).get("weighted") if isinstance(service, dict) else None
        if not isinstance(weighted, dict):
            errors.append(f"service {sname} must define weighted")
            continue
        children = weighted.get("services", [])
        if len(children) != 2:
            errors.append(f"service {sname} must reference exactly 2 children")
            continue
        total = 0
        for child in children:
            name = str((child or {}).get("name", ""))
            if not name.endswith("@docker"):
                errors.append(f"service {sname} child {name!r} must reference @docker")
            try:
                w = int((child or {}).get("weight", -1))
            except (TypeError, ValueError):
                errors.append(f"service {sname} child {name!r} has a non-integer weight")
                continue
            if w < 0:
                errors.append(f"service {sname} child {name!r} has a negative weight")
            total += max(w, 0)
        if total != 100:
            errors.append(f"service {sname} weights sum to {total}, expected 100")
        sticky = weighted.get("sticky", None)
        if sticky is not None and not isinstance(sticky, dict):
            errors.append(f"service {sname} sticky must be a mapping when present")
    return errors


def _docker():
    import docker

    return docker.from_env()


def _active_staged_deployment(service):
    from apps.deployments.models import Deployment

    return (
        Deployment.objects.filter(
            service=service,
            status__in=(Deployment.Status.STAGED, Deployment.Status.HEALTH_CHECK),
        )
        .exclude(green_container_id__isnull=True)
        .exclude(green_container_id="")
        .order_by("-created_at")
        .first()
    )


def _container_running(container) -> bool:
    try:
        container.reload()
        state = container.attrs.get("State", {}) or {}
        if str(state.get("Status") or "").lower() != "running":
            return False
        health = str((state.get("Health", {}) or {}).get("Status") or "").lower()
        return health in ("healthy", "")
    except Exception:
        return False


def write_canary_file(service, live_weight: int, staging_weight: int) -> dict:
    """Write (or rewrite) this service's canary file. Hot-applied by Traefik.

    Verifies: split wanted (CANARY + 1..100), STAGED green present, BOTH
    containers running, expand/contract guard allows the commit. Raises
    :class:`CanaryFileError` with a human message otherwise.
    Returns ``{'path', 'router', 'service', 'live_weight', 'staging_weight'}``.
    """
    if str(getattr(service, "deploy_strategy", "") or "").upper() != "CANARY":
        raise CanaryFileError("Service deploy_strategy is not CANARY.")
    try:
        staging_weight = int(staging_weight)
        live_weight = int(live_weight)
    except (TypeError, ValueError):
        raise CanaryFileError("Weights must be integers.")
    if not 1 <= staging_weight <= 100 or live_weight + staging_weight != 100:
        raise CanaryFileError("Staging weight must be 1-100 (weights sum to 100).")
    if str(getattr(service, "deploy_mode", "") or "").upper() == "COMPOSE":
        raise CanaryFileError("Weighted splits are unsupported for COMPOSE services.")
    if _remote_service(service):
        raise CanaryFileError("Weighted splits are unsupported for remote targets.")

    deployment = _active_staged_deployment(service)
    if deployment is None:
        raise CanaryFileError("No STAGED deployment with a green container — push first.")
    green_id = str(getattr(deployment, "green_container_id", "") or "").strip()
    live_name = str(getattr(service, "name", "") or "").strip()
    if not green_id or not live_name:
        raise CanaryFileError("Green container reference is missing.")

    client = _docker()
    try:
        live_container = client.containers.get(live_name)
    except Exception:
        raise CanaryFileError(f"Live container {live_name} is not inspectable.")
    try:
        green_container = client.containers.get(green_id)
    except Exception:
        raise CanaryFileError("Green container is gone — redeploy to stage a fresh one.")
    if not _container_running(live_container):
        raise CanaryFileError("Live container is not running/healthy.")
    if not _container_running(green_container):
        raise CanaryFileError("Green container is not running/healthy — cannot split to it.")

    try:
        from apps.deployments.services.safedeploy.canary_guard import (
            validate_canary_enable,
        )

        allowed, reasons = validate_canary_enable(
            service, commit_hash=getattr(deployment, "commit_hash", None),
        )
    except Exception as exc:
        logger.warning("Canary file: guard lookup failed, refusing: %s", exc)
        raise CanaryFileError("Migration safety could not be verified — try again.")
    if not allowed:
        raise CanaryFileError(reasons[0] if reasons else "Commit is not expand-safe.")

    topology = resolve_canary_topology(service, deployment, live_container, green_container)
    try:
        from apps.deployments.services.safedeploy.promotion_guard import (
            get_promotion_policy,
        )

        policy = get_promotion_policy(service)
        get_only = bool(policy.get("canary_get_only", False))
        sticky = bool(policy.get("canary_sticky", False))
    except Exception as exc:
        logger.debug("Canary split policy lookup failed, using defaults: %s", exc)
        get_only = False
        sticky = False
    config = build_canary_config(
        topology, live_weight, staging_weight, get_only=get_only, sticky=sticky,
    )
    try:
        live_priority = int(topology.get("live_priority", 100))
    except (TypeError, ValueError):
        live_priority = 100
    errors = validate_canary_config(config, live_priority=live_priority)
    if errors:
        raise CanaryFileError("; ".join(errors[:3]))

    path = canary_file_path(service)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    # Atomic replace so a failed write can never leave a half-written
    # live file behind (a transient provider hiccup on the .tmp name is
    # harmless — the previous good config stays loaded).
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp_path, path)
    try:
        with open(path, encoding="utf-8") as f:
            yaml.safe_load(f)
    except Exception as exc:
        raise CanaryFileError(f"Wrote unreadable canary file: {exc}")
    logger.info(
        "Canary file written for service %s (%d/%d)",
        live_name, live_weight, staging_weight,
    )
    base = sanitize_router_name(topology["live_router"])
    return {
        "path": path,
        "router": f"{base}{CANARY_ROUTER_SUFFIX}",
        "service": f"{base}{CANARY_WRR_SUFFIX}",
        "live_weight": live_weight,
        "staging_weight": staging_weight,
        "get_only": get_only,
        "sticky": sticky,
    }


def _remote_service(service) -> bool:
    try:
        from apps.deployments.services.caddy_manager.upstream import (
            _remote_upstream_url_for_service,
        )

        return bool(_remote_upstream_url_for_service(service))
    except Exception:
        return False


def remove_canary_file(service_or_id) -> bool:
    """Delete the canary file if present. Never raises. Returns True if removed.

    Accepts a Service row or a raw service id string (filenames are
    id-based so renames can't orphan them). Callers: abort, cancel,
    reaper, supersede, promote, service delete.
    """
    try:
        if isinstance(service_or_id, str):
            safe = re.sub(r"[^A-Za-z0-9-]", "-", service_or_id.strip()) or "unknown"
            path = os.path.join(TRAEFIK_DYNAMIC_DIR, f"{CANARY_FILE_PREFIX}{safe}.yml")
        else:
            # Service deletions pass an id; live services pass the row.
            service_id = getattr(service_or_id, "id", None)
            path = canary_file_path(service_or_id) if service_id else None
            if path is None:
                return False
        if path and os.path.exists(path):
            os.remove(path)
            logger.info("Canary file removed: %s", path)
            return True
        return False
    except Exception as exc:
        logger.warning("Canary file removal failed: %s", exc)
        return False


def sync_canary_files() -> dict:
    """Remove canary files whose split is no longer active.

    Read-only w.r.t. routing intent: a file survives only when its
    service still wants CANARY weight>0 AND a STAGED green is present.
    Returns ``{'checked', 'removed'}``. Never raises.
    """
    checked = 0
    removed = []
    try:
        if not os.path.isdir(TRAEFIK_DYNAMIC_DIR):
            return {"checked": 0, "removed": []}
        files = [
            f for f in os.listdir(TRAEFIK_DYNAMIC_DIR)
            if f.startswith(CANARY_FILE_PREFIX) and f.endswith((".yml", ".yaml"))
        ]
    except Exception as exc:
        logger.debug("Canary sync list failed: %s", exc)
        return {"checked": 0, "removed": []}
    try:
        from apps.deployments.models import Deployment, Service
    except Exception as exc:
        logger.debug("Canary sync DB unavailable: %s", exc)
        return {"checked": 0, "removed": []}
    for filename in files:
        checked += 1
        service_id = filename[len(CANARY_FILE_PREFIX):].rsplit(".", 1)[0]
        try:
            service = Service.objects.filter(id=service_id).first()
            keep = False
            if service is not None:
                try:
                    pct = int(getattr(service, "canary_percentage", 0) or 0)
                except (TypeError, ValueError):
                    pct = 0
                keep = (
                    str(getattr(service, "deploy_strategy", "") or "").upper() == "CANARY"
                    and 1 <= pct <= 100
                    and Deployment.objects.filter(
                        service=service,
                        status__in=(Deployment.Status.STAGED, Deployment.Status.HEALTH_CHECK),
                    ).exclude(green_container_id__isnull=True).exclude(
                        green_container_id=""
                    ).exists()
                )
            if not keep:
                full = os.path.join(TRAEFIK_DYNAMIC_DIR, filename)
                os.remove(full)
                removed.append(filename)
                logger.info("Canary sync removed stale file %s", filename)
        except Exception as exc:
            logger.debug("Canary sync failed for %s: %s", filename, exc)
    return {"checked": checked, "removed": removed}
