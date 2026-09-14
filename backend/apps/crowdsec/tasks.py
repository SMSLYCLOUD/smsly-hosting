"""Periodic CrowdSec hygiene: configurable automatic unblocking."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK

logger = logging.getLogger(__name__)


def _auto_unblock_config() -> tuple[bool, int]:
    """Return (enabled, max_age_hours) with fail-safe defaults."""
    try:
        from apps.deployments.models import PlatformConfig

        config = PlatformConfig.load()
        enabled = getattr(config, "crowdsec_auto_unblock_enabled", True)
        hours = getattr(config, "crowdsec_auto_unblock_after_hours", 24)
        try:
            hours = int(hours)
        except (TypeError, ValueError):
            hours = 24
        if hours < 1:
            hours = 1
        return bool(enabled), hours
    except Exception as exc:
        logger.debug("CrowdSec auto-unblock config unreadable: %s", exc)
        return True, 24


@shared_task(
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
    name="apps.crowdsec.tasks.crowdsec_auto_unblock",
)
def crowdsec_auto_unblock():
    """Remove bans older than the configured retention window.

    CrowdSec scenario bans carry their own durations, but alert-only
    records, LAPI leftovers, and stuck decisions can linger past any
    sane lifetime. This sweeper deletes Ip/Range bans whose observed
    start is older than ``crowdsec_auto_unblock_after_hours`` — unless
    the operator disabled automatic unblocking, in which case every ban
    requires a manual Unblock click. Simulated decisions are never
    touched. Returns counters for logging/alerting.
    """
    from .services import _parse_dt, get_crowdsec_service

    enabled, max_age_hours = _auto_unblock_config()
    if not enabled:
        return {"status": "ok", "mode": "disabled", "unbanned": 0}

    try:
        service = get_crowdsec_service()
        decisions = service.get_decisions(active=False, limit=500)
    except Exception as exc:
        logger.error("CrowdSec auto-unblock: decision fetch failed: %s", exc)
        return {"status": "error", "reason": str(exc)}

    now = datetime.now(timezone.utc)
    unbanned: list[str] = []
    failed: list[str] = []
    skipped = 0
    for decision in decisions:
        try:
            if decision.simulated:
                skipped += 1
                continue
            scope = (decision.scope or "").strip().lower()
            if scope == "ip":
                range_type = "Ip"
            elif scope == "range":
                range_type = "Range"
            else:
                # Unknown scope — never auto-delete what we can't classify.
                skipped += 1
                continue
            if not decision.value:
                skipped += 1
                continue
            start = _parse_dt(decision.start_time) or _parse_dt(
                decision.first_seen
            )
            if start is None:
                # No trustworthy age — leave for manual review.
                skipped += 1
                continue
            age_hours = (now - start).total_seconds() / 3600.0
            if age_hours < max_age_hours:
                skipped += 1
                continue
            result = service.unban(decision.value, range_type)
            if "error" in result:
                failed.append(decision.value)
                logger.warning(
                    "CrowdSec auto-unblock: failed to remove %s: %s",
                    decision.value, result["error"],
                )
            else:
                unbanned.append(decision.value)
                logger.info(
                    "CrowdSec auto-unblock: removed %s ban for %s (age %.1fh)",
                    decision.type or decision.scope, decision.value, age_hours,
                )
        except Exception:
            logger.exception(
                "CrowdSec auto-unblock failed for decision %s",
                getattr(decision, "id", "?"),
            )
            failed.append(getattr(decision, "value", "?") or "?")

    return {
        "status": "ok",
        "mode": "enabled",
        "unbanned": len(unbanned),
        "failed": len(failed),
        "skipped": skipped,
        "sample": unbanned[:10],
    }
