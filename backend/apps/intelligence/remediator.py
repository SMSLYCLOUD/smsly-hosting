"""Remediator module."""
from typing import Dict, Optional
import logging
import subprocess
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from apps.deployments.models import Service, Deployment
from apps.deployments.models_audit import AuditLog
from apps.deployments.tasks import smart_deploy_task
from .providers import ask_with_fallback

logger = logging.getLogger(__name__)


class RemediationEngine:
    """
    Analyzes issues and applies fixes autonomously.
    """

    MAX_MEMORY_LIMIT = 2048  # 2GB Hard Limit
    AUTO_DEPLOY_COOLDOWN_MINUTES = 10
    IN_PROGRESS_STATUSES = (
        Deployment.Status.QUEUED,
        Deployment.Status.REVIEW,
        Deployment.Status.BUILDING,
        Deployment.Status.DEPLOYING,
        Deployment.Status.HEALTH_CHECK,
    )

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
        },
        'SSL_CERT_EXPIRED': {
            'action': 'NOTIFY_ADMIN',
            'resource': 'SSL',
            'message': 'SSL certificate has expired. Trigger certificate renewal.'
        },
        'DISK_FULL': {
            'action': 'CLEANUP',
            'resource': 'DISK',
            'message': 'Disk full. Pruning old Docker images and logs.'
        },
        'PORT_CONFLICT': {
            'action': 'RESTART',
            'resource': 'CONTAINER',
            'message': 'Port conflict detected. Restarting container with port reassignment.'
        },
        'DNS_FAILURE': {
            'action': 'NOTIFY_ADMIN',
            'resource': 'DNS',
            'message': 'DNS resolution failed. Check domain configuration.'
        },
        'DEPENDENCY_MISSING': {
            'action': 'REBUILD',
            'resource': 'BUILD',
            'message': 'Missing dependency detected. Triggering fresh build.'
        },
        'BUILD_FAILURE': {
            'action': 'NOTIFY_AND_DIAGNOSE',
            'resource': 'BUILD',
            'message': 'Build failed. Running AI diagnosis on build logs.'
        },
        'TIMEOUT': {
            'action': 'SCALE_UP',
            'resource': 'REPLICAS',
            'amount': 1,
            'message': 'Request timeouts detected. Adding replica.'
        },
        'HEALTH_CHECK_FAIL': {
            'action': 'RESTART_OR_ROLLBACK',
            'resource': 'CONTAINER',
            'message': 'Health check failing. Attempting restart, then rollback if persistent.'
        },
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

                action = fix['action']

                if action == 'SCALE_UP' and fix['resource'] == 'MEMORY':
                    if service.memory_mb >= self.MAX_MEMORY_LIMIT:
                        logger.warning("Memory limit reached for %s (%sMB)", service.name, service.memory_mb)
                        return False
                    old_mem = service.memory_mb
                    service.memory_mb += fix['amount']
                    service.save()
                    AuditLog.objects.create(
                        actor="AI_REMEDIATOR",
                        action="SCALE_UP",
                        target=service.name,
                        metadata={"old_mb": old_mem, "new_mb": service.memory_mb, "reason": "OOM"}
                    )
                    return self._trigger_redeploy(service, fix['message'])

                if action == 'SCALE_UP' and fix['resource'] == 'REPLICAS':
                    service.min_replicas += 1
                    service.save()
                    AuditLog.objects.create(
                        actor="AI_REMEDIATOR",
                        action="SCALE_UP",
                        target=service.name,
                        metadata={"new_replicas": service.min_replicas, "reason": "TIMEOUT"}
                    )
                    return self._trigger_redeploy(service, fix['message'])

                if action == 'ROLLBACK':
                    return self._handle_rollback(service)

                if action == 'CLEANUP':
                    try:
                        subprocess.run(
                            ['docker', 'system', 'prune', '-f'],
                            capture_output=True, timeout=30, check=True
                        )
                        AuditLog.objects.create(
                            actor="AI_REMEDIATOR",
                            action="CLEANUP",
                            target="SYSTEM",
                            metadata={"reason": "DISK_FULL"}
                        )
                        return True
                    except Exception as e:
                        logger.error("Cleanup failed: %s", e)
                        return False

                if action == 'REBUILD':
                    # Trigger build without cache logic is tricky here, default deploy for now.
                    return self._trigger_redeploy(service, fix['message'])

                if action == 'NOTIFY_AND_DIAGNOSE':
                    last_deploy = service.deployments.filter(status='FAILED').first()
                    if last_deploy and last_deploy.build_logs:
                        prompt = f"Diagnose this build failure for {service.name}:\n\n{last_deploy.build_logs[-5000:]}"
                        try:
                            response, provider = ask_with_fallback(prompt)
                            last_deploy.ai_diagnosis = f"[{provider}] {response}"
                            last_deploy.save(update_fields=['ai_diagnosis'])
                            AuditLog.objects.create(
                                actor="AI_REMEDIATOR",
                                action="DIAGNOSE",
                                target=service.name,
                                metadata={"diagnosis": response[:200]}
                            )
                            return True
                        except Exception as e:
                            logger.error("AI diagnosis failed: %s", e)
                    return False

                if action == 'RESTART_OR_ROLLBACK':
                    # Simple restart via API call equivalent (not fully implemented here, assume redeploy works as restart)
                    # Ideally we check uptime. If short uptime, rollback. If long uptime, restart.
                    return self._trigger_redeploy(service, "Restarting due to health check failure")

                if action == 'NOTIFY_ADMIN':
                    AuditLog.objects.create(
                        actor="AI_REMEDIATOR",
                        action="NOTIFY",
                        target=service.name,
                        metadata={"message": fix['message'], "severity": "CRITICAL"}
                    )
                    return True

            return False

        except Service.DoesNotExist:
            logger.error("Service %s not found", service_id)
            return False

    def _has_in_progress_deployment(self, service: Service) -> bool:
        return service.deployments.filter(
            status__in=self.IN_PROGRESS_STATUSES
        ).exists()

    def _has_recent_auto_deployment(self, service: Service, prefix: str) -> bool:
        cutoff = timezone.now() - timedelta(minutes=self.AUTO_DEPLOY_COOLDOWN_MINUTES)
        return service.deployments.filter(
            commit_message__startswith=prefix,
            created_at__gte=cutoff,
        ).exclude(status=Deployment.Status.ACTIVE).exists()

    def _trigger_redeploy(self, service: Service, message: str):
        """Trigger a new deployment based on the latest active one."""
        if self._has_in_progress_deployment(service):
            logger.info(
                "Skipping auto-remediation deploy for %s: deployment already in progress",
                service.name,
            )
            return False

        if self._has_recent_auto_deployment(service, "Auto-Remediation:"):
            logger.info(
                "Skipping auto-remediation deploy for %s: cooldown window active",
                service.name,
            )
            return False

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
                smart_deploy_task.delay(str(new_deploy.id), provider_id, skip_review=True)
                return True
        return False

    def _handle_rollback(self, service: Service) -> bool:
        """Handle automated rollback logic."""
        if self._has_in_progress_deployment(service):
            logger.info(
                "Skipping auto-rollback for %s: deployment already in progress",
                service.name,
            )
            return False

        # Find previous successful deployment
        latest_deploy = service.deployments.order_by('-created_at').first()
        last_good_deploy = service.deployments.filter(
            status='ACTIVE'
        ).exclude(
            id=latest_deploy.id if latest_deploy else None
        ).order_by('-finished_at').first()

        if last_good_deploy:
            cutoff = timezone.now() - timedelta(minutes=self.AUTO_DEPLOY_COOLDOWN_MINUTES)
            recent_duplicate = service.deployments.filter(
                is_rollback=True,
                commit_hash=last_good_deploy.commit_hash,
                commit_message__startswith='Auto-Rollback:',
                created_at__gte=cutoff,
            ).exclude(status=Deployment.Status.ACTIVE).exists()
            if recent_duplicate:
                logger.info(
                    "Skipping auto-rollback for %s: recent rollback to commit %s already queued",
                    service.name,
                    last_good_deploy.commit_hash,
                )
                return False

            AuditLog.objects.create(
                actor="AI_REMEDIATOR",
                action="ROLLBACK",
                target=service.name,
                metadata={
                    "from_commit": latest_deploy.commit_hash if latest_deploy else "unknown",
                    "to_commit": last_good_deploy.commit_hash,
                    "reason": "CRASH_LOOP detected"
                }
            )

            new_deploy = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_hash=last_good_deploy.commit_hash,
                commit_message=f"Auto-Rollback: Reverted to {last_good_deploy.commit_hash[:7]}",
                is_rollback=True,
                rollback_from=latest_deploy,
            )

            provider_id = str(service.provider.id) if service.provider else None
            if provider_id:
                smart_deploy_task.delay(str(new_deploy.id), provider_id, skip_review=True)
                logger.info("Rollback triggered for %s", service.name)
                return True
            return False

        logger.warning("No previous good deployment found for %s", service.name)
        return False
