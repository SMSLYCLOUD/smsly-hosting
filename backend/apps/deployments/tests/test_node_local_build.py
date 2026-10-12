"""Node-local builds: resolver, trigger payload, builder token hook."""
from unittest import mock

from django.test import SimpleTestCase

from apps.deployments.services.builders import _deployment_github_token
from apps.deployments.services.remote_orchestrator.deployment import (
    DeploymentMixin,
)
from apps.deployments.tasks.tasks_utils import resolve_node_local_build


class ResolveNodeLocalBuildTests(SimpleTestCase):
    def test_override_true_forces_on(self):
        svc = mock.Mock()
        svc.node_local_build = True
        self.assertTrue(resolve_node_local_build(svc))

    def test_override_false_forces_off(self):
        svc = mock.Mock()
        svc.node_local_build = False
        with mock.patch(
                "apps.deployments.models.PlatformConfig") as pc:
            pc.load.return_value.node_local_build_default = True
            self.assertFalse(resolve_node_local_build(svc))

    def test_inherit_platform_default(self):
        svc = mock.Mock()
        svc.node_local_build = None
        with mock.patch(
                "apps.deployments.models.PlatformConfig") as pc:
            pc.load.return_value.node_local_build_default = True
            self.assertTrue(resolve_node_local_build(svc))
            pc.load.return_value.node_local_build_default = False
            self.assertFalse(resolve_node_local_build(svc))

    def test_fail_closed_on_error(self):
        self.assertFalse(resolve_node_local_build(None))


class DeploymentGithubTokenTests(SimpleTestCase):
    def test_metadata_token_wins(self):
        dep = mock.Mock()
        dep.metadata = {"github_token": "tok-123"}
        self.assertEqual(_deployment_github_token(dep), "tok-123")

    def test_missing_or_blank_falls_back(self):
        self.assertIsNone(_deployment_github_token(mock.Mock(metadata={})))
        self.assertIsNone(_deployment_github_token(mock.Mock(metadata=None)))
        blank = mock.Mock()
        blank.metadata = {"github_token": "   "}
        self.assertIsNone(_deployment_github_token(blank))
        self.assertIsNone(_deployment_github_token(None))


def _deployment_stub():
    deployment = mock.MagicMock()
    deployment.commit_hash = "abc1234"
    deployment.service.project = None
    deployment.service.addons.exclude.return_value = []
    deployment.service.volumes.all.return_value = []
    return deployment


def _mixin_self(resp, captured):
    helper = mock.Mock()
    helper._parse_json_response.return_value = {"deployment_id": "remote-1"}
    helper._set_last_error.return_value = None

    def fake_request(method, path, **kwargs):
        captured.update(kwargs.get("payload") or {})
        return resp

    helper._request.side_effect = fake_request
    return helper


def _platform_config_mock():
    pc = mock.Mock()
    pc.server_ip = "10.100.0.1"
    return pc


class TriggerNodeBuildPayloadTests(SimpleTestCase):
    def _ok_response(self):
        resp = mock.Mock()
        resp.status_code = 200
        return resp

    def test_node_build_ships_flag_and_token(self):
        captured = {}
        helper = _mixin_self(self._ok_response(), captured)
        with mock.patch(
                "apps.deployments.services.builders.get_github_access_token_for_service",
                return_value="gh-token"), mock.patch(
                "apps.deployments.services.remote_orchestrator.deployment.PlatformConfig") as pc:
            pc.load.return_value = _platform_config_mock()
            rid = DeploymentMixin.trigger_deploy(
                helper, _deployment_stub(), "remote-svc", node_build=True)
        self.assertEqual(rid, "remote-1")
        self.assertTrue(captured.get("node_build"))
        self.assertEqual(captured.get("github_token"), "gh-token")

    def test_master_build_sends_no_token(self):
        captured = {}
        helper = _mixin_self(self._ok_response(), captured)
        with mock.patch(
                "apps.deployments.services.remote_orchestrator.deployment.PlatformConfig") as pc:
            pc.load.return_value = _platform_config_mock()
            DeploymentMixin.trigger_deploy(
                helper, _deployment_stub(), "remote-svc")
        self.assertFalse(captured.get("node_build", False))
        self.assertNotIn("github_token", captured)
