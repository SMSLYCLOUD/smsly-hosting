"""Remediator module."""
from typing import Dict, Optional
import logging
from django.db import transaction
from apps.deployments.models import Service, Deployment
from apps.deployments.models_audit import AuditLog
from apps.deployments.tasks import smart_deploy_task

logger = logging.getLogger(__name__)


class RemediationEngine:
    """
    Analyzes issues and applies fixes autonomously.
    """

    MAX_MEMORY_LIMIT = 2048  # 2GB Hard Limit

    RECOMMENDATIONS = {
        'OOM_KILLED': {
            'action': 'SCALE_UP',
            'resource': 'MEMORY',
            'amount': 256,  # +256MB
            'message': 'Increased memory limit by 256MB.'
        },
        'DB_CONNECTION_TIMEOUT': {
            'action': 'SCALE_UP_POOL',
            'resource': 'DB_POOL',
            'amount': 20,
            'message': (
                'Database connection pool exhausted. '
                'Increase connection limit or optimize queries.'
            )
        },
        'CRASH_LOOP': {
            'action': 'ROLLBACK',
            'resource': 'DEPLOYMENT',
            'message': 'Reverted to previous successful deployment.'
        }
    }

    def suggest_fix(self, issue_type: str) -> Optional[Dict]:
        """Return the recommended fix for a given issue type."""
        return self.RECOMMENDATIONS.get(issue_type)

    def apply_fix(self, issue_type: str, service_id: str) -> bool:
        # pylint: disable=inconsistent-return-statements
        """
        Executes the fix for the given issue.
        """
        try:
            with transaction.atomic():
                # Lock service row to prevent concurrent remediation races
                service = Service.objects.select_for_update().get(id=service_id)
                fix = self.RECOMMENDATIONS.get(issue_type)

                if not fix:
                    logger.warning("No fix found for %s", issue_type)
                    return False

                if fix['action'] == 'SCALE_UP' and fix['resource'] == 'MEMORY':
                    if service.memory_mb >= self.MAX_MEMORY_LIMIT:
                        logger.warning(
                            "Memory limit reached for %s (%sMB)",
                            service.name, service.memory_mb
                        )
                        return False

                    old_mem = service.memory_mb
                    service.memory_mb += fix['amount']
                    service.save()

                    # Log to Immutable Audit
                    AuditLog.objects.create(
                        actor="AI_REMEDIATOR",
                        action="SCALE_UP",
                        target=service.name,
                        metadata={
                            "old_mb": old_mem,
                            "new_mb": service.memory_mb,
                            "reason": "OOM"}
                    )

                    # Trigger Redeploy
                    self._trigger_redeploy(service, fix['message'])
                    return True

                if fix['action'] == 'ROLLBACK':
                    return self._handle_rollback(service)

            return False

        except Service.DoesNotExist:
            logger.error("Service %s not found", service_id)
            return False

    def _trigger_redeploy(self, service: Service, message: str):
        """Trigger a new deployment based on the latest active one."""
        last_deploy = service.deployments.filter(status='ACTIVE').first()
        if last_deploy:
            new_deploy = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_hash=last_deploy.commit_hash,
                commit_message=f"Auto-Remediation: {message}"
            )
            provider_id = str(service.provider.id) if service.provider else None
            if provider_id:
                smart_deploy_task.delay(str(new_deploy.id), provider_id)

    def _handle_rollback(self, service: Service) -> bool:
        """Handle automated rollback logic."""
        # Find previous successful deployment
        last_good_deploy = service.deployments.filter(
            status='ACTIVE'
        ).exclude(
            id=service.deployments.latest(
                'created_at').id if service.deployments.exists() else None
        ).order_by('-finished_at').first()

        if last_good_deploy:
            AuditLog.objects.create(
                actor="AI_REMEDIATOR",
                action="ROLLBACK",
                target=service.name,
                metadata={
                    "from_commit": service.deployments.latest(
                        'created_at').commit_hash if service.deployments.exists() else "unknown",
                    "to_commit": last_good_deploy.commit_hash,
                    "reason": "CRASH_LOOP detected"
                }
            )

            new_deploy = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_hash=last_good_deploy.commit_hash,
                commit_message=f"Auto-Rollback: Reverted to {last_good_deploy.commit_hash[:7]}"
            )

            provider_id = str(service.provider.id) if service.provider else None
            if provider_id:
                smart_deploy_task.delay(str(new_deploy.id), provider_id)
                logger.info("Rollback triggered for %s", service.name)
                return True
            return False

        logger.warning("No previous good deployment found for %s", service.name)
        return False
