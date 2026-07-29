from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from apps.cloud.models import CloudProvider
from apps.deployments.models.addons import Addon
from apps.deployments.models.core import Service
from apps.deployments.tasks.infra.tasks_maintenance import _clear_orphaned_runtime_resources

User = get_user_model()


class SystemMaintenanceApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.admin = User.objects.create_superuser("admin", "admin@example.com", "password")
        self.client.force_authenticate(self.admin)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch("apps.deployments.tasks.run_maintenance_task.apply_async")
    def test_maintenance_post_queues_task(self, mock_apply_async):
        mock_apply_async.return_value = SimpleNamespace(
            id="maintenance-task-123",
            ready=lambda: False,
        )

        response = self.client.post(reverse("system-config"), {"action": "clear"}, format="json")

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["task_id"], "maintenance-task-123")
        mock_apply_async.assert_called_once()
        self.assertIsNotNone(cache.get("smsly:maintenance:clear:lock"))
        self.assertEqual(
            mock_apply_async.call_args.kwargs["task_id"],
            cache.get("smsly:maintenance:clear:lock"),
        )

    @override_settings(CELERY_TASK_ALWAYS_EAGER=False)
    @patch("apps.deployments.tasks.run_maintenance_task.apply_async")
    def test_maintenance_post_rejects_duplicate_action(self, mock_apply_async):
        cache.set("smsly:maintenance:refresh:lock", "existing-task-456", timeout=300)

        response = self.client.post(reverse("system-config"), {"action": "refresh"}, format="json")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["task_id"], "existing-task-456")
        mock_apply_async.assert_not_called()

    @patch("apps.core.views.system.AsyncResult")
    def test_maintenance_task_status_returns_result(self, mock_async_result):
        mock_async_result.return_value = SimpleNamespace(
            state="SUCCESS",
            result={"status": "success", "message": "Proxy refresh flag written."},
        )

        response = self.client.get(
            reverse("system-config"),
            {"maintenance_task_id": "maintenance_task_123"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["message"], "Proxy refresh flag written.")


class SystemMaintenanceCleanupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", "owner@example.com", "password")
        self.provider = CloudProvider.objects.create(
            name="local",
            provider_type="LOCAL",
            is_active=True,
        )
        self.service = Service.objects.create(
            name="active-service",
            repository_url="https://github.com/example/app",
            owner=self.user,
            provider=self.provider,
        )
        self.addon = Addon.objects.create(
            service=self.service,
            name="postgres-active-service",
            addon_type="POSTGRES",
            status=Addon.Status.ACTIVE,
        )

    @patch("apps.deployments.tasks.deploy.deletion._clear_directory_contents")
    @patch("apps.deployments.tasks.deploy.deletion.docker.from_env")
    def test_clear_orphaned_resources_protects_active_addons(self, mock_docker, mock_clear_dir):
        active_addon_container = MagicMock()
        active_addon_container.name = f"smsly-addon-postgres-{self.addon.id}"
        active_addon_container.labels = {}
        active_addon_container.status = "exited"

        stale_green_container = MagicMock()
        stale_green_container.name = "active-service-green-deadbeef"
        stale_green_container.labels = {"managed_by": "smsly-hosting"}
        stale_green_container.status = "exited"

        orphan_addon_container = MagicMock()
        orphan_addon_container.name = "smsly-addon-postgres-00000000-0000-0000-0000-000000000000"
        orphan_addon_container.labels = {}
        orphan_addon_container.status = "dead"

        mock_client = MagicMock()
        mock_client.containers.list.return_value = [
            active_addon_container,
            stale_green_container,
            orphan_addon_container,
        ]
        mock_client.images.prune.return_value = {"SpaceReclaimed": 0}
        mock_docker.return_value = mock_client
        mock_clear_dir.return_value = {"path": "/tmp/cache", "removed": 0, "missing": True, "errors": []}

        result = _clear_orphaned_runtime_resources()

        active_addon_container.remove.assert_not_called()
        stale_green_container.remove.assert_called_once_with(force=True)
        orphan_addon_container.remove.assert_called_once_with(force=True)
        self.assertEqual(result["removed_count"], 2)
