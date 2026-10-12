"""Node registry self-heal (mocked SSH, no docker)."""
from unittest import mock

from django.test import SimpleTestCase

from apps.deployments.services.provisioner.helpers import registry as reg


def _ssh_ca(status, login_code=0, login_text=None):
    ssh = mock.Mock()
    denied_text = login_text if login_text is not None else "denied"
    responses = {
        "test -s": ("CA-OK\n" if status == "ok" else "\n", "", 0),
        "docker login": ("Login Succeeded\n" if login_code == 0 else denied_text + "\n",
                         "" if login_code == 0 else denied_text, login_code),
    }

    def _exec(cmd, timeout=None):
        for key, val in responses.items():
            if key in cmd:
                return val
        if "base64 -d" in cmd:
            return "", "", 0
        return "", "", 0

    ssh.exec_command.side_effect = _exec
    return ssh


class EnsureNodeRegistryTests(SimpleTestCase):
    def _server(self):
        srv = mock.Mock()
        srv.name = "node-1"
        srv.host = "10.9.9.9"
        return srv

    def test_healthy_node_only_refreshes_login(self):
        ssh = _ssh_ca("ok")
        with mock.patch("apps.deployments.services.registry_routing.master_registry_node_url",
                               return_value="10.100.0.1:5000"), \
             mock.patch("apps.deployments.services.ssh_client.SSHClient",
                        return_value=ssh), \
             mock.patch.object(reg, "_registry_credential_list",
                               return_value=[("10.100.0.1:5000", "u", "p")]):
            result = reg.ensure_node_registry(self._server())
        self.assertTrue(result["ok"])
        self.assertIn("registry-login-refresh", result["repaired"])
        cmds = [c[0][0] for c in ssh.exec_command.call_args_list]
        self.assertFalse(any("tee /etc/docker" in c for c in cmds))

    def test_missing_ca_reinstalls_then_logs_in(self):
        ssh = _ssh_ca("missing")
        with mock.patch("apps.deployments.services.registry_routing.master_registry_node_url",
                               return_value="10.100.0.1:5000"), \
             mock.patch.object(reg, "_master_registry_ca_pem",
                               return_value="-----BEGIN CERTIFICATE-----\nX\n"), \
             mock.patch("apps.deployments.services.ssh_client.SSHClient",
                        return_value=ssh), \
             mock.patch.object(reg, "_registry_credential_list",
                               return_value=[("10.100.0.1:5000", "u", "p")]):
            result = reg.ensure_node_registry(self._server())
        self.assertTrue(result["ok"])
        self.assertIn("registry-ca", result["repaired"])
        cmds = [c[0][0] for c in ssh.exec_command.call_args_list]
        self.assertTrue(any("tee /etc/docker/certs.d" in c for c in cmds))

    def test_unresolvable_registry_url_fails_cleanly(self):
        with mock.patch("apps.deployments.services.registry_routing.master_registry_node_url",
                               return_value=""):
            result = reg.ensure_node_registry(self._server())
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])

    def test_failed_login_reports_error(self):
        ssh = _ssh_ca("ok", login_code=1)
        with mock.patch("apps.deployments.services.registry_routing.master_registry_node_url",
                               return_value="10.100.0.1:5000"), \
             mock.patch("apps.deployments.services.ssh_client.SSHClient",
                        return_value=ssh), \
             mock.patch.object(reg, "_registry_credential_list",
                               return_value=[("10.100.0.1:5000", "u", "p")]), \
             mock.patch.object(reg, "_resync_node_htpasswd",
                               return_value=False):
            result = reg.ensure_node_registry(self._server())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "registry login failed")

    def test_auth_failure_resyncs_htpasswd_and_retries(self):
        ssh = _ssh_ca("ok", login_code=1)
        with mock.patch("apps.deployments.services.registry_routing.master_registry_node_url",
                               return_value="10.100.0.1:5000"), \
             mock.patch("apps.deployments.services.ssh_client.SSHClient",
                        return_value=ssh), \
             mock.patch.object(reg, "_registry_credential_list",
                               return_value=[("10.100.0.1:5000", "u", "p")]), \
             mock.patch.object(reg, "_resync_node_htpasswd",
                               return_value=True) as resync:
            result = reg.ensure_node_registry(self._server())
        resync.assert_called_once()
        self.assertFalse(result["ok"])
        self.assertIn("registry-htpasswd-resync", result["repaired"])
        self.assertEqual(result["error"], "registry login failed")

    def test_transient_failure_skips_htpasswd_resync(self):
        ssh = _ssh_ca("ok", login_code=1,
                      login_text="net/http: TLS handshake timeout")
        with mock.patch("apps.deployments.services.registry_routing.master_registry_node_url",
                               return_value="10.100.0.1:5000"), \
             mock.patch("apps.deployments.services.ssh_client.SSHClient",
                        return_value=ssh), \
             mock.patch.object(reg, "_registry_credential_list",
                               return_value=[("10.100.0.1:5000", "u", "p")]), \
             mock.patch.object(reg, "_resync_node_htpasswd") as resync:
            result = reg.ensure_node_registry(self._server())
        resync.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "registry login failed")
