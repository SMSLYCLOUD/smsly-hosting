"""Unit tests for live resource-limit application (Docker SDK mocked)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase as DjangoTestCase

from apps.deployments.services.resource_limits import apply_service_resource_limits

User = get_user_model()


def _container(name="api-1", runtime="runc"):
    c = MagicMock()
    c.name = name
    c.status = "running"
    c.attrs = {"HostConfig": {"Runtime": runtime}}
    return c


def _service(cpu="2.0", mem=2048, server_id=None):
    from decimal import Decimal
    svc = MagicMock()
    svc.id = "svc-1"
    svc.name = "api"
    svc.cpu_cores = Decimal(str(cpu))
    svc.memory_mb = mem
    svc.server_id = server_id
    return svc


class TestApplyServiceResourceLimits(TestCase):
    @patch("docker.from_env")
    def test_updates_running_containers(self, mock_from_env):
        c1 = _container("api-1")
        mock_from_env.return_value.containers.list.return_value = [c1]
        res = apply_service_resource_limits(_service("2.0", 2048))
        self.assertEqual(res, {"updated": ["api-1"], "skipped_containers": [], "skipped": None, "errors": []})
        c1.update.assert_called_once_with(
            cpu_period=100000, cpu_quota=200000, cpu_shares=2048,
            mem_limit="2048m", memswap_limit="4096m",
        )

    @patch("docker.from_env")
    def test_runsc_containers_skipped_gracefully(self, mock_from_env):
        c1 = _container("api-green-1", runtime="runsc")
        mock_from_env.return_value.containers.list.return_value = [c1]
        res = apply_service_resource_limits(_service("2.0", 2048))
        self.assertEqual(res["updated"], [])
        self.assertEqual(len(res["skipped_containers"]), 1)
        self.assertIn("runsc", res["skipped_containers"][0]["reason"])
        c1.update.assert_not_called()

    @patch("docker.from_env")
    def test_no_containers_nothing_to_do(self, mock_from_env):
        mock_from_env.return_value.containers.list.return_value = []
        mock_from_env.return_value.containers.get.side_effect = Exception("No such container")
        res = apply_service_resource_limits(_service())
        self.assertEqual(res["updated"], [])
        self.assertEqual(res["errors"], [])

    def test_remote_service_skipped(self):
        res = apply_service_resource_limits(_service(server_id="node-1"))
        self.assertIn("remote", res["skipped"])
        self.assertEqual(res["updated"], [])

    def test_zero_limits_skipped(self):
        res = apply_service_resource_limits(_service(cpu="0", mem=0))
        self.assertIn("no limits", res["skipped"])

    @patch("docker.from_env")
    def test_stopped_containers_ignored(self, mock_from_env):
        c1 = _container("api-1")
        c1.status = "exited"
        mock_from_env.return_value.containers.list.return_value = [c1]
        res = apply_service_resource_limits(_service())
        self.assertEqual(res["updated"], [])
        c1.update.assert_not_called()


class TestApplyLimitsTask(TestCase):
    @patch("apps.deployments.services.resource_limits.apply_service_resource_limits")
    @patch("apps.deployments.models.Service.objects")
    def test_task_applies_for_existing_service(self, mock_objects, mock_apply):
        from apps.deployments.tasks.resource_limits import apply_service_resource_limits_task
        mock_apply.return_value = {"updated": ["api-1"], "skipped": None, "errors": []}
        res = apply_service_resource_limits_task.run("svc-1")
        self.assertEqual(res["updated"], ["api-1"])
        mock_objects.get.assert_called_once_with(id="svc-1")


class TestResourceLimitSignal(DjangoTestCase):
    def setUp(self):
        from apps.deployments.models import Service
        self.Service = Service
        self.svc = Service.objects.create(name="limit-sig-svc")

    @patch("apps.deployments.tasks.resource_limits.apply_service_resource_limits_task")
    def test_save_with_cpu_change_dispatches(self, mock_task):
        self.svc.cpu_cores = "3.0"
        self.svc.save(update_fields=["cpu_cores", "updated_at"])
        mock_task.delay.assert_called_once_with(str(self.svc.id))

    @patch("apps.deployments.tasks.resource_limits.apply_service_resource_limits_task")
    def test_save_without_resource_change_skips(self, mock_task):
        self.svc.branch = "develop"
        self.svc.save(update_fields=["branch", "updated_at"])
        mock_task.delay.assert_not_called()
