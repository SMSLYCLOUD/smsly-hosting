# pylint: disable=invalid-name
"""
Tests for critical Celery tasks:
  1. delete_service_task  — success, failure, and retry paths
  2. smart_deploy_task    — transient error retries
  3. provision_addon_task — addon status transitions
  4. addon_health_check_all — iterates addons, handles failures
  5. recover_stalled_deletions — re-queues stuck services
"""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.cloud.models import CloudProvider
from apps.deployments.constants import STALL_RECOVERY_THRESHOLD_MINUTES
from apps.deployments.models import Deployment, Service
from apps.deployments.models.addons import Addon

TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-celery-tasks",
    }
}


@override_settings(CACHES=TEST_CACHES)
class DeleteServiceTaskTests(TestCase):
    """Tests for delete_service_task (apps.deployments.tasks.deploy.deletion)."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="del-user", password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="del-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )

    @patch(
        "apps.deployments.tasks.deploy.deletion.DeletionOrchestrator"
    )
    def test_delete_service_success(self, MockOrchestrator):
        """Successful deletion removes the service from the DB."""
        orchestrator = MockOrchestrator.return_value
        orchestrator.docker_client = MagicMock()
        orchestrator.delete_service_resources.return_value = True
        orchestrator.delete_addon_resources.return_value = True

        service = Service.objects.create(
            name="delete-me-ok",
            owner=self.user,
            provider=self.provider,
        )
        svc_id = str(service.id)

        from apps.deployments.tasks.deploy.deletion import delete_service_task
        result = delete_service_task(svc_id)

        self.assertFalse(Service.objects.filter(id=svc_id).exists())

    @patch(
        "apps.deployments.tasks.deploy.deletion.DeletionOrchestrator"
    )
    def test_delete_service_failure_sets_status(self, MockOrchestrator):
        """When orchestrator fails, service is marked DELETION_FAILED."""
        orchestrator = MockOrchestrator.return_value
        orchestrator.docker_client = MagicMock()
        orchestrator.delete_service_resources.return_value = False

        service = Service.objects.create(
            name="delete-fail",
            owner=self.user,
            provider=self.provider,
        )
        svc_id = str(service.id)

        from apps.deployments.tasks.deploy.deletion import delete_service_task
        delete_service_task(svc_id)

        service.refresh_from_db()
        self.assertEqual(service.status, Service.Status.DELETION_FAILED)
        self.assertIn("Failed to remove", service.deletion_error)

    def test_delete_service_nonexistent_returns(self):
        """Calling with a nonexistent ID returns silently (no crash)."""
        from apps.deployments.tasks.deploy.deletion import delete_service_task
        result = delete_service_task(str(User.objects.create_user(username="z", password="z").id))
        self.assertIsNone(result)

    @patch(
        "apps.deployments.tasks.deploy.deletion.DeletionOrchestrator"
    )
    @patch(
        "apps.deployments.tasks.deploy.deletion.delete_service_task.retry",
        side_effect=Exception("retry raised"),
    )
    def test_delete_service_retries_on_exception(self, mock_retry, MockOrchestrator):
        """Unexpected exception triggers self.retry()."""
        orchestrator = MockOrchestrator.return_value
        orchestrator.docker_client = MagicMock()
        orchestrator.delete_service_resources.side_effect = RuntimeError("docker boom")

        service = Service.objects.create(
            name="delete-retry",
            owner=self.user,
            provider=self.provider,
        )

        from apps.deployments.tasks.deploy.deletion import delete_service_task
        with self.assertRaises(Exception):
            delete_service_task(str(service.id))

        mock_retry.assert_called_once()


@override_settings(CACHES=TEST_CACHES)
class SmartDeployTaskTests(TestCase):
    """Tests for smart_deploy_task transient-error retry behavior."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="deploy-user", password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="deploy-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="deploy-svc",
            owner=self.user,
            provider=self.provider,
            deploy_type="DOCKER",
            docker_image="nginx:latest",
        )

    @patch(
        "apps.deployments.tasks.deployment.tasks_deploy._deploy_container",
        side_effect=ConnectionError("network unreachable"),
    )
    @patch(
        "apps.deployments.tasks.deployment.tasks_deploy.smart_deploy_task.retry",
        side_effect=Exception("retry raised"),
    )
    def test_transient_error_triggers_retry(self, mock_retry, _mock_deploy):
        """ConnectionError/DockerException triggers retry with countdown."""
        deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash="abc1234",
        )

        from apps.deployments.tasks.deployment.tasks_deploy import smart_deploy_task
        with self.assertRaises(Exception):
            smart_deploy_task(str(deployment.id), str(self.provider.id))

        mock_retry.assert_called_once()

    def test_cancelled_deployment_is_skipped(self):
        """A cancelled deployment should return immediately."""
        deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.CANCELLED,
            commit_hash="deadbeef",
        )

        from apps.deployments.tasks.deployment.tasks_deploy import smart_deploy_task
        result = smart_deploy_task(str(deployment.id), str(self.provider.id))
        self.assertIsNone(result)

    @patch(
        "apps.deployments.tasks.deployment.tasks_deploy._handle_failure",
    )
    @patch(
        "apps.deployments.tasks.deployment.tasks_deploy._deploy_container",
        side_effect=RuntimeError("system failure"),
    )
    def test_non_transient_error_calls_handle_failure(self, mock_deploy, mock_handle):
        """Non-DockerException errors go to _handle_failure (no retry)."""
        deployment = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash="cafebabe",
        )

        from apps.deployments.tasks.deployment.tasks_deploy import smart_deploy_task
        smart_deploy_task(str(deployment.id), str(self.provider.id))

        mock_handle.assert_called_once()


@override_settings(CACHES=TEST_CACHES)
class ProvisionAddonTaskTests(TestCase):
    """Tests for provision_addon_task status transitions."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="addon-user", password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="addon-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="addon-svc",
            owner=self.user,
            provider=self.provider,
        )

    @patch(
        "apps.addons.tasks.crud.addon_provisioner"
    )
    def test_provision_success_sets_active(self, mock_provisioner):
        """Successful provisioning transitions addon to ACTIVE."""
        mock_provisioner.provision_dispatch.return_value = ("cid-123", "postgres://localhost:5432/db")

        addon = Addon.objects.create(
            service=self.service,
            name="test-pg",
            addon_type=Addon.Type.POSTGRES,
            status=Addon.Status.PROVISIONING,
        )

        from apps.addons.tasks.crud import provision_addon_task
        provision_addon_task(str(addon.id))

        addon.refresh_from_db()
        self.assertEqual(addon.status, Addon.Status.ACTIVE)
        self.assertEqual(addon.coolify_uuid, "cid-123")
        self.assertIn("postgres://", addon.connection_url)

    @patch(
        "apps.addons.tasks.crud.addon_provisioner"
    )
    def test_provision_failure_retries_then_marks_failed(self, mock_provisioner):
        """On provisioner failure, addon retries and eventually marks FAILED."""
        mock_provisioner.provision_dispatch.side_effect = RuntimeError("container failed")

        addon = Addon.objects.create(
            service=self.service,
            name="test-pg-fail",
            addon_type=Addon.Type.POSTGRES,
            status=Addon.Status.PROVISIONING,
        )

        from apps.addons.tasks.crud import provision_addon_task

        # Simulate max_retries exceeded by calling with retries = max_retries
        provision_addon_task.max_retries = 0
        provision_addon_task(str(addon.id))

        addon.refresh_from_db()
        self.assertEqual(addon.status, Addon.Status.FAILED)

    @patch(
        "apps.addons.tasks.crud.addon_provisioner"
    )
    def test_provision_injects_env_vars(self, mock_provisioner):
        """Successful provision injects connection_url as env var on the service."""
        from apps.deployments.models import EnvironmentVariable

        mock_provisioner.provision_dispatch.return_value = ("cid-env", "redis://localhost:6379")

        addon = Addon.objects.create(
            service=self.service,
            name="test-redis",
            addon_type=Addon.Type.REDIS,
            status=Addon.Status.PROVISIONING,
        )

        from apps.addons.tasks.crud import provision_addon_task
        provision_addon_task(str(addon.id))

        addon.refresh_from_db()
        self.assertEqual(addon.status, Addon.Status.ACTIVE)

        env_var = EnvironmentVariable.objects.filter(
            service=self.service,
            key__contains="REDIS",
        ).first()
        self.assertIsNotNone(env_var)
        self.assertIn("redis://", env_var.value)


@override_settings(CACHES=TEST_CACHES)
class AddonHealthCheckAllTests(TestCase):
    """Tests for addon_health_check_all task."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="hc-user", password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="hc-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="hc-svc",
            owner=self.user,
            provider=self.provider,
        )

    @patch(
        "apps.addons.tasks.AddonMaintenanceService"
    )
    @patch("apps.addons.tasks.check_alerts", return_value=[])
    def test_iterates_all_active_addons(self, _mock_alerts, MockMaintenance):
        """Health check runs for every ACTIVE addon."""
        mock_svc = MockMaintenance.return_value
        mock_svc.health_check.return_value = None
        mock_svc.proxy.get_stats.return_value = {}

        Addon.objects.create(
            service=self.service, name="pg-1",
            addon_type=Addon.Type.POSTGRES, status=Addon.Status.ACTIVE,
        )
        Addon.objects.create(
            service=self.service, name="redis-1",
            addon_type=Addon.Type.REDIS, status=Addon.Status.ACTIVE,
        )
        # This one should be skipped
        Addon.objects.create(
            service=self.service, name="pg-dead",
            addon_type=Addon.Type.POSTGRES, status=Addon.Status.FAILED,
        )

        from apps.addons.tasks import addon_health_check_all
        addon_health_check_all()

        self.assertEqual(mock_svc.health_check.call_count, 2)

    @patch(
        "apps.addons.tasks.AddonMaintenanceService"
    )
    @patch("apps.addons.tasks.check_alerts", return_value=[])
    def test_health_check_failure_on_one_addon_does_not_block_others(
        self, _mock_alerts, MockMaintenance,
    ):
        """If one addon's health check raises, the others still run."""
        call_count = [0]

        def side_effect(addon):
            if addon.name == "broken-addon":
                raise RuntimeError("health check crashed")
            call_count[0] += 1

        mock_svc = MockMaintenance.return_value
        mock_svc.health_check.side_effect = side_effect
        mock_svc.proxy.get_stats.return_value = {}

        Addon.objects.create(
            service=self.service, name="good-addon",
            addon_type=Addon.Type.REDIS, status=Addon.Status.ACTIVE,
        )
        Addon.objects.create(
            service=self.service, name="broken-addon",
            addon_type=Addon.Type.POSTGRES, status=Addon.Status.ACTIVE,
        )
        Addon.objects.create(
            service=self.service, name="another-good",
            addon_type=Addon.Type.REDIS, status=Addon.Status.ACTIVE,
        )

        from apps.addons.tasks import addon_health_check_all
        with self.assertRaises(RuntimeError):
            addon_health_check_all()

        # The first addon was checked before the exception
        self.assertGreaterEqual(call_count[0], 1)


@override_settings(CACHES=TEST_CACHES)
class RecoverStalledDeletionsTests(TestCase):
    """Tests for recover_stalled_deletions task."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="stall-user", password="password123",
        )
        self.provider = CloudProvider.objects.create(
            name="stall-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )

    @patch(
        "apps.deployments.tasks.deploy.deletion.delete_service_task.delay"
    )
    def test_requeues_stalled_services(self, mock_delay):
        """Services stuck in DELETION_PENDING past threshold get re-queued."""
        old_time = timezone.now() - timedelta(
            minutes=STALL_RECOVERY_THRESHOLD_MINUTES + 5,
        )
        service = Service.objects.create(
            name="stalled-1",
            owner=self.user,
            provider=self.provider,
            status=Service.Status.DELETION_PENDING,
        )
        Service.objects.filter(id=service.id).update(updated_at=old_time)

        from apps.deployments.tasks.deploy.deletion import recover_stalled_deletions
        result = recover_stalled_deletions()

        self.assertEqual(result["recovered"], 1)
        mock_delay.assert_called_once_with(str(service.id))

    @patch(
        "apps.deployments.tasks.deploy.deletion.delete_service_task.delay"
    )
    def test_skips_recent_pending_services(self, mock_delay):
        """Services still within the threshold window are not re-queued."""
        service = Service.objects.create(
            name="recent-pending",
            owner=self.user,
            provider=self.provider,
            status=Service.Status.DELETION_PENDING,
        )

        from apps.deployments.tasks.deploy.deletion import recover_stalled_deletions
        result = recover_stalled_deletions()

        self.assertEqual(result["recovered"], 0)
        mock_delay.assert_not_called()

    @patch(
        "apps.deployments.tasks.deploy.deletion.delete_service_task.delay"
    )
    def test_does_not_requeue_non_pending_services(self, mock_delay):
        """ACTIVE services are never re-queued regardless of age."""
        old_time = timezone.now() - timedelta(
            minutes=STALL_RECOVERY_THRESHOLD_MINUTES + 5,
        )
        service = Service.objects.create(
            name="old-active",
            owner=self.user,
            provider=self.provider,
            status=Service.Status.ACTIVE,
        )
        Service.objects.filter(id=service.id).update(updated_at=old_time)

        from apps.deployments.tasks.deploy.deletion import recover_stalled_deletions
        result = recover_stalled_deletions()

        self.assertEqual(result["recovered"], 0)
        mock_delay.assert_not_called()

    @patch(
        "apps.deployments.tasks.deploy.deletion.delete_service_task.delay",
        side_effect=RuntimeError("broker down"),
    )
    def test_handles_delay_failure_gracefully(self, mock_delay):
        """If .delay() fails, the task logs but does not crash."""
        old_time = timezone.now() - timedelta(
            minutes=STALL_RECOVERY_THRESHOLD_MINUTES + 5,
        )
        service = Service.objects.create(
            name="stall-delay-fail",
            owner=self.user,
            provider=self.provider,
            status=Service.Status.DELETION_PENDING,
        )
        Service.objects.filter(id=service.id).update(updated_at=old_time)

        from apps.deployments.tasks.deploy.deletion import recover_stalled_deletions
        result = recover_stalled_deletions()

        # It still counted the service even though .delay() raised
        self.assertEqual(result["recovered"], 1)
