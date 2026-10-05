"""Manual mTLS reload tests (service + project actions)."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.response import Response
from rest_framework.test import APIClient

from apps.deployments.models import Project, Service
from apps.mtls.models import MtlsConfig

User = get_user_model()


def _mtls(service, enabled=True):
    cfg, _ = MtlsConfig.objects.get_or_create(service=service)
    cfg.enabled = enabled
    cfg.trust_domain = "platform.local"
    cfg.save()
    return cfg


class MtlsReloadServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mtls-user", password="x")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.service = Service.objects.create(name="mtls-svc", owner=self.user)

    def test_disabled_rejected(self):
        _mtls(self.service, enabled=False)
        resp = self.client.post(f"/api/v1/services/{self.service.id}/mtls-reload/", {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_missing_config_rejected(self):
        resp = self.client.post(f"/api/v1/services/{self.service.id}/mtls-reload/", {}, format="json")
        self.assertEqual(resp.status_code, 400)

    def test_outsider_denied(self):
        _mtls(self.service, enabled=True)
        other = User.objects.create_user(username="mtls-outsider", password="x")
        self.client.force_authenticate(user=other)
        resp = self.client.post(f"/api/v1/services/{self.service.id}/mtls-reload/", {}, format="json")
        self.assertIn(resp.status_code, (403, 404))

    def test_enabled_triggers_deploy_and_components(self):
        _mtls(self.service, enabled=True)
        with patch(
            "apps.deployments.views.service.deploy.DeployActionsMixin.deploy",
            return_value=Response({"id": "dep-1"}, status=201),
        ), patch(
            "apps.mtls.services.envoy_sidecar.EnvoySidecar.reattach_if_stale",
            return_value={"status": "current", "reattached": False},
        ), patch(
            "apps.deployments.tasks_spiffe.sync_spiffe_entries_task",
        ) as mock_spiffe:
            resp = self.client.post(
                f"/api/v1/services/{self.service.id}/mtls-reload/", {}, format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["deploy_triggered"])
        self.assertEqual(resp.data["deployment_id"], "dep-1")
        self.assertEqual(resp.data["components"]["sidecar"]["status"], "current")
        mock_spiffe.delay.assert_called_once_with()


class MtlsReloadComponentsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mtls-comp-user", password="x")
        self.service = Service.objects.create(name="mtls-comp-svc", owner=self.user)

    def test_remote_service_node_handled(self):
        from apps.mtls.services.reload import reload_service_components
        self.service.active_target_type = "remote"
        self.service.active_host_ip = "10.0.0.5"
        self.service.save()
        with patch(
            "apps.mtls.services.envoy_sidecar.EnvoySidecar.reattach_if_stale",
        ) as mock_sidecar, patch(
            "apps.deployments.tasks_spiffe.sync_spiffe_entries_task",
        ):
            result = reload_service_components(self.service)
        mock_sidecar.assert_not_called()
        self.assertEqual(result["sidecar"]["status"], "node-handled")


class MtlsReloadProjectTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mtls-proj-user", password="x")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.project = Project.objects.create(owner=self.user, name="mtls proj")
        self.svc_on = Service.objects.create(
            name="mtls-proj-on", owner=self.user, project=self.project,
        )
        self.svc_off = Service.objects.create(
            name="mtls-proj-off", owner=self.user, project=self.project,
        )
        _mtls(self.svc_on, enabled=True)
        _mtls(self.svc_off, enabled=False)

    def test_project_reloads_only_enabled(self):
        with patch(
            "apps.deployments.views.service.deploy.DeployActionsMixin.deploy",
            return_value=Response({"id": "dep-9"}, status=201),
        ), patch(
            "apps.mtls.services.envoy_sidecar.EnvoySidecar.reattach_if_stale",
            return_value={"status": "current", "reattached": False},
        ), patch(
            "apps.deployments.tasks_spiffe.sync_spiffe_entries_task",
        ):
            resp = self.client.post(
                f"/api/v1/projects/{self.project.id}/mtls-reload/", {}, format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["skipped"], ["mtls-proj-off"])
        self.assertEqual(len(resp.data["reloaded"]), 1)
        self.assertEqual(resp.data["reloaded"][0]["service"], "mtls-proj-on")
        self.assertEqual(resp.data["errors"], [])
