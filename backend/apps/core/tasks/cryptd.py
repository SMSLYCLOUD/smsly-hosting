"""Periodic Celery maintenance for grid-cryptd epoch monitoring and lease renewal."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.core.tasks.cryptd.sync_cryptd_leases_task",
    soft_time_limit=120,
    time_limit=150,
    ignore_result=True,
)
def sync_cryptd_leases_task() -> dict:
    """
    Renew leases for ACTIVE opted-in services and ensure STOPPED services stay locked.

    Only executes when `grid-cryptd` is opted in at the platform level (`cryptd_client.is_enabled()`).
    """
    from apps.core.crypto_daemon import (
        CryptdError,
        cryptd_client,
        register_and_unlock_service,
    )
    from apps.deployments.models import Service

    if not cryptd_client.is_enabled():
        return {"skipped": True, "reason": "cryptd not enabled"}

    synced = 0
    locked = 0
    errors = 0

    try:
        st = cryptd_client.get_status()
        state = st.get("state")
        if state in ("GRACE", "LOCKED"):
            logger.warning("grid-cryptd epoch controller in %s state (week=%s, err=%s)",
                           state, st.get("current_week"), st.get("last_error"))
    except CryptdError as exc:
        logger.warning("grid-cryptd status probe failed: %s", exc)

    # Renew leases for ACTIVE services opted into encryption at rest.
    for svc in Service.objects.filter(encryption_at_rest_enabled=True).iterator(chunk_size=100):
        try:
            if svc.status == Service.Status.ACTIVE:
                register_and_unlock_service(svc, unlock_now=True)
                synced += 1
            elif svc.status in (Service.Status.STOPPED, Service.Status.DELETED, Service.Status.DELETION_PENDING):
                cryptd_client.lock_service(str(svc.id), reason=f"service status {svc.status}")
                locked += 1
        except Exception as exc:
            errors += 1
            logger.debug("cryptd sync failed for service %s: %s", svc.id, exc)

    return {"synced": synced, "locked": locked, "errors": errors}
