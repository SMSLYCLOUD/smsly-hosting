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
        existing_services = set()
        for entry in existing_entries:
            path = entry.get("spiffe_id", {}).get("path", "")
            if path.startswith("/service/"):
                existing_services.add(path[len("/service/"):])

        created = 0
        for name in service_names - existing_services:
            if _create_spire_entry(name):
                created += 1

        removed = 0
        for name in existing_services - service_names:
            if _delete_spire_entry(name, existing_entries):
                removed += 1

        result = {
            "status": "ok",
            "trust_domain": ECOSYSTEM_SPIFFE_TRUST_DOMAIN,
            "total_services": len(service_names),
            "existing_entries": len(existing_services),
            "created": created,
            "removed": removed,
        }
        logger.info("SPIRE ecosystem sync complete: %s", result)
        return result

    except Exception as exc:
        logger.error("SPIRE ecosystem sync failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


def _list_spire_entries() -> list:
    """List all SPIRE registration entries from ecosystem server."""
    try:
        result = subprocess.run(
            [
                "docker", "exec", ECOSYSTEM_SPIRE_SERVER_CONTAINER,
                "/opt/spire/bin/spire-server", "entry", "list",
                "-socketPath", ECOSYSTEM_SPIRE_SERVER_SOCKET,
                "-output", "json",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout)
            return data.get("entries", [])
    except Exception as e:
        logger.warning("Failed to list SPIRE ecosystem entries: %s", e)
    return []


def _create_spire_entry(service_name: str) -> bool:
    """Create a SPIRE registration entry in the ecosystem server for a service."""
    try:
        spiffe_id = f"spiffe://{ECOSYSTEM_SPIFFE_TRUST_DOMAIN}/service/{service_name}"
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


def _delete_spire_entry(service_name: str, entries: list | None = None) -> bool:
    """Delete a SPIRE registration entry from the ecosystem server for a service."""
    try:
        if entries is None:
            entries = _list_spire_entries()
        for entry in entries:
            if entry.get("spiffe_id", {}).get("path", "") == f"/service/{service_name}":
                entry_id = entry.get("id", "")
                if entry_id:
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
                        logger.info("Deleted SPIRE ecosystem entry for %s", service_name)
                        return True
    except Exception as e:
        logger.warning("Failed to delete SPIRE ecosystem entry for %s: %s", service_name, e)
    return False
