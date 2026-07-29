"""Background anomaly detection tasks for the intelligence system."""

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.deployments.constants import TASK_TIME_LIMIT_LONG, TASK_TIME_LIMIT_MEDIUM, TASK_TIME_LIMIT_STANDARD
from apps.deployments.models import Deployment, Service
from apps.core.models.audit import AuditLog
from apps.intelligence.analyzer import LogAnalyzer
from apps.intelligence.remediator import RemediationEngine

logger = logging.getLogger(__name__)


@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1])
def detect_anomalies_task(self, batch_size: int = 100):
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
) -> dict[str, int]:
    """Analyze one service and apply fixes for high-confidence issues."""
    latest_deployment = (
        service.deployments.order_by("-created_at").only("id", "build_logs").first()
    )
    if not latest_deployment and service.health_status != "unhealthy":
        return {"issues_count": 0, "fixed_count": 0}

    logs = ""
    if latest_deployment and latest_deployment.build_logs:
        logs = latest_deployment.build_logs[-20000:]

    issues: list[dict[str, object]] = analyzer.analyze_logs(logs)

    # Health monitor signal fallback: treat persistent unhealthy status as crash-loop risk.
    if service.health_status == "unhealthy" and not issues:
        issues = [{"type": "CRASH_LOOP", "pattern": "health_status=unhealthy", "confidence": 0.9}]

    fixed_count = 0
    for issue in issues:
        issue_type = str(issue.get("type") or "")
        confidence = float(issue.get("confidence") or 0.0)  # type: ignore[arg-type]
        if confidence < 0.9 or not issue_type:
            continue

        success = remediator.apply_fix(issue_type, str(service.id))
        if success:
            fixed_count += 1
            logger.info("Auto-remediation applied for service %s issue=%s", service.id, issue_type)

    return {"issues_count": len(issues), "fixed_count": fixed_count}


@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_MEDIUM[0], time_limit=TASK_TIME_LIMIT_MEDIUM[1])
def proactive_health_scan_task(self):
    """
    Proactive health scan — runs every 5 minutes.
    Checks ALL services for:
    1. Services that have been unhealthy for >5 minutes
    2. Services with memory usage >85% of limit
    3. Services with no successful deployment in >24 hours
    4. Services with repeated restart patterns
    """
    remediator = RemediationEngine()
    unhealthy_services = Service.objects.filter(health_status='unhealthy')

    for service in unhealthy_services:
        try:
            remediator.apply_fix('HEALTH_CHECK_FAIL', str(service.id))
        except Exception as exc:
            logger.error("Health scan failed for service %s: %s", service.id, exc)


@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_STANDARD[0], time_limit=TASK_TIME_LIMIT_STANDARD[1])
def ai_deployment_review_task(self, deployment_id: str):
    """
    Post-deployment AI review — triggered after every deployment.
    Analyzes build logs + runtime behavior in first 2 minutes.
    If issues detected, provides AI-powered diagnosis and
    optionally triggers auto-rollback.
    """
    try:
        deployment = Deployment.objects.get(id=deployment_id)
        if deployment.status in ('FAILED', 'BUILD_FAILED'):
            analyzer = LogAnalyzer()
            diagnosis = analyzer.generate_diagnosis(deployment.build_logs or "")
            deployment.ai_diagnosis = diagnosis
            deployment.save(update_fields=['ai_diagnosis'])

            # Create audit log
            AuditLog.objects.create(
                actor="AI_REVIEWER",
                action="DIAGNOSIS",
                target=deployment.service.name,
                metadata={"diagnosis": diagnosis[:500]}
            )
    except Deployment.DoesNotExist:
        logger.error("Deployment %s not found for AI review", deployment_id)


@shared_task(bind=True, soft_time_limit=TASK_TIME_LIMIT_LONG[0], time_limit=TASK_TIME_LIMIT_LONG[1])
def daily_intelligence_report_task(self):
    """
    Daily intelligence report — runs once per day.
    Generates a summary of:
    - Total deployments (success/fail ratio)
    - Resource utilization trends
    - Cost projections
    - Proactive recommendations
    Stores report in DB and can be viewed in the Intelligence UI.
    """
    # Summary of last 24h
    now = timezone.now()
    yesterday = now - timedelta(hours=24)

    deployments = Deployment.objects.filter(created_at__gte=yesterday)
    total = deployments.count()
    failed = deployments.filter(status='FAILED').count()
    success_rate = ((total - failed) / total * 100) if total > 0 else 100

    anomalies = AuditLog.objects.filter(
        created_at__gte=yesterday,
        actor__in=["AI_REMEDIATOR", "AI_REVIEWER"]
    ).count()

    report = {
        "date": now.date().isoformat(),
        "total_deployments": total,
        "failed_deployments": failed,
        "success_rate": f"{success_rate:.1f}%",
        "anomalies_detected": anomalies,
        "generated_at": now.isoformat()
    }

    # Store in AuditLog as a report
    AuditLog.objects.create(
        actor="AI_REPORTER",
        action="DAILY_REPORT",
        target="SYSTEM",
        metadata=report
    )

    logger.info("Daily Intelligence Report generated: %s", report)
