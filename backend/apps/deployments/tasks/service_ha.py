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

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="apps.deployments.tasks.service_ha_pass",
    soft_time_limit=90,
    time_limit=120,
)
def service_ha_pass(self):
    """Beat task (60s): one idempotent HA evaluation pass."""
    try:
        from apps.deployments.services.service_ha import ServiceHAManager

        manager = ServiceHAManager()
        results = manager.run_ha_pass()

        if results.get("failovers", 0) > 0 or results.get("escalations", 0) > 0:
            logger.info(
                "HA pass: %d checked, %d escalations, %d replica failovers, "
                "%d node failovers",
                results.get("checked", 0),
                results.get("escalations", 0),
                results.get("replica_failovers", 0),
                results.get("node_failovers", 0),
            )
            # Alert on any failovers
            for alert_msg in results.get("alerts", []):
                try:
                    from apps.core.tasks.alerts import alert_user_task
                    # Find any superuser to send the alert to
                    from django.contrib.auth import get_user_model
                    User = get_user_model()
                    admin = User.objects.filter(is_superuser=True).first()
                    if admin:
                        latest = (
                            admin.services.order_by("-created_at").first()
                            if hasattr(admin, "services") else None
                        )
                        if latest:
                            alert_user_task.delay(
                                deployment_id=str(latest.latest_deployment.id)
                                if hasattr(latest, 'latest_deployment') and latest.latest_deployment
                                else str(latest.id),
                                error_message=f"HA: {alert_msg}",
                            )
                except Exception as exc:
                    logger.debug("HA alert dispatch failed: %s", exc)

        return {
            "status": "ok",
            "checked": results.get("checked", 0),
            "escalations": results.get("escalations", 0),
            "replica_failovers": results.get("replica_failovers", 0),
            "node_failovers": results.get("node_failovers", 0),
        }

    except Exception as exc:
        logger.error("HA pass failed: %s", exc)
        return {"status": "error", "reason": str(exc)}
