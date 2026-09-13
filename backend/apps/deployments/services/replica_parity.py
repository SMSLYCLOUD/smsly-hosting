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

logger = logging.getLogger(__name__)


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


def traefik_service_block(labels: dict, service_name: str) -> dict:
    """Extract the ``traefik.http.services.<svc>.*`` label block."""
    prefix = f"traefik.http.services.{service_name}."
    return {k: v for k, v in (labels or {}).items() if k.startswith(prefix)}


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
            if is_placeholder(cleaned):
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


def live_service_blocks(client, service_name: str) -> list:
    """Traefik service blocks of every live service container (best effort)."""
    blocks = []
    for c in find_service_containers(client, service_name):
        try:
            labels = (c.attrs or {}).get("Config", {}).get("Labels", {}) or {}
        except Exception:
            continue
        blocks.append(traefik_service_block(labels, service_name))
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


def reference_config(client, service_name: str) -> tuple[dict, dict]:
    """Return ``(env, traefik_service_labels)`` from the live reference.

    ``({}, {})`` when no reference container exists.
    """
    ref = find_reference_container(client, service_name)
    if ref is None:
        return {}, {}
    try:
        attrs = ref.attrs or {}
    except Exception:
        return {}, {}
    config = attrs.get("Config", {}) or {}
    return (
        parse_env_list(config.get("Env", []) or []),
        traefik_service_block(config.get("Labels", {}) or {}, service_name),
    )


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
