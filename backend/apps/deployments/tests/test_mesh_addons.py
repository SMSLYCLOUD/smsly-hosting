"""Mesh-backed addon behavior: readiness, reconcile, provision guard."""
from unittest.mock import MagicMock, Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Deployment, Service
from apps.deployments.models.addons import Addon

User = get_user_model()


def _mesh_addon(service, url="postgres://u:p@10.100.0.1:24111/db"):
    return Addon.objects.create(
        service=service,
        name="mesh-pg",
        addon_type="POSTGRES",
        connection_url=url,
        status="ACTIVE",
        provider_metadata={"mesh_backed": True, "mesh_forward_port": 24111},
    )


class MeshReadinessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mesh-ready-user", password="x")
        self.service = Service.objects.create(name="mesh-ready-svc", owner=self.user)
        self.deployment = Deployment.objects.create(
            service=self.service, status=Deployment.Status.BUILDING,
            commit_hash="abc",
        )

    def test_mesh_addon_probed_not_inspected(self):
        from apps.deployments.tasks.deploy.addons import _ensure_addons_ready
        _mesh_addon(self.service)
        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value = MagicMock()
            _ensure_addons_ready(self.service, self.deployment)
        mock_conn.assert_called_once_with(("10.100.0.1", 24111), timeout=8)

    def test_mesh_addon_refusal_fails_closed(self):
        from apps.deployments.tasks.deploy.addons import _ensure_addons_ready
        _mesh_addon(self.service)
        with patch("socket.create_connection", side_effect=OSError("refused")):
            with self.assertRaises(RuntimeError) as ctx:
                _ensure_addons_ready(self.service, self.deployment)
        self.assertIn("mesh endpoint", str(ctx.exception))

    def test_mesh_addon_bad_url_fails_closed(self):
        from apps.deployments.tasks.deploy.addons import _ensure_addons_ready
        _mesh_addon(self.service, url="not-a-url")
        with self.assertRaises(RuntimeError):
            _ensure_addons_ready(self.service, self.deployment)


class MeshReconcileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="mesh-rec-user", password="x")
        self.service = Service.objects.create(name="mesh-rec-svc", owner=self.user)

    def test_mesh_reachable_not_flagged(self):
        from apps.addons.services.addon_reconcile import check_addon_backend
        addon = _mesh_addon(self.service)
        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value = MagicMock()
            self.assertIsNone(check_addon_backend(addon))

    def test_mesh_refused_flagged_gone(self):
        from apps.addons.services.addon_reconcile import check_addon_backend
        addon = _mesh_addon(self.service)
        with patch("socket.create_connection", side_effect=OSError("down")):
            verdict = check_addon_backend(addon)
        self.assertTrue(str(verdict).startswith("gone:"))


class MeshProvisionGuardTests(TestCase):
    def test_local_provision_refused(self):
        from apps.addons.services.addon_provisioner import AddonProvisioner
        user = User.objects.create_user(username="mesh-prov-user", password="x")
        service = Service.objects.create(name="mesh-prov-svc", owner=user)
        addon = _mesh_addon(service)
        with self.assertRaises(RuntimeError) as ctx:
            AddonProvisioner.provision(Mock(), addon)
        self.assertIn("mesh-backed", str(ctx.exception))


class BothHostsReconcileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="both-hosts-user", password="x")
        from apps.deployments.models.core import ManagedServer
        self.server = ManagedServer.objects.create(
            name="both-node", host="10.9.9.9", is_primary=False, owner=self.user,
        )
        self.service = Service.objects.create(name="both-hosts-svc", owner=self.user)
        self.service.active_target_type = "remote"
        self.service.active_host_ip = "10.9.9.9"
        self.service.server = self.server
        self.service.save()
        self.addon = Addon.objects.create(
            service=self.service,
            name="both-pg",
            addon_type="POSTGRES",
            connection_url="postgres://u:p@host:5432/db",
            status="ACTIVE",
        )

    def _node_resp(self, status_code=200, body=None):
        resp = Mock()
        resp.status_code = status_code
        resp.json = Mock(return_value=body if body is not None else {})
        return resp

    def test_master_only_backend_not_flagged(self):
        from apps.addons.services import addon_reconcile as rec
        with patch(
            "apps.deployments.services.remote_orchestrator.RemoteOrchestrator._request",
            return_value=self._node_resp(200, {"status": "not-found"}),
        ), patch("docker.from_env") as mock_docker:
            mock_docker.return_value.containers.get.return_value = Mock()
            self.assertIsNone(rec.check_addon_backend(self.addon))

    def test_node_only_backend_not_flagged(self):
        from apps.addons.services import addon_reconcile as rec
        import docker as _docker_lib
        with patch(
            "apps.deployments.services.remote_orchestrator.RemoteOrchestrator._request",
            return_value=self._node_resp(200, {"status": "running"}),
        ), patch("docker.from_env", side_effect=_docker_lib.errors.DockerException("nope")):
            self.assertIsNone(rec.check_addon_backend(self.addon))

    def test_absent_both_hosts_flagged(self):
        from apps.addons.services import addon_reconcile as rec
        import docker as _docker_lib
        with patch(
            "apps.deployments.services.remote_orchestrator.RemoteOrchestrator._request",
            return_value=self._node_resp(200, {"status": "not-found"}),
        ), patch("docker.from_env", side_effect=_docker_lib.errors.DockerException("nope")):
            verdict = rec.check_addon_backend(self.addon)
        self.assertTrue(str(verdict).startswith("gone:"))

    def test_node_unreachable_not_flagged(self):
        from apps.addons.services import addon_reconcile as rec
        import docker as _docker_lib
        with patch(
            "apps.deployments.services.remote_orchestrator.RemoteOrchestrator._request",
            return_value=None,
        ), patch("docker.from_env", side_effect=_docker_lib.errors.DockerException("nope")):
            verdict = rec.check_addon_backend(self.addon)
        self.assertTrue(str(verdict).startswith("check-failed:"))
