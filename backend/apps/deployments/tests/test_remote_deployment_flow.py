from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, ManagedServer, Service
from apps.deployments.tasks import (
    _handle_remote_deployment,
    _resume_remote_deployment,
    resume_deploy_task,
)


class RemoteDeploymentFlowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="remote-flow", password="pass")
        self.server = ManagedServer.objects.create(
            owner=self.user,
            name="worker",
            host="203.0.113.20",
            api_url="https://worker.example.com",
            api_token="smsly_token",
            is_primary=False,
        )
        self.service = Service.objects.create(
            owner=self.user,
            name="remote-git-api",
            repository_url="https://github.com/example/api.git",
            server=self.server,
        )
        self.deployment = Deployment.objects.create(
            service=self.service,
            commit_hash="latest",
            commit_message="Manual Trigger: HEAD",
        )
        self.provider = CloudProvider.objects.create(
            name="local",
            provider_type=CloudProvider.ProviderType.LOCAL,
        )

    @patch("apps.deployments.tasks.broadcast_status")
    @patch("apps.deployments.tasks.time.sleep", return_value=None)
    @patch("apps.deployments.services.server_guard.ServerGuard.check_user_workload_allowed", return_value={"ok": True})
    @patch("apps.deployments.services.remote_orchestrator.RemoteOrchestrator")
    def test_remote_review_is_mirrored_to_controller(self, orchestrator_cls, _guard, _sleep, _broadcast):
        orchestrator = orchestrator_cls.return_value
        orchestrator.sync_service.return_value = "remote-service-id"
        orchestrator.trigger_deploy.return_value = "remote-deployment-id"
        orchestrator.poll_deployment.return_value = {
            "status": Deployment.Status.REVIEW,
            "review_summary": {"resources": {"memory_mb": 512}},
            "build_logs": "paused for review",
            "commit_hash": "abc123",
            "commit_message": "remote commit",
        }

        _handle_remote_deployment(self.deployment, self.server)

        self.deployment.refresh_from_db()
        self.assertEqual(self.deployment.status, Deployment.Status.REVIEW)
        self.assertEqual(self.deployment.remote_deployment_id, "remote-deployment-id")
        self.assertEqual(self.deployment.review_summary["resources"]["memory_mb"], 512)
        self.assertEqual(self.deployment.commit_hash, "abc123")

    @patch("apps.deployments.tasks.broadcast_status")
    @patch("apps.deployments.tasks.time.sleep", return_value=None)
    @patch("apps.deployments.services.server_guard.ServerGuard.check_user_workload_allowed", return_value={"ok": True})
    @patch("apps.deployments.services.remote_orchestrator.RemoteOrchestrator")
    def test_remote_approval_resumes_existing_deployment(self, orchestrator_cls, _guard, _sleep, _broadcast):
        self.deployment.remote_deployment_id = "remote-deployment-id"
        self.deployment.status = Deployment.Status.BUILDING
        self.deployment.save(update_fields=["remote_deployment_id", "status"])

        orchestrator = orchestrator_cls.return_value
        orchestrator.sync_service.return_value = "remote-service-id"
        orchestrator.approve_deployment.return_value = True
        orchestrator.poll_deployment.return_value = {
            "status": Deployment.Status.ACTIVE,
            "build_logs": "remote active",
        }

        _resume_remote_deployment(self.deployment, self.server)

        self.deployment.refresh_from_db()
        self.assertEqual(self.deployment.status, Deployment.Status.ACTIVE)
        orchestrator.trigger_deploy.assert_not_called()
        orchestrator.approve_deployment.assert_called_once_with(
            "remote-deployment-id",
            payload={"cpu_cores": str(self.service.cpu_cores), "memory_mb": self.service.memory_mb},
        )

    @patch("apps.deployments.tasks._handle_remote_deployment")
    @patch("apps.deployments.tasks._resume_remote_deployment")
    def test_resume_task_approves_existing_remote_review(self, resume_mock, handle_mock):
        self.deployment.remote_deployment_id = "remote-deployment-id"
        self.deployment.status = Deployment.Status.BUILDING
        self.deployment.save(update_fields=["remote_deployment_id", "status"])

        resume_deploy_task.run(
            deployment_id=str(self.deployment.id),
            provider_id=str(self.provider.id),
        )

        resume_mock.assert_called_once()
        handle_mock.assert_not_called()
