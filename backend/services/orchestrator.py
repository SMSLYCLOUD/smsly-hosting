# pylint: disable=too-few-public-methods,wrong-import-order
"""Orchestrator module - production hardened."""
# pylint: disable=no-member
import logging
import signal
import threading

from apps.deployments.models import Deployment
from apps.deployments.tasks_ai import analyze_failure_task
from apps.deployments.tasks_alerts import alert_user_task
from django.conf import settings
from django.utils import timezone

from .builders import BuildManager
from .clusters import ClusterManager

logger = logging.getLogger(__name__)

# Default deployment timeout in seconds (configurable via settings)
DEPLOYMENT_TIMEOUT = getattr(settings, 'DEPLOYMENT_TIMEOUT_SECONDS', 600)


class DeploymentTimeoutError(Exception):
    """Raised when a deployment exceeds its time limit."""


class Orchestrator:
    """
    Main entry point for deployment operations.
    Production hardened with:
      - Deployment timeout enforcement
      - Graceful shutdown on SIGTERM/SIGINT
      - Auto-rollback after N consecutive failures
    """

    def __init__(self, deployment_id):
        self.deployment = Deployment.objects.get(id=deployment_id)
        self.build_manager = BuildManager(self.deployment)
        self.cluster_manager = ClusterManager(self.deployment)
        self._shutdown_requested = False

        # Register graceful shutdown handlers
        self._register_signal_handlers()

    def _register_signal_handlers(self):
        """Register SIGTERM/SIGINT for graceful shutdown."""
        try:
            signal.signal(signal.SIGTERM, self._handle_shutdown)
            signal.signal(signal.SIGINT, self._handle_shutdown)
        except (OSError, ValueError):
            # Cannot set signal handlers outside main thread (e.g., in Celery)
            # Celery handles its own SIGTERM via soft_time_limit
            pass

    def _handle_shutdown(self, signum, frame):
        """Mark deployment as failed on shutdown signal."""
        logger.warning(
            "Shutdown signal %s received during deployment %s",
            signum, self.deployment.id
        )
        self._shutdown_requested = True

        if self.deployment.status in (
            Deployment.Status.BUILDING,
            Deployment.Status.DEPLOYING,
        ):
            self.deployment.status = Deployment.Status.FAILED
            self.deployment.finished_at = timezone.now()
            self.deployment.build_logs += (
                "\n\n[SHUTDOWN] Deployment interrupted by shutdown signal."
            )
            self.deployment.save()

    def run_deployment(self):
        """
        Run deployment process with timeout enforcement.
        Raises DeploymentTimeoutError if DEPLOYMENT_TIMEOUT is exceeded.
        """
        self.deployment.status = Deployment.Status.BUILDING
        self.deployment.started_at = timezone.now()
        self.deployment.save()

        # Create a timer that will fire if the deployment exceeds timeout
        timeout_timer = threading.Timer(
            DEPLOYMENT_TIMEOUT,
            self._handle_timeout,
        )
        timeout_timer.daemon = True
        timeout_timer.start()

        try:
            if self._shutdown_requested:
                raise DeploymentTimeoutError("Shutdown requested before build.")

            # 1. Build
            image_tag = self.build_manager.build_image()

            if self._shutdown_requested:
                raise DeploymentTimeoutError("Shutdown requested after build.")

            # 2. Deploy
            self.deployment.status = Deployment.Status.DEPLOYING
            self.deployment.save()

            container_id = self.cluster_manager.deploy_service(image_tag)

            # 3. Success
            self.deployment.status = Deployment.Status.ACTIVE
            self.deployment.container_id = container_id
            self.deployment.finished_at = timezone.now()
            self.deployment.runtime_logs_url = (
                f"https://logs.smsly.cloud/{container_id}"
            )
            self.deployment.save()

            logger.info(
                "Deployment %s succeeded for service %s",
                self.deployment.id,
                self.deployment.service.name,
            )

        except Exception as e:
            self.deployment.status = Deployment.Status.FAILED
            self.deployment.finished_at = timezone.now()
            self.deployment.build_logs += f"\n\n[ERROR] {e!s}"
            self.deployment.save()

            logger.error(
                "Deployment %s failed: %s", self.deployment.id, str(e)
            )

            # Vertical Integration: SMS Alert
            alert_user_task.delay(self.deployment.id, str(e))

            # AI Doctor: Analyze Logs
            analyze_failure_task.delay(self.deployment.id)

            # Check if auto-rollback should be triggered
            self._check_auto_rollback()

            raise e

        finally:
            timeout_timer.cancel()

    def _handle_timeout(self):
        """Called by the timeout timer if deployment exceeds the limit."""
        logger.error(
            "Deployment %s exceeded timeout of %ss",
            self.deployment.id,
            DEPLOYMENT_TIMEOUT,
        )
        self._shutdown_requested = True
        self.deployment.status = Deployment.Status.FAILED
        self.deployment.finished_at = timezone.now()
        self.deployment.build_logs += (
            f"\n\n[TIMEOUT] Deployment exceeded {DEPLOYMENT_TIMEOUT}s limit."
        )
        self.deployment.save()

    def _check_auto_rollback(self):
        from apps.deployments.services.auto_rollback import (
            AutoRollbackEngine,
            Trigger,
        )

        result = AutoRollbackEngine.trigger(
            service=self.deployment.service,
            trigger=Trigger.CONSECUTIVE_FAILURES,
            reason_detail=(
                f"Deployment {self.deployment.id} failed with "
                f"{self.deployment.error_message or 'unknown error'}"
            ),
            failed_deployment=self.deployment,
        )
        if result.fired:
            logger.warning(
                "Auto-rollback fired for %s: %s (rollback_id=%s)",
                self.deployment.service.name,
                result.reason,
                result.rollback_id,
            )
        else:
            logger.info(
                "Auto-rollback not fired for %s: %s",
                self.deployment.service.name,
                result.reason,
            )


