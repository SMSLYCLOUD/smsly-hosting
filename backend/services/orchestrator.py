# pylint: disable=too-few-public-methods,wrong-import-order
"""Orchestrator module."""
# pylint: disable=no-member
"""Orchestration service."""
from django.utils import timezone
from .builders import BuildManager
from .clusters import ClusterManager
from apps.deployments.models import Deployment
from apps.deployments.tasks_alerts import alert_user_task
from apps.deployments.tasks_ai import analyze_failure_task


class Orchestrator:
    """
    Main entry point for deployment operations.
    """

    def __init__(self, deployment_id):
        self.deployment = Deployment.objects.get(id=deployment_id)
        self.build_manager = BuildManager(self.deployment)
        self.cluster_manager = ClusterManager(self.deployment)

    def run_deployment(self):
        """Run deployment process."""
        self.deployment.status = Deployment.Status.BUILDING
        self.deployment.started_at = timezone.now()
        self.deployment.save()

        try:
            # 1. Build
            image_tag = self.build_manager.build_image()

            # 2. Deploy
            self.deployment.status = Deployment.Status.DEPLOYING
            self.deployment.save()

            container_id = self.cluster_manager.deploy_service(image_tag)

            # 3. Success
            self.deployment.status = Deployment.Status.ACTIVE
            self.deployment.container_id = container_id
            self.deployment.finished_at = timezone.now()
            self.deployment.runtime_logs_url = f"https://logs.smsly.cloud/{container_id}"
            self.deployment.save()

        except Exception as e:
            self.deployment.status = Deployment.Status.FAILED
            self.deployment.finished_at = timezone.now()
            self.deployment.build_logs += f"\n\n[ERROR] {str(e)}"
            self.deployment.save()

            # Vertical Integration: SMS Alert
            alert_user_task.delay(self.deployment.id, str(e))

            # AI Doctor: Analyze Logs
            analyze_failure_task.delay(self.deployment.id)

            raise e
