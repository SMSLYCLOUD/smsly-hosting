"""Unit tests for BlueprintManager (ORM/celery fully mocked, no DB)."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.deployments.services.blueprint_manager import BlueprintManager


def _manager():
    provider = MagicMock()
    user = MagicMock()
    user.username = "tester"
    return BlueprintManager(provider, user)


def _blueprint():
    return {
        "name": "Test Blueprint",
        "services": [
            {"name": "web", "image": "nginx:latest", "port": 80,
             "env": {"DATABASE_URL": "${DATABASE_URL}", "DEBUG": "false"}},
        ],
        "addons": [{"name": "shared-postgres", "type": "POSTGRES"}],
    }


class TestBlueprintValidation(TestCase):
    def test_rejects_missing_name(self):
        mgr = _manager()
        with self.assertRaises(ValueError):
            mgr._validate_blueprint({"services": []})

    def test_rejects_empty_services(self):
        mgr = _manager()
        with self.assertRaises(ValueError):
            mgr._validate_blueprint({"name": "x", "services": []})

    def test_rejects_service_without_image(self):
        mgr = _manager()
        with self.assertRaises(ValueError):
            mgr._validate_blueprint(
                {"name": "x", "services": [{"name": "web"}]})

    def test_rejects_path_traversal(self):
        mgr = _manager()
        with self.assertRaises(ValueError):
            mgr.load_blueprint("../../etc/passwd")


class TestWaitForAddons(TestCase):
    def test_failed_addon_aborts(self):
        from apps.deployments.models.addons import Addon as RealAddon
        mgr = _manager()
        addon = MagicMock()
        addon.id = "a1"
        addon.name = "pg"
        addon.status = RealAddon.Status.FAILED
        with patch(
            "apps.deployments.services.blueprint_manager.Addon"
        ) as mock_addon:
            mock_addon.Status = RealAddon.Status
            mock_addon.objects.filter.return_value.first.return_value = addon
            with self.assertRaises(ValueError):
                mgr._wait_for_addons([addon])

    def test_active_addons_build_context(self):
        from apps.deployments.models.addons import Addon as RealAddon
        mgr = _manager()
        addon = MagicMock()
        addon.id = "a1"
        addon.name = "pg"
        addon.addon_type = "POSTGRES"
        addon.status = RealAddon.Status.ACTIVE
        addon.connection_url = "postgres://u:p@h:5432/db"
        with patch(
            "apps.deployments.services.blueprint_manager.Addon"
        ) as mock_addon:
            mock_addon.Status = RealAddon.Status
            mock_addon.objects.filter.return_value.first.return_value = addon
            ctx = mgr._wait_for_addons([addon])
        self.assertEqual(ctx["DATABASE_URL"], "postgres://u:p@h:5432/db")

    def test_timeout_raises(self):
        from apps.deployments.models.addons import Addon as RealAddon
        mgr = _manager()
        addon = MagicMock()
        addon.id = "a1"
        addon.name = "pg"
        waiting = MagicMock()
        waiting.status = RealAddon.Status.PROVISIONING
        with patch(
            "apps.deployments.services.blueprint_manager.Addon"
        ) as mock_addon, patch(
            "apps.deployments.services.blueprint_manager.time"
        ) as mock_time, patch(
            "apps.deployments.services.blueprint_manager."
            "ADDON_PROVISION_TIMEOUT_SECONDS", 0,
        ):
            mock_addon.Status = RealAddon.Status
            mock_addon.objects.filter.return_value.first.return_value = waiting
            mock_time.monotonic.side_effect = [0, 1]
            with self.assertRaises(ValueError):
                mgr._wait_for_addons([addon])


class TestDeployFlow(TestCase):
    def test_deploy_wires_project_and_fast_deploy(self):
        from apps.deployments.models.addons import Addon as RealAddon
        mgr = _manager()
        project = MagicMock()
        service = MagicMock()
        service.name = "web-tester"
        addon = MagicMock()
        addon.id = "a1"
        addon.name = "shared-postgres-tester"
        addon.addon_type = "POSTGRES"
        addon.status = RealAddon.Status.ACTIVE
        addon.connection_url = "postgres://u:p@h:5432/db"
        deployment = MagicMock()
        with patch(
            "apps.deployments.services.blueprint_manager.Project"
        ) as mock_project, patch(
            "apps.deployments.services.blueprint_manager.Service"
        ) as mock_service, patch(
            "apps.deployments.services.blueprint_manager.Addon"
        ) as mock_addon, patch(
            "apps.deployments.services.blueprint_manager.EnvironmentVariable"
        ), patch(
            "apps.deployments.services.blueprint_manager.Deployment"
        ) as mock_deployment, patch(
            "apps.deployments.services.blueprint_manager.provision_addon_task"
        ), patch(
            "apps.deployments.services.blueprint_manager.enqueue_smart_deploy_task"
        ) as mock_enqueue:
            mock_project.objects.get_or_create.return_value = (project, True)
            mock_service.objects.filter.return_value.exists.return_value = False
            mock_service.objects.create.return_value = service
            mock_addon.objects.filter.return_value.exists.return_value = False
            mock_addon.objects.create.return_value = addon
            mock_addon.objects.filter.return_value.first.return_value = addon
            mock_addon.Type.choices = [("POSTGRES", "PostgreSQL"), ("REDIS", "Redis")]
            mock_addon.Status.ACTIVE = RealAddon.Status.ACTIVE
            mock_addon.Status.FAILED = RealAddon.Status.FAILED
            mock_addon.Status.PROVISIONING = RealAddon.Status.PROVISIONING
            mock_deployment.objects.create.return_value = deployment
            mgr.load_blueprint = MagicMock(return_value=_blueprint())
            self.assertTrue(mgr.deploy("test-bp"))
            # Service anchored to the project (network isolation fix).
            _, svc_kwargs = mock_service.objects.create.call_args
            self.assertEqual(svc_kwargs.get("project"), project)
            # Addon attached to a real service (FK fix).
            _, addon_kwargs = mock_addon.objects.create.call_args
            self.assertEqual(addon_kwargs.get("service"), service)
            # Deployment carries a commit hash + fast path, enqueued
            # with explicit review skip.
            _, dep_kwargs = mock_deployment.objects.create.call_args
            self.assertTrue(dep_kwargs.get("commit_hash"))
            self.assertTrue(dep_kwargs.get("is_fast_deploy"))
            _, enq_kwargs = mock_enqueue.call_args
            self.assertTrue(enq_kwargs.get("skip_review"))
