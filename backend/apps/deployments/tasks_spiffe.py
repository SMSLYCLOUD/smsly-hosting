"""
Celery task to sync SPIRE registration entries with deployed services.

Runs periodically to ensure all deployed services have SPIFFE identities
registered in the ECOSYSTEM SPIRE server (separate from platform services).

Add to celery.py beat schedule:
    'sync-spiffe-entries': {
        'task': 'apps.deployments.tasks_spiffe.sync_spiffe_entries_task',
        'schedule': crontab(minute='*/5'),
    },
"""

import logging
import subprocess
import os

from celery import shared_task

from apps.deployments.constants import RETRY_DELAY_FAST, TASK_TIME_LIMIT_QUICK

logger = logging.getLogger(__name__)

ECOSYSTEM_SPIFFE_TRUST_DOMAIN = os.getenv("ECOSYSTEM_TRUST_DOMAIN", "ecosystem.local")
ECOSYSTEM_SPIRE_SERVER_CONTAINER = os.getenv(
    "SPIRE_ECOSYSTEM_SERVER_CONTAINER", "smsly-spire-server-ecosystem"
)
ECOSYSTEM_SPIRE_SERVER_SOCKET = "/tmp/spire-server/private/api.sock"

# Platform trust domain (default services). Separate SPIRE server/agent
# pair from the ecosystem stack — entries, SVIDs and agents must never
# cross domains.
PLATFORM_SPIFFE_TRUST_DOMAIN = os.getenv("SPIFFE_TRUST_DOMAIN", "platform.local")
PLATFORM_SPIRE_SERVER_CONTAINER = os.getenv(
    "SPIRE_SERVER_CONTAINER", "smsly-spire-server"
)
PLATFORM_SPIRE_SERVER_SOCKET = "/tmp/spire-server/private/api.sock"


_PLATFORM_STATIC_SERVICES = frozenset({
    "gateway", "identity", "backend", "platform",
    "audit", "policy", "rate-limit", "chain", "email",
})


def _sync_domain_entries(
    MtlsConfig,
    *,
    trust_domain: str,
    server_container: str,
    server_socket: str,
    live_agent: str | None,
    existing_entries: list | None,
    preserve_services: frozenset[str] = frozenset(),
) -> dict:
    """Sync registration entries for a single SPIFFE trust domain."""
    if existing_entries is None:
        raise RuntimeError(f"Failed to list SPIRE entries for {trust_domain}; aborting sync")

    enabled_configs = MtlsConfig.objects.filter(
        enabled=True, trust_domain=trust_domain
    ).select_related("service")
    service_names = {cfg.service.name for cfg in enabled_configs}

    by_path: dict[str, list] = {}
    for entry in existing_entries:
        path = _entry_path(entry)
        if path.startswith("/service/"):
            by_path.setdefault(path, []).append(entry)
    existing_services = {path[len("/service/"):] for path in by_path}

    created = 0
    for name in service_names - existing_services:
        if _create_spire_entry(
            name,
            parent_id=live_agent,
            trust_domain=trust_domain,
            server_container=server_container,
            server_socket=server_socket,
        ):
            created += 1

    reparented = 0
    duplicates_removed = 0
    converge_names = service_names | (existing_services & preserve_services)
    for name in converge_names:
        path = f"/service/{name}"
        entries = by_path.get(path, [])
        if not entries:
            continue
        expected = f"docker:label:com.paas.service:{name}"
        canonical = [
            e for e in entries
            if _entry_selectors(e) == [expected]
            and _entry_parent_id(e) == live_agent
        ] if live_agent else []
        if canonical:
            for extra in entries:
                if extra is not canonical[0] and _delete_spire_entry_by_id(
                    extra.get("id", ""), server_container=server_container, server_socket=server_socket
                ):
                    duplicates_removed += 1
            continue
        if live_agent:
            moved = False
            for entry in entries:
                selectors = _entry_selectors(entry)
                if selectors != [expected]:
                    if _delete_spire_entry_by_id(
                        entry.get("id", ""), server_container=server_container, server_socket=server_socket
                    ):
                        duplicates_removed += 1
                    continue
                if not moved and _reparent_spire_entry(
                    entry.get("id", ""),
                    path,
                    expected,
                    live_agent,
                    trust_domain=trust_domain,
                    server_container=server_container,
                    server_socket=server_socket,
                ):
                    reparented += 1
                    moved = True
                elif _delete_spire_entry_by_id(
                    entry.get("id", ""), server_container=server_container, server_socket=server_socket
                ):
                    duplicates_removed += 1

    removed = 0
    for name in existing_services - service_names - preserve_services:
        removed += _delete_spire_entries_by_path(
            f"/service/{name}",
            entries=existing_entries,
            server_container=server_container,
            server_socket=server_socket,
        )

    return {
        "trust_domain": trust_domain,
        "total_services": len(service_names),
        "existing_entries": len(existing_services),
        "created": created,
        "reparented": reparented,
        "duplicates_removed": duplicates_removed,
        "removed": removed,
    }


@shared_task(
    name="apps.deployments.tasks_spiffe.sync_spiffe_entries_task",
    bind=True,
    max_retries=3,
    default_retry_delay=RETRY_DELAY_FAST,
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
    acks_late=True,
)
def sync_spiffe_entries_task(self):
    """Sync SPIRE registration entries for ecosystem and platform trust domains."""
    mtls_enabled = os.getenv("MTLS_ENABLED", "true").lower() in ("true", "1", "yes")
    if not mtls_enabled:
        logger.info("mTLS disabled globally (env var), skipping SPIRE sync")
        return {"status": "skipped", "reason": "mtls_disabled"}

    eco_enabled = True
    platform_enabled = False
    try:
        from apps.deployments.models.platform import PlatformConfig
        pc = PlatformConfig.load()
        eco_enabled = bool(pc.mtls_ecosystem_enabled)
        platform_enabled = bool(getattr(pc, "mtls_enabled", False))
    except Exception:
        pass

    try:
        from apps.mtls.models import MtlsConfig
    except Exception:
        logger.warning("Models not available, skipping SPIRE sync")
        return {"status": "skipped", "reason": "models_not_found"}

    has_platform_rows = MtlsConfig.objects.filter(
        enabled=True, trust_domain=PLATFORM_SPIFFE_TRUST_DOMAIN
    ).exists()
    sync_platform = platform_enabled or has_platform_rows

    if not eco_enabled and not sync_platform:
        logger.info("mTLS ecosystem and platform disabled in PlatformConfig, skipping SPIRE sync")
        return {"status": "skipped", "reason": "mtls_ecosystem_disabled"}

    try:
        eco_result = None
        if eco_enabled:
            eco_result = _sync_domain_entries(
                MtlsConfig,
                trust_domain=ECOSYSTEM_SPIFFE_TRUST_DOMAIN,
                server_container=ECOSYSTEM_SPIRE_SERVER_CONTAINER,
                server_socket=ECOSYSTEM_SPIRE_SERVER_SOCKET,
                live_agent=_live_ecosystem_agent_id(),
                existing_entries=_list_spire_entries(),
            )
            logger.info("SPIRE ecosystem sync complete: %s", eco_result)

        platform_result = None
        if sync_platform:
            platform_entries = _list_platform_spire_entries()
            if platform_entries is not None:
                platform_result = _sync_domain_entries(
                    MtlsConfig,
                    trust_domain=PLATFORM_SPIFFE_TRUST_DOMAIN,
                    server_container=PLATFORM_SPIRE_SERVER_CONTAINER,
                    server_socket=PLATFORM_SPIRE_SERVER_SOCKET,
                    live_agent=_live_platform_agent_id(),
                    existing_entries=platform_entries,
                    preserve_services=_PLATFORM_STATIC_SERVICES,
                )
                logger.info("SPIRE platform sync complete: %s", platform_result)
            elif not eco_enabled:
                raise RuntimeError("Failed to list platform SPIRE entries; aborting sync")

        result = {"status": "ok", **(eco_result or platform_result or {})}
        if platform_result is not None:
            result["platform"] = platform_result
        return result

    except Exception as exc:
        logger.error("SPIRE sync failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


def _entry_path(entry: dict) -> str:
    """Return the SPIFFE ID path of a listed entry ('' when malformed)."""
    spiffe_id = entry.get("spiffe_id", {})
    if not isinstance(spiffe_id, dict):
        return ""
    return str(spiffe_id.get("path", "") or "")


def _entry_selectors(entry: dict) -> list:
    """Return the selector values of a listed entry.

    The server splits selectors into type/value ({"type": "docker",
    "value": "label:..."}), while creation uses the combined
    "docker:label:..." form — recombine here so comparisons match.
    """
    selectors = entry.get("selectors", [])
    if not isinstance(selectors, list):
        return []
    out = []
    for s in selectors:
        if not isinstance(s, dict):
            continue
        sel_type = str(s.get("type", "") or "")
        value = str(s.get("value", "") or "")
        out.append(f"{sel_type}:{value}" if sel_type else value)
    return out


def _entry_parent_id(entry: dict) -> str:
    """Return the full parent SPIFFE ID of a listed entry."""
    parent = entry.get("parent", {})
    if not isinstance(parent, dict):
        return ""
    return f"spiffe://{parent.get('trust_domain', '')}{parent.get('path', '')}"


def _list_spire_entries() -> list | None:
    """List all SPIRE registration entries from ecosystem server.

    Returns None when the list call itself fails — callers must abort
    rather than treat failure as "no entries" (blind-sync incident).
    """
    return _list_spire_entries_from(
        ECOSYSTEM_SPIRE_SERVER_CONTAINER, ECOSYSTEM_SPIRE_SERVER_SOCKET,
    )


def _list_platform_spire_entries() -> list | None:
    """List all SPIRE registration entries from the platform server."""
    return _list_spire_entries_from(
        PLATFORM_SPIRE_SERVER_CONTAINER, PLATFORM_SPIRE_SERVER_SOCKET,
    )


def _list_spire_entries_from(server_container: str, server_socket: str) -> list | None:
    """List registration entries from the given SPIRE server."""
    try:
        result = subprocess.run(
            [
                "docker", "exec", server_container,
                # NOTE: this image (spire 1.9.6) has `entry show`, not
                # `entry list` — list prints fallback usage with rc!=0.
                "/opt/spire/bin/spire-server", "entry", "show",
                "-socketPath", server_socket,
                "-output", "json",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            return data.get("entries", [])
        logger.warning(
            "SPIRE entry list failed (rc=%s): %s",
            result.returncode, (result.stderr or "")[:500],
        )
    except Exception as e:
        logger.warning("Failed to list SPIRE ecosystem entries: %s", e)
    return None


def _live_ecosystem_agent_id() -> str | None:
    """Discover the live ecosystem agent's SPIFFE ID.

    Workload entries are only synced to (and served for) the agent
    they are parented to, and join-token agent IDs rotate on every
    re-bootstrap. A hardcoded parent (e.g. .../spire-server) therefore
    silently stops issuing SVIDs (2026-09-08: every sidecar served
    smsly-identity-service's SVID via a stale catch-all). Picks the
    non-banned join_token agent with the latest SVID expiry.
    """
    return _live_agent_id(
        ECOSYSTEM_SPIRE_SERVER_CONTAINER,
        ECOSYSTEM_SPIRE_SERVER_SOCKET,
        ECOSYSTEM_SPIFFE_TRUST_DOMAIN,
    )


def _live_platform_agent_id() -> str | None:
    """Discover the live platform agent's SPIFFE ID (platform.local)."""
    return _live_agent_id(
        PLATFORM_SPIRE_SERVER_CONTAINER,
        PLATFORM_SPIRE_SERVER_SOCKET,
        PLATFORM_SPIFFE_TRUST_DOMAIN,
    )


def _live_agent_id(server_container: str, server_socket: str, trust_domain: str) -> str | None:
    """Discover the live join_token agent's SPIFFE ID on a SPIRE server."""
    import json

    try:
        result = subprocess.run(
            [
                "docker", "exec", server_container,
                "/opt/spire/bin/spire-server", "agent", "list",
                "-socketPath", server_socket,
                "-output", "json",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        agents = json.loads(result.stdout).get("agents", [])
        best, best_exp = None, ""
        fallback = None
        for ag in agents:
            if not isinstance(ag, dict) or ag.get("banned"):
                continue
            if ag.get("attestation_type") != "join_token":
                continue
            path = ((ag.get("id") or {}).get("path")) or ""
            if not path:
                continue
            full = f"spiffe://{trust_domain}{path}"
            if fallback is None:
                fallback = full
            exp = str(ag.get("x509svid_expires_at") or "")
            if exp >= best_exp:
                best, best_exp = full, exp
        return best or fallback
    except Exception as exc:
        logger.warning("Failed to discover live SPIRE agent: %s", exc)
        return None


def _create_spire_entry(service_name: str, parent_id: str | None = None,
                          trust_domain: str | None = None,
                          server_container: str | None = None,
                          server_socket: str | None = None) -> bool:
    """Create a SPIRE registration entry for a service.

    Defaults target the ecosystem server; pass the platform constants
    for platform.local services. Parent defaults to the live agent of
    the targeted server (discovered); callers must not use a static
    parent — agent IDs rotate on re-bootstrap.
    """
    trust_domain = trust_domain or ECOSYSTEM_SPIFFE_TRUST_DOMAIN
    server_container = server_container or ECOSYSTEM_SPIRE_SERVER_CONTAINER
    server_socket = server_socket or ECOSYSTEM_SPIRE_SERVER_SOCKET
    try:
        spiffe_id = f"spiffe://{trust_domain}/service/{service_name}"
        if not parent_id:
            if trust_domain == PLATFORM_SPIFFE_TRUST_DOMAIN:
                parent_id = _live_platform_agent_id()
            else:
                parent_id = _live_ecosystem_agent_id()
        if not parent_id:
            parent_id = f"spiffe://{trust_domain}/spire-server"
        selector = f"docker:label:com.paas.service:{service_name}"

        result = subprocess.run(
            [
                "docker", "exec", server_container,
                "/opt/spire/bin/spire-server", "entry", "create",
                "-socketPath", server_socket,
                "-spiffeID", spiffe_id,
                "-parentID", parent_id,
                "-selector", selector,
                "-ttl", "3600",
                "-dns", service_name,
                "-dns", f"{service_name}.{trust_domain.split('.')[0]}.svc",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("Created SPIRE %s entry for %s", trust_domain, service_name)
            return True
        elif "already exists" in result.stderr:
            return False
        else:
            logger.warning("Failed to create SPIRE %s entry for %s: %s", trust_domain, service_name, result.stderr)
            return False
    except Exception as e:
        logger.warning("Failed to create SPIRE %s entry for %s: %s", trust_domain, service_name, e)
        return False


def _reparent_spire_entry(
    entry_id: str,
    path: str,
    selector: str,
    parent_id: str,
    *,
    trust_domain: str | None = None,
    server_container: str | None = None,
    server_socket: str | None = None,
) -> bool:
    """Move an entry under a new parent, preserving SPIFFE ID + selectors."""
    try:
        if not entry_id:
            return False
        trust_domain = trust_domain or ECOSYSTEM_SPIFFE_TRUST_DOMAIN
        server_container = server_container or ECOSYSTEM_SPIRE_SERVER_CONTAINER
        server_socket = server_socket or ECOSYSTEM_SPIRE_SERVER_SOCKET
        spiffe_id = f"spiffe://{trust_domain}{path}"
        result = subprocess.run(
            [
                "docker", "exec", server_container,
                "/opt/spire/bin/spire-server", "entry", "update",
                "-socketPath", server_socket,
                "-entryID", entry_id,
                "-spiffeID", spiffe_id,
                "-parentID", parent_id,
                "-selector", selector,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("Re-parented SPIRE entry %s under live agent", spiffe_id)
            return True
        logger.warning("Failed to re-parent SPIRE entry %s: %s", spiffe_id, result.stderr)
        return False
    except Exception as e:
        logger.warning("Failed to re-parent SPIRE entry %s: %s", path, e)
        return False


def _delete_spire_entry_by_id(
    entry_id: str,
    *,
    server_container: str | None = None,
    server_socket: str | None = None,
) -> bool:
    """Delete a single SPIRE entry by ID. Returns True on success."""
    if not entry_id:
        return False
    server_container = server_container or ECOSYSTEM_SPIRE_SERVER_CONTAINER
    server_socket = server_socket or ECOSYSTEM_SPIRE_SERVER_SOCKET
    try:
        result = subprocess.run(
            [
                "docker", "exec", server_container,
                "/opt/spire/bin/spire-server", "entry", "delete",
                "-socketPath", server_socket,
                "-entryID", entry_id,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("Deleted SPIRE entry %s", entry_id)
            return True
        logger.warning(
            "Failed to delete SPIRE entry %s: %s", entry_id, (result.stderr or "")[:300]
        )
    except Exception as e:
        logger.warning("Failed to delete SPIRE entry %s: %s", entry_id, e)
    return False


def _delete_spire_entries_by_path(
    path: str,
    entries: list | None = None,
    *,
    server_container: str | None = None,
    server_socket: str | None = None,
) -> int:
    """Delete ALL entries for a SPIFFE ID path (dedup-safe). Returns count."""
    try:
        if entries is None:
            if server_container and server_socket:
                entries = _list_spire_entries_from(server_container, server_socket) or []
            else:
                entries = _list_spire_entries() or []
        removed = 0
        for entry in entries:
            if _entry_path(entry) == path and _delete_spire_entry_by_id(
                entry.get("id", ""),
                server_container=server_container,
                server_socket=server_socket,
            ):
                removed += 1
        return removed
    except Exception as e:
        logger.warning("Failed to delete SPIRE entries for %s: %s", path, e)
        return 0


def _delete_spire_entry(service_name: str, entries: list | None = None) -> bool:
    """Delete SPIRE registration entries for a service (all duplicates)."""
    if entries is None:
        entries = _list_spire_entries() or []
    return _delete_spire_entries_by_path(f"/service/{service_name}", entries) > 0
