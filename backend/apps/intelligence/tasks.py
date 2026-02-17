"""Tasks module."""
import logging
from celery import shared_task
from apps.deployments.models import Service
from apps.intelligence.remediator import RemediationEngine
from apps.intelligence.analyzer import LogAnalyzer

logger = logging.getLogger(__name__)


@shared_task
def detect_anomalies_task(batch_size: int = 100):
    """
    Runs periodically to check services for crash loops or OOMs.
    Batches processing to avoid OOMs on the worker itself.
    """
    analyzer = LogAnalyzer()
    remediator = RemediationEngine()

    # Process active services only
    queryset = Service.objects.all().order_by('id')

    total_count = queryset.count()
    for offset in range(0, total_count, batch_size):
        batch = queryset[offset:offset + batch_size]
        for service in batch:
            # pylint: disable=broad-exception-caught
            try:
                _process_service_anomaly(service, analyzer, remediator)
            except Exception as e:
                logger.error("Error processing service %s: %s", service.id, e)


def _process_service_anomaly(
    service: Service,
    analyzer: LogAnalyzer,
    remediator: RemediationEngine
):
    """Analyze a single service."""
    # 1. Fetch Logs (Stub - in real app, query CloudWatch/Loki)
    # logs = fetch_logs(service)
    logs = ""  # Placeholder - requires LogService integration

    if not logs:
        return

    # 2. Analyze
    issues = analyzer.analyze_logs(logs)

    for issue in issues:
        logger.info("Anomaly detected in %s: %s", service.name, issue['type'])

        # 3. Auto-Remediate (if confidence high)
        if issue['confidence'] > 0.9:
            success = remediator.apply_fix(issue['type'], str(service.id))
            if success:
                logger.info("Auto-fixed %s", service.name)
