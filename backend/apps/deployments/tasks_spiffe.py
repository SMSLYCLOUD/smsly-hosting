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
    """Sync SPIRE registration entries with all deployed services.

    1. Get all services with mTLS enabled (ecosystem trust domain only)
    2. List existing SPIRE entries from ecosystem server
    3. Create missing entries
    4. Delete entries for removed services
    """
    mtls_enabled = os.getenv("MTLS_ENABLED", "true").lower() in ("true", "1", "yes")
    if not mtls_enabled:
        logger.info("mTLS disabled globally (env var), skipping SPIRE sync")
        return {"status": "skipped", "reason": "mtls_disabled"}

    # Also check PlatformConfig DB toggle
    try:
        from apps.deployments.models.platform import PlatformConfig
        pc = PlatformConfig.load()
        if not pc.mtls_ecosystem_enabled:
            logger.info("mTLS ecosystem disabled in PlatformConfig, skipping SPIRE sync")
            return {"status": "skipped", "reason": "mtls_ecosystem_disabled"}
    except Exception:
        pass

    try:
        from apps.mtls.models import MtlsConfig
    except Exception:
        logger.warning("Models not available, skipping SPIRE sync")
        return {"status": "skipped", "reason": "models_not_found"}

    try:
        enabled_configs = MtlsConfig.objects.filter(
            enabled=True, trust_domain=ECOSYSTEM_SPIFFE_TRUST_DOMAIN
        ).select_related("service")
        service_names = {cfg.service.name for cfg in enabled_configs}

        existing_entries = _list_spire_entries()
        if existing_entries is None:
            # The list call failed (server unreachable, exec error). Aborting
            # here is critical: the old code treated failure as "zero
            # entries", reported a false success, and silently skipped every
            # create (2026-09-09: backend sidecar denied SDS for hours while
            # the sync logged existing=0/created=0).
            raise RuntimeError("Failed to list SPIRE entries; aborting sync")
        by_path: dict[str, list] = {}
        for entry in existing_entries:
            path = _entry_path(entry)
            if path.startswith("/service/"):
                by_path.setdefault(path, []).append(entry)
        existing_services = {path[len("/service/"):] for path in by_path}

        live_agent = _live_ecosystem_agent_id()

        created = 0
        for name in service_names - existing_services:
            if _create_spire_entry(name, parent_id=live_agent):
                created += 1

        # Converge: exactly ONE canonical entry per live service —
        # selectors == [docker:label:com.paas.service:<name>] and parented
        # to the live agent. Extra entries for the same SPIFFE ID (legacy
        # spire-server parents, rotated-agent parents, exact duplicates)
        # are deleted, never left to accumulate. A stale-parented entry is
        # re-parented ONLY when no canonical entry already exists —
        # otherwise reparenting manufactures an exact duplicate.
        reparented = 0
        duplicates_removed = 0
        for name in service_names:
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
                        extra.get("id", "")
                    ):
                        duplicates_removed += 1
                continue
            if live_agent:
                moved = False
                for entry in entries:
                    selectors = _entry_selectors(entry)
                    if selectors != [expected]:
                        # Non-canonical entry for a live name (stale
                        # catch-all/multi-selector): it can issue this
                        # service's SVID to unrelated workloads
                        # (2026-09-08 incident). Remove it.
                        if _delete_spire_entry_by_id(entry.get("id", "")):
                            duplicates_removed += 1
                        continue
                    if not moved and _reparent_spire_entry(
                        entry.get("id", ""), path, expected, live_agent
                    ):
                        reparented += 1
                        moved = True
                    elif _delete_spire_entry_by_id(entry.get("id", "")):
                        duplicates_removed += 1

        removed = 0
        for name in existing_services - service_names:
            removed += _delete_spire_entries_by_path(f"/service/{name}")

        result = {
            "status": "ok",
            "trust_domain": ECOSYSTEM_SPIFFE_TRUST_DOMAIN,
            "total_services": len(service_names),
            "existing_entries": len(existing_services),
            "created": created,
            "reparented": reparented,
            "duplicates_removed": duplicates_removed,
            "removed": removed,
        }
        logger.info("SPIRE ecosystem sync complete: %s", result)
        return result

    except Exception as exc:
        logger.error("SPIRE ecosystem sync failed: %s", exc, exc_info=True)
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
    try:
        result = subprocess.run(
            [
                "docker", "exec", ECOSYSTEM_SPIRE_SERVER_CONTAINER,
                # NOTE: this image (spire 1.9.6) has `entry show`, not
                # `entry list` — list prints fallback usage with rc!=0.
                "/opt/spire/bin/spire-server", "entry", "show",
                "-socketPath", ECOSYSTEM_SPIRE_SERVER_SOCKET,
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
    import json

    try:
        result = subprocess.run(
            [
                "docker", "exec", ECOSYSTEM_SPIRE_SERVER_CONTAINER,
                "/opt/spire/bin/spire-server", "agent", "list",
                "-socketPath", ECOSYSTEM_SPIRE_SERVER_SOCKET,
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
            full = f"spiffe://{ECOSYSTEM_SPIFFE_TRUST_DOMAIN}{path}"
            if fallback is None:
                fallback = full
            exp = str(ag.get("x509svid_expires_at") or "")
            if exp >= best_exp:
                best, best_exp = full, exp
        return best or fallback
    except Exception as exc:
        logger.warning("Failed to discover live SPIRE agent: %s", exc)
        return None


def _create_spire_entry(service_name: str, parent_id: str | None = None) -> bool:
    """Create a SPIRE registration entry in the ecosystem server for a service.

    Parent defaults to the live agent (discovered); callers must not use
    a static parent — agent IDs rotate on re-bootstrap.
    """
    try:
        spiffe_id = f"spiffe://{ECOSYSTEM_SPIFFE_TRUST_DOMAIN}/service/{service_name}"
        if not parent_id:
            parent_id = _live_ecosystem_agent_id()
        if not parent_id:
            parent_id = f"spiffe://{ECOSYSTEM_SPIFFE_TRUST_DOMAIN}/spire-server"
        selector = f"docker:label:com.paas.service:{service_name}"

        result = subprocess.run(
            [
                "docker", "exec", ECOSYSTEM_SPIRE_SERVER_CONTAINER,
                "/opt/spire/bin/spire-server", "entry", "create",
                "-socketPath", ECOSYSTEM_SPIRE_SERVER_SOCKET,
                "-spiffeID", spiffe_id,
                "-parentID", parent_id,
                "-selector", selector,
                "-ttl", "3600",
                "-dns", service_name,
                "-dns", f"{service_name}.ecosystem.svc",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("Created SPIRE ecosystem entry for %s", service_name)
            return True
        elif "already exists" in result.stderr:
            return False
        else:
            logger.warning("Failed to create SPIRE ecosystem entry for %s: %s", service_name, result.stderr)
            return False
    except Exception as e:
        logger.warning("Failed to create SPIRE ecosystem entry for %s: %s", service_name, e)
        return False


def _reparent_spire_entry(entry_id: str, path: str, selector: str, parent_id: str) -> bool:
    """Move an entry under a new parent, preserving SPIFFE ID + selectors."""
    try:
        if not entry_id:
            return False
        trust_domain = ECOSYSTEM_SPIFFE_TRUST_DOMAIN
        spiffe_id = f"spiffe://{trust_domain}{path}"
        result = subprocess.run(
            [
                "docker", "exec", ECOSYSTEM_SPIRE_SERVER_CONTAINER,
                "/opt/spire/bin/spire-server", "entry", "update",
                "-socketPath", ECOSYSTEM_SPIRE_SERVER_SOCKET,
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


def _delete_spire_entry_by_id(entry_id: str) -> bool:
    """Delete a single SPIRE entry by ID. Returns True on success."""
    if not entry_id:
        return False
    try:
        result = subprocess.run(
            [
                "docker", "exec", ECOSYSTEM_SPIRE_SERVER_CONTAINER,
                "/opt/spire/bin/spire-server", "entry", "delete",
                "-socketPath", ECOSYSTEM_SPIRE_SERVER_SOCKET,
                "-entryID", entry_id,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            logger.info("Deleted SPIRE ecosystem entry %s", entry_id)
            return True
        logger.warning(
            "Failed to delete SPIRE entry %s: %s", entry_id, (result.stderr or "")[:300]
        )
    except Exception as e:
        logger.warning("Failed to delete SPIRE entry %s: %s", entry_id, e)
    return False


def _delete_spire_entries_by_path(path: str, entries: list | None = None) -> int:
    """Delete ALL entries for a SPIFFE ID path (dedup-safe). Returns count."""
    try:
        if entries is None:
            entries = _list_spire_entries() or []
        removed = 0
        for entry in entries:
            if _entry_path(entry) == path and _delete_spire_entry_by_id(
                entry.get("id", "")
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
