"""Background anomaly detection tasks for the intelligence system."""

import logging
from typing import Dict, List

from celery import shared_task

from apps.deployments.models import Service
from apps.intelligence.analyzer import LogAnalyzer
from apps.intelligence.remediator import RemediationEngine

logger = logging.getLogger(__name__)


@shared_task
def detect_anomalies_task(batch_size: int = 100):
    """
    Periodically scan services for anomaly patterns and auto-remediate when safe.

    Uses latest deployment logs + service health status as inputs.
    """
    analyzer = LogAnalyzer()
    remediator = RemediationEngine()

    queryset = Service.objects.order_by("id")
    total_count = queryset.count()
    summary = {
        "checked": 0,
        "issues_detected": 0,
        "auto_fixed": 0,
        "errors": 0,
    }

    for offset in range(0, total_count, batch_size):
        batch = queryset[offset:offset + batch_size]
        for service in batch:
            try:
                outcome = _process_service_anomaly(service, analyzer, remediator)
                summary["checked"] += 1
                summary["issues_detected"] += int(outcome.get("issues_count", 0))
                summary["auto_fixed"] += int(outcome.get("fixed_count", 0))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                summary["errors"] += 1
                logger.error("Error processing service %s: %s", service.id, exc)

    logger.info("Intelligence anomaly scan summary: %s", summary)
    return summary


def _process_service_anomaly(
    service: Service,
    analyzer: LogAnalyzer,
    remediator: RemediationEngine,
) -> Dict[str, int]:
    """Analyze one service and apply fixes for high-confidence issues."""
    latest_deployment = (
        service.deployments.order_by("-created_at").only("id", "build_logs").first()
    )
    if not latest_deployment and service.health_status != "unhealthy":
        return {"issues_count": 0, "fixed_count": 0}

    logs = ""
    if latest_deployment and latest_deployment.build_logs:
        logs = latest_deployment.build_logs[-20000:]

    issues: List[Dict[str, object]] = analyzer.analyze_logs(logs)

    # Health monitor signal fallback: treat persistent unhealthy status as crash-loop risk.
    if service.health_status == "unhealthy" and not issues:
        issues = [{"type": "CRASH_LOOP", "pattern": "health_status=unhealthy", "confidence": 0.9}]

    fixed_count = 0
    for issue in issues:
        issue_type = str(issue.get("type") or "")
        confidence = float(issue.get("confidence") or 0.0)
        if confidence < 0.9 or not issue_type:
            continue

        success = remediator.apply_fix(issue_type, str(service.id))
        if success:
            fixed_count += 1
            logger.info("Auto-remediation applied for service %s issue=%s", service.id, issue_type)

    return {"issues_count": len(issues), "fixed_count": fixed_count}
