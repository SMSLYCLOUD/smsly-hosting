"""Remediator module."""
import logging
import subprocess
from datetime import timedelta
from typing import Any

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.deployments.models import Deployment, Service
from apps.deployments.models_audit import AuditLog
from apps.deployments.tasks_deploy import (
    _resolve_provider_for_service,
    enqueue_smart_deploy_task,
)

from .providers import _cached_ask

logger = logging.getLogger(__name__)


class RemediationEngine:
    """
    Analyzes issues and applies fixes autonomously.
    """

    MAX_MEMORY_LIMIT = 2048  # 2GB Hard Limit
    AUTO_DEPLOY_COOLDOWN_MINUTES = 10
    DOCKER_PRUNE_COOLDOWN_SECONDS = 24 * 60 * 60
    IN_PROGRESS_STATUSES = (
        Deployment.Status.QUEUED,
        Deployment.Status.REVIEW,
        Deployment.Status.BUILDING,
        Deployment.Status.DEPLOYING,
        Deployment.Status.HEALTH_CHECK,
    )

    RECOMMENDATIONS: dict[str, dict[str, Any]] = {
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

    def suggest_fix(self, issue_type: str) -> dict | None:
        """Return the recommended fix for a given issue type."""
        rec: dict | None = self.RECOMMENDATIONS.get(issue_type)
        return rec

    def apply_fix(self, issue_type: str, service_id: str, explicit_admin: bool = False) -> bool:
        # pylint: disable=inconsistent-return-statements
        """
        Executes the fix for the given issue.

        ``explicit_admin`` must be True for high-risk side-effects
        (e.g. ``docker system prune``). The proactive scan must NOT
        pass this flag, ensuring destructive cleanup only happens
        when an admin deliberately triggers it.
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
                    if not explicit_admin:
                        logger.warning(
                            "Refusing to run docker system prune for %s: "
                            "not triggered by an explicit admin action",
                            service.name,
                        )
                        return False
                    server_id = str(getattr(service, "server_id", None) or "default")
                    prune_key = f"docker_prune:{server_id}"
                    if cache.get(prune_key):
                        logger.warning(
                            "docker system prune skipped for %s: rate limited "
                            "(last run within 24h)",
                            service.name,
                        )
                        return False
                    try:
                        result = subprocess.run(
                            ['docker', 'system', 'prune', '-f'],
                            capture_output=True, timeout=30, check=True,
                            text=True,
                        )
                        logger.info(
                            "docker system prune output for %s: stdout=%s stderr=%s",
                            service.name, result.stdout, result.stderr,
                        )
                        cache.set(prune_key, timezone.now().isoformat(), self.DOCKER_PRUNE_COOLDOWN_SECONDS)
                        AuditLog.objects.create(
                            actor="AI_REMEDIATOR",
                            action="CLEANUP",
                            target="SYSTEM",
                            metadata={"reason": "DISK_FULL", "server_id": server_id}
                        )
                        return True
                    except Exception as e:
                        logger.error(
                            "docker system prune failed for %s: %s",
                            service.name, e,
                        )
                        return False

                if action == 'REBUILD':
                    return self._trigger_redeploy(service, fix['message'])

                if action == 'NOTIFY_AND_DIAGNOSE':
                    last_deploy = service.deployments.filter(status='FAILED').first()
                    if last_deploy and last_deploy.build_logs:
                        prompt = f"Diagnose this build failure for {service.name}:\n\n{last_deploy.build_logs[-5000:]}"
                        try:
                            response, provider = _cached_ask(prompt)
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
            status__in=self.IN_PROGRESS_STATUSES,
        ).exists()

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
            provider = _resolve_provider_for_service(service)
            if not provider:
                logger.warning(
                    "Skipping auto-remediation deploy for %s: no active provider",
                    service.name,
                )
                return False
            new_deploy = Deployment.objects.create(
                service=service,
                status=Deployment.Status.QUEUED,
                commit_hash=last_deploy.commit_hash,
                commit_message=f"Auto-Remediation: {message}"
            )
            try:
                enqueue_smart_deploy_task(str(new_deploy.id), str(provider.id), skip_review=True)
            except Exception as exc:  # pragma: no cover - broker/runtime failure
                logger.exception(
                    "Failed to enqueue auto-remediation deployment %s",
                    new_deploy.id,
                )
                new_deploy.status = Deployment.Status.FAILED
                new_deploy.finished_at = timezone.now()
                new_deploy.build_logs = (
                    (new_deploy.build_logs or "")
                    + f"\n[ERROR] Failed to queue auto-remediation task: {exc}\n"
                )
                new_deploy.save(update_fields=["status", "finished_at", "build_logs", "updated_at"])
                return False
            return True
        return False

    def _handle_rollback(self, service: Service) -> bool:
        """Handle automated rollback logic via the centralized engine."""
        from apps.deployments.services.auto_rollback import (
            AutoRollbackEngine,
            Trigger,
        )

        latest_deploy = service.deployments.order_by('-created_at').first()
        try:
            result = AutoRollbackEngine.trigger(
                service=service,
                trigger=Trigger.AI_CRASH_LOOP,
                reason_detail=(
                    f"CRASH_LOOP detected. Latest deployment: {latest_deploy.commit_hash[:7] if latest_deploy and latest_deploy.commit_hash else 'unknown'}"
                ),
                failed_deployment=latest_deploy,
            )
        except Exception as exc:
            logger.error("AutoRollbackEngine.trigger failed for %s: %s", service.name, exc)
            return False
        if result is None:
            logger.warning("AutoRollbackEngine.trigger returned None for %s", service.name)
            return False
        if result.fired:
            logger.info("Rollback triggered for %s (rollback_id=%s)", service.name, result.rollback_id)
            return True

        logger.info("Auto-rollback not fired for %s: %s", service.name, result.reason)
        return False
