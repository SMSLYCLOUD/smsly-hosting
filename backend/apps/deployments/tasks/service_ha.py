"""Service HA — beat task wrapper for the ServiceHAManager.

Runs every 60 seconds. Idempotent: each pass re-evaluates state and
takes at most one action per service. Three layers:

  L1: Escalation alerts for services stuck in needs_manual_intervention
  L2: Replica failover for unhealthy multi-replica services
  L3: Node failover — respawn services from offline nodes elsewhere
"""
from __future__ import annotations

import logging

from celery import shared_task

from apps.deployments.constants import TASK_TIME_LIMIT_QUICK

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="apps.deployments.tasks.service_ha_pass",
    soft_time_limit=TASK_TIME_LIMIT_QUICK[0],
    time_limit=TASK_TIME_LIMIT_QUICK[1],
)
def service_ha_pass(self):
    """Beat task (60s): one idempotent HA evaluation pass."""
    try:
        from apps.deployments.services.service_ha import ServiceHAManager

        manager = ServiceHAManager()
        results = manager.run_ha_pass()

        total_failovers = (
            int(results.get("node_failovers", 0) or 0)
            + int(results.get("remote_failovers", 0) or 0)
        )
        if total_failovers > 0 or int(results.get("escalations", 0) or 0) > 0:
            logger.info(
                "HA pass: %d checked, %d escalations, %d remote failovers, "
                "%d node failovers, %d local reconciles",
                results.get("checked", 0),
                results.get("escalations", 0),
                results.get("remote_failovers", 0),
                results.get("node_failovers", 0),
                results.get("local_reconciles", 0),
            )
            # Alert on any failovers
            for alert_msg in results.get("alerts", []):
                try:
                    from apps.core.tasks.alerts import alert_user_task
                    from apps.deployments.models import Deployment
                    latest_dep = (
                        Deployment.objects.filter(service__isnull=False)
                        .order_by("-created_at")
                        .first()
                    )
                    if latest_dep:
                        alert_user_task.delay(
                            deployment_id=str(latest_dep.id),
                            error_message=f"HA: {alert_msg}",
                        )
                except Exception as exc:
                    logger.debug("HA alert dispatch failed: %s", exc)

        return {
            "status": "ok",
            "checked": results.get("checked", 0),
            "escalations": results.get("escalations", 0),
            "local_reconciles": results.get("local_reconciles", 0),
            "remote_failovers": results.get("remote_failovers", 0),
            "node_failovers": results.get("node_failovers", 0),
        }

    except Exception as exc:
        logger.error("HA pass failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
