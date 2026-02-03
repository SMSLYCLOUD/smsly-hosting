"""Remediator module."""
from typing import Dict, List, Optional
import logging
from apps.deployments.models import Service, Deployment
from apps.deployments.models_audit import AuditLog

logger = logging.getLogger(__name__)


class RemediationEngine:
    """
    Analyzes issues and applies fixes autonomously.
    """

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
            'message': 'Database connection pool exhausted. Increase connection limit or optimize queries.'
        },
        'CRASH_LOOP': {
            'action': 'ROLLBACK',
            'resource': 'DEPLOYMENT',
            'message': 'Reverted to previous successful deployment.'
        }
    }

    def suggest_fix(self, issue_type: str) -> Optional[Dict]:
        return self.RECOMMENDATIONS.get(issue_type)

    def apply_fix(self, issue_type: str, service_id: str) -> bool:
        """
        Executes the fix for the given issue.
        """
        try:
            service = Service.objects.get(id=service_id)
            fix = self.RECOMMENDATIONS.get(issue_type)

            if not fix:
                logger.warning(f"No fix found for {issue_type}")
                return False

            if fix['action'] == 'SCALE_UP' and fix['resource'] == 'MEMORY':
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
                from apps.deployments.tasks import smart_deploy_task
                # Find latest active deployment to redeploy
                last_deploy = service.deployments.filter(
                    status='ACTIVE').first()
                if last_deploy:
                    new_deploy = Deployment.objects.create(
                        service=service,
                        status=Deployment.Status.QUEUED,
                        commit_hash=last_deploy.commit_hash,
                        commit_message=f"Auto-Remediation: {fix['message']}"
                    )
                    # Using local provider/existing one
                    provider_id = str(
                        service.provider.id) if service.provider else None
                    if provider_id:
                        smart_deploy_task.delay(
                            str(new_deploy.id), provider_id)

                return True

            elif fix['action'] == 'ROLLBACK':
                # Find previous successful deployment
                last_good_deploy = service.deployments.filter(
                    status='ACTIVE'
                ).exclude(
                    id=service.deployments.latest(
                        'created_at').id if service.deployments.exists() else None
                ).order_by('-finished_at').first()

                if last_good_deploy:
                    # Create audit record
                    AuditLog.objects.create(
                        actor="AI_REMEDIATOR",
                        action="ROLLBACK",
                        target=service.name,
                        metadata={
                            "from_commit": service.deployments.latest('created_at').commit_hash if service.deployments.exists() else "unknown",
                            "to_commit": last_good_deploy.commit_hash,
                            "reason": "CRASH_LOOP detected"
                        }
                    )

                    # Trigger redeploy with last good commit
                    new_deploy = Deployment.objects.create(
                        service=service,
                        status=Deployment.Status.QUEUED,
                        commit_hash=last_good_deploy.commit_hash,
                        commit_message=f"Auto-Rollback: Reverted to {last_good_deploy.commit_hash[:7]}"
                    )

                    provider_id = str(
                        service.provider.id) if service.provider else None
                    if provider_id:
                        smart_deploy_task.delay(
                            str(new_deploy.id), provider_id)
                        logger.info(
                            f"Rollback triggered for {service.name} to commit {last_good_deploy.commit_hash[:7]}")
                        return True
                else:
                    logger.warning(
                        f"No previous good deployment found for {service.name}")
                    return False

            return False

        except Service.DoesNotExist:
            logger.error(f"Service {service_id} not found")
            return False
