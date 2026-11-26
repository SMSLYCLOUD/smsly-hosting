"""Parity helpers for replica create/recreate paths.

Outage record (2026-09-12, prod): a local replica was stamped with a
PARTIAL Traefik service block (``server.port`` only) while the primary
carried healthcheck labels too. Traefik drops a service whose
containers disagree (``defined multiple times with different
configurations``) — the whole service went dark and all traffic fell
through to route-fallback for ~1h. A second gap: replica env came from
DB rows only, missing pipeline-derived keys (notably ``PORT``), so the
replica bound :8000 while Traefik dialed :80.

Rules enforced here:
1. A replica's ``traefik.http.services.<svc>.*`` block must be
   IDENTICAL to the live reference (copied, then verified — fail
   closed: a conflicting spawn raises instead of taking the service
   down).
2. Replica env = live reference env (runtime truth: PORT, secrets,
   derived vars) overlaid with current DB rows (freshness), sanitized
   like the pipeline.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_ROUTER_RULE_RE = re.compile(r"^traefik\.http\.routers\.([^.]+)\.rule$")
_SERVICE_KEY_RE = re.compile(r"^traefik\.http\.services\.([^.]+)\.")
_TRANSIENT_CONTAINER_TAGS = ("-rollback-", "-green-", "-promoting-")


class ReplicaParityError(RuntimeError):
    """Raised when a replica would disagree with its service's live config."""


#: Keys that belong to a specific container instance, never copied.
CONTAINER_SPECIFIC_ENV = frozenset({"HOSTNAME"})


def parse_env_list(env_list) -> dict:
    """Parse a Docker ``Config.Env`` list into a dict, minus per-container keys."""
    env = {}
    for item in env_list or []:
        if not isinstance(item, str) or "=" not in item:
            continue
        key, _, value = item.partition("=")
        if key and key not in CONTAINER_SPECIFIC_ENV:
            env[key] = value
    return env


def resolve_traefik_service_name(labels: dict, service_name: str) -> str:
    """Discover the effective Traefik service key defined on a container.

    Handles exact canonical names (``<svc>``), blue-green promoted slots
    (``<svc>-bg-<hex>``), sanitized ``.``/``_`` names (``my-app``), and
    ``generate_traefik_labels`` suffixed names (``<router>-service``).
    Falls back to *service_name* when no matching Traefik service exists.
    """
    labels = labels or {}
    if not service_name:
        return ""
    exact_prefix = f"traefik.http.services.{service_name}."
    if any(isinstance(k, str) and k.startswith(exact_prefix) for k in labels):
        return service_name

    sanitized_dash = service_name.replace(".", "-").replace("_", "-")
    sanitized_under = service_name.replace("-", "_").replace(".", "_")

    # 1. Router -> service binding on the live container (ignoring internal routers).
    router_candidates: list[tuple[int, str]] = []
    for key, val in labels.items():
        if not isinstance(key, str):
            continue
        match = _ROUTER_RULE_RE.match(key)
        if not match:
            continue
        rname = match.group(1)
        rule = str(val or "").strip()
        if not rule or rname.endswith("-internal") or rname.endswith("-staging") or "disabled.localhost" in rule:
            continue
        svc_ref = str(labels.get(f"traefik.http.routers.{rname}.service") or rname).strip()
        if not svc_ref or not any(isinstance(k, str) and k.startswith(f"traefik.http.services.{svc_ref}.") for k in labels):
            continue
        priority = 2
        if rname == sanitized_dash or rname.startswith(f"{sanitized_dash}-bg-") or rname == f"{sanitized_under}":
            priority = 0
        elif sanitized_dash in rname or sanitized_under in rname:
            priority = 1
        router_candidates.append((priority, svc_ref))
    if router_candidates:
        router_candidates.sort(key=lambda item: item[0])
        return router_candidates[0][1]

    # 2. Direct service label scan (sanitized, -bg-<slot>, or -service forms).
    defined_services: list[str] = []
    for key in labels:
        if not isinstance(key, str):
            continue
        match = _SERVICE_KEY_RE.match(key)
        if match:
            svc_key = match.group(1)
            if svc_key not in defined_services and not svc_key.endswith("-internal"):
                defined_services.append(svc_key)

    preferred_names = (
        sanitized_dash,
        f"{sanitized_dash}-service",
        f"{service_name}-service",
        sanitized_under,
        f"{sanitized_under}-service",
    )
    for cand in preferred_names:
        if cand in defined_services:
            return cand
    for svc_key in defined_services:
        if svc_key.startswith(f"{sanitized_dash}-bg-") or svc_key.startswith(f"{service_name}-bg-"):
            return svc_key
    if len(defined_services) == 1:
        return defined_services[0]
    return service_name


def traefik_service_block(
    labels: dict,
    service_name: str,
    resolved_service_name: str | None = None,
) -> dict:
    """Extract the ``traefik.http.services.<svc>.*`` label block."""
    target = resolved_service_name or resolve_traefik_service_name(labels, service_name)
    if not target:
        return {}
    prefix = f"traefik.http.services.{target}."
    return {k: v for k, v in (labels or {}).items() if isinstance(k, str) and k.startswith(prefix)}


def replica_sablier_labels(service, ref_labels: dict | None = None) -> dict[str, str]:
    """Return Sablier group/allow-nonrunning labels for a replica when enrolled.

    Checks both the live reference container labels and the Service model
    (guarding against MagicMock attributes in unit tests).
    """
    ref_labels = ref_labels or {}
    ref_enrolled = str(ref_labels.get("sablier.enable", "")).strip().lower() == "true"
    svc_sablier = getattr(service, "sablier_enabled", False) is True
    if not ref_enrolled and not svc_sablier:
        return {}
    if not ref_enrolled:
        try:
            from apps.deployments.services.traefik_labels import service_supports_sablier
            if not service_supports_sablier(service)[0]:
                return {}
        except Exception:
            pass
    group = str(ref_labels.get("sablier.group") or getattr(service, "name", "") or "").strip()
    if not group:
        return {}
    return {
        "sablier.enable": "true",
        "sablier.group": group,
        "traefik.docker.allownonrunning": "true",
    }


def sanitize_db_env(db_env: dict) -> dict:
    """Apply pipeline sanitization to DB-declared env (best effort)."""
    try:
        from apps.deployments.utils.env_sanitizer import (
            is_placeholder,
            sanitize_env_value,
        )
    except Exception:
        return dict(db_env)
    clean = {}
    for key, value in db_env.items():
        try:
            cleaned = sanitize_env_value(value, key=key, allow_empty=True)
        except Exception:
            cleaned = value
        if cleaned is None:
            continue
        try:
            if is_placeholder(cleaned, key):
                continue
        except Exception:
            pass
        clean[key] = cleaned
    return clean


def merge_replica_env(live_env: dict, db_env: dict) -> dict:
    """Live reference env overlaid with current (sanitized) DB rows."""
    merged = dict(live_env or {})
    merged.update(sanitize_db_env(db_env or {}))
    return merged


def find_service_containers(client, service_name: str) -> list:
    """All live containers belonging to the service (primary + replicas)."""
    candidates: list = []
    try:
        candidates.append(client.containers.get(service_name))
    except Exception:
        pass
    try:
        for c in client.containers.list(
            all=True, filters={"label": f"smsly.blue_green.canonical_name={service_name}"}
        ):
            if getattr(c, "name", "") != service_name and c not in candidates:
                candidates.append(c)
    except Exception:
        pass
    if not candidates:
        try:
            for c in client.containers.list(
                all=True, filters={"name": service_name}
            ):
                if c not in candidates:
                    candidates.append(c)
        except Exception:
            pass
    return candidates


def live_service_blocks(
    client,
    service_name: str,
    target_service_name: str | None = None,
) -> list:
    """Traefik service blocks of every live active service container (best effort)."""
    blocks = []
    for c in find_service_containers(client, service_name):
        cname = str(getattr(c, "name", "") or "")
        if any(tag in cname for tag in _TRANSIENT_CONTAINER_TAGS):
            continue
        try:
            labels = (c.attrs or {}).get("Config", {}).get("Labels", {}) or {}
        except Exception:
            continue
        blocks.append(
            traefik_service_block(
                labels,
                service_name,
                resolved_service_name=target_service_name,
            )
        )
    return blocks


def find_reference_container(client, service_name: str):
    """Return the live container to copy config from.

    Preference: RUNNING primary (exact name) → any-state primary →
    RUNNING sibling replica → any sibling. ``None`` when nothing exists
    (first-ever spawn: best effort, nothing to conflict with).
    """
    candidates = find_service_containers(client, service_name)
    running = [c for c in candidates if getattr(c, "status", "") == "running"]
    primaries = [c for c in candidates if getattr(c, "name", "") == service_name]
    for pool in (
        [c for c in primaries if c in running],
        primaries,
        running,
        candidates,
    ):
        if pool:
            return pool[0]
    return None


def reference_details(client, service_name: str) -> tuple[dict, dict, dict, str]:
    """Return ``(env, service_block, raw_labels, resolved_traefik_service_name)``.

    Uses the live primary/sibling container so replicas mirror promoted
    ``-bg-<slot>`` or sanitized Traefik service blocks identically.
    """
    ref = find_reference_container(client, service_name)
    if ref is None:
        return {}, {}, {}, service_name.replace(".", "-").replace("_", "-")
    try:
        attrs = ref.attrs or {}
    except Exception:
        return {}, {}, {}, service_name.replace(".", "-").replace("_", "-")
    config = attrs.get("Config", {}) or {}
    raw_labels = config.get("Labels", {}) or {}
    resolved = resolve_traefik_service_name(raw_labels, service_name)
    return (
        parse_env_list(config.get("Env", []) or []),
        traefik_service_block(raw_labels, service_name, resolved_service_name=resolved),
        dict(raw_labels),
        resolved,
    )


def reference_config(client, service_name: str) -> tuple[dict, dict]:
    """Return ``(env, traefik_service_labels)`` from the live reference.

    ``({}, {})`` when no reference container exists.
    """
    env, block, _labels, _resolved = reference_details(client, service_name)
    return env, block


def assert_block_compatible(reference_blocks: list, new_block: dict,
                             service_name: str) -> None:
    """Fail closed when the new service block disagrees with live config.

    Every non-empty live block must equal the new one — Traefik drops
    the WHOLE service on any disagreement, so a conflicting spawn must
    raise instead of deploying.
    """
    for ref in reference_blocks:
        if not ref:
            continue
        if dict(ref) != dict(new_block or {}):
            diff_keys = sorted(
                set(ref) ^ set(new_block or {}) |
                {k for k in ref if k in (new_block or {}) and ref[k] != new_block[k]}
            )
            raise ReplicaParityError(
                f"Replica Traefik service block for {service_name} disagrees "
                f"with live config on keys={diff_keys[:8]} — refusing to spawn "
                f"(Traefik would drop the entire service). Fix or destroy the "
                f"conflicting container first."
            )
    if not new_block and any(reference_blocks):
        raise ReplicaParityError(
            f"Replica for {service_name} carries no Traefik service block while "
            f"live containers do — refusing to spawn (Traefik would drop the "
            f"entire service)."
        )
