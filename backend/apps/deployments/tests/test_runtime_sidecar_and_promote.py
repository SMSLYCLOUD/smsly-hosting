from unittest.mock import MagicMock, patch
import os
import tempfile
import uuid

from django.test import SimpleTestCase, TestCase

from apps.cloud.adapters.local import LocalAdapter
from apps.deployments.services.container_runtime import get_runtime_for_container


class RuntimeSelectionTests(SimpleTestCase):
    @patch("apps.deployments.services.container_runtime.detect_best_runtime", return_value="runsc")
    def test_explicit_runc_preference_returns_none(self, _mock_detect):
        self.assertIsNone(get_runtime_for_container("my-app", runtime_preference="runc"))
        self.assertIsNone(get_runtime_for_container("my-app", runtime_preference="none"))

    @patch("apps.deployments.services.container_runtime._service_has_active_sidecar", return_value=True)
    @patch("apps.deployments.services.container_runtime.detect_best_runtime", return_value="runsc")
    def test_active_sidecar_uses_runc_instead_of_runsc(self, _mock_detect, _mock_sidecar):
        self.assertIsNone(get_runtime_for_container("smsly-frontend-l1zzf"))

    @patch("apps.deployments.services.container_runtime._service_has_mtls_enabled", return_value=True)
    @patch("apps.deployments.services.container_runtime._service_has_active_sidecar", return_value=False)
    @patch("apps.deployments.services.container_runtime.detect_best_runtime", return_value="runsc")
    def test_mtls_enabled_without_sidecar_uses_runc_instead_of_runsc(self, _mock_detect, _mock_sidecar, _mock_mtls):
        self.assertIsNone(get_runtime_for_container("smsly-business-email"))

    @patch("apps.deployments.services.container_runtime._service_has_mtls_enabled", return_value=False)
    @patch("apps.deployments.services.container_runtime._service_has_active_sidecar", return_value=False)
    @patch("apps.deployments.services.container_runtime.detect_best_runtime", return_value="runsc")
    def test_non_sidecar_service_preserves_runsc(self, _mock_detect, _mock_sidecar, _mock_mtls):
        self.assertEqual(get_runtime_for_container("worker-app"), "runsc")


class SandboxedPromoteNetworkTests(SimpleTestCase):
    @patch.object(LocalAdapter, "_wait_container_healthy", return_value=True)
    def test_runsc_promote_skips_disconnect_reconnect_on_promoted(self, _wait_mock):
        adapter = object.__new__(LocalAdapter)
        adapter._resolve_network_name = MagicMock(return_value="smsly-net")
        adapter._apply_egress_restrictions = MagicMock()

        green = MagicMock()
        green.id = "green-runsc-id"
        green.name = "myapp-green"
        green.image.tags = ["registry:5000/proj/myapp:v2"]
        green.labels = {
            "smsly.blue_green.port": "8000",
            "smsly.blue_green.host_rule": "Host(`myapp.example.com`)",
            "smsly.blue_green.is_public": "True",
        }
        green.attrs = {
            "State": {"Status": "running", "Health": {"Status": "healthy"}},
            "Config": {"Env": [], "Cmd": None, "Entrypoint": None, "Healthcheck": None},
            "HostConfig": {"Runtime": "runsc"},
        }

        old_live = MagicMock()
        old_live.status = "running"
        old_live.name = "myapp"
        old_live.labels = {"traefik.http.routers.myapp.rule": "Host(`myapp.example.com`)"}
        old_live.attrs = {
            "NetworkSettings": {"Networks": {"smsly-net": {"IPAddress": "172.18.0.42"}}},
            "Config": {"Labels": old_live.labels},
        }

        promoted = MagicMock()
        promoted.id = "promoted-runsc-id"
        promoted.attrs = {"NetworkSettings": {"Networks": {"smsly-net": {"IPAddress": "172.18.0.43"}}}}

        docker_client = MagicMock()
        docker_client.containers.get.side_effect = lambda key: green if key == "green-runsc-id" else old_live
        docker_client.containers.create.return_value = promoted
        net_mock = MagicMock()
        docker_client.networks.get.return_value = net_mock
        docker_client.api.create_endpoint_config.side_effect = lambda **kw: kw
        adapter.docker_client = docker_client

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict(os.environ, {"TRAEFIK_DYNAMIC_DIR": tmpdir}), patch("time.sleep"):
                res = adapter.promote_container("myapp", "green-runsc-id")

        self.assertEqual(res, "promoted-runsc-id")
        endpoint_calls = [c.kwargs.get("aliases") for c in docker_client.api.create_endpoint_config.call_args_list]
        self.assertTrue(any("myapp" in (aliases or []) for aliases in endpoint_calls))
        # Critical invariant: promoted runsc container is NEVER disconnected/reconnected
        for c in net_mock.disconnect.call_args_list:
            self.assertNotEqual(c.args[0], promoted)
        for c in net_mock.connect.call_args_list:
            self.assertNotEqual(c.args[0], promoted)
        net_mock.disconnect.assert_called_with(old_live)
