"""Trigger-deploy mesh env tests (task 9).

Master computes addon mesh overrides (rewrite_env_for_mesh) and ships
them in the remote deploy payload so node pipeline deploys — which
read node-local (empty) Addon rows — get working DB URLs.
Fail-open: mesh errors never block the deploy trigger.
"""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from apps.deployments.services.remote_orchestrator.deployment import (
    DeploymentMixin,
)


class TriggerMeshEnvTests(TestCase):
    def _trigger(self, mesh_value):
        orch = DeploymentMixin()
        orch._request = Mock(return_value=Mock(status_code=200))
        orch._parse_json_response = Mock(return_value={"id": "remote-1"})
        orch._set_last_error = Mock()
        deployment = SimpleNamespace(
            service=SimpleNamespace(name="svc", project=None),
            commit_hash="abc123",
        )
        with patch(
            "apps.deployments.models.PlatformConfig.load",
            return_value=SimpleNamespace(server_ip="controller"),
        ), patch(
            "apps.deployments.services.infisical.resolve_service_token",
            return_value="",
        ), patch(
            "apps.deployments.services.addon_mesh.rewrite_env_for_mesh",
            **(
                {"side_effect": mesh_value}
                if isinstance(mesh_value, Exception)
                else {"return_value": mesh_value}
            ),
        ), patch(
            "apps.deployments.services.secrets_sops.ensure_age_keypair",
            return_value=(
                "age1testpublic0000000000000000000000000000000000",
                "AGE-SECRET-KEY-1testprivate000000000000000000000000000000",
            ),
        ):
            rid = orch.trigger_deploy(deployment, "svc-remote-id")
        self.assertEqual(rid, "remote-1")
        return orch._request.call_args

    def test_mesh_overrides_shipped(self):
        call = self._trigger({"DATABASE_URL": "postgres://u:p@10.100.0.1:24111/db"})
        _args, kwargs = call
        payload = kwargs.get("payload") or call[0][2]
        self.assertEqual(
            payload["mesh_env"],
            {"DATABASE_URL": "postgres://u:p@10.100.0.1:24111/db"},
        )

    def test_empty_mesh_omitted(self):
        call = self._trigger({})
        _args, kwargs = call
        payload = kwargs.get("payload") or call[0][2]
        self.assertNotIn("mesh_env", payload)

    def test_mesh_failure_is_fail_open(self):
        call = self._trigger(RuntimeError("docker down"))
        _args, kwargs = call
        payload = kwargs.get("payload") or call[0][2]
        self.assertNotIn("mesh_env", payload)

    def test_sops_pair_shipped_fail_open(self):
        call = self._trigger({})
        _args, kwargs = call
        payload = kwargs.get("payload") or call[0][2]
        self.assertEqual(
            payload["sops_age_public"],
            "age1testpublic0000000000000000000000000000000000",
        )
        self.assertTrue(payload["sops_age_private"].startswith("AGE-SECRET-KEY-"))
