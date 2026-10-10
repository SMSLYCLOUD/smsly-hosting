"""Tests for the Security Intel wiring additions.

Pins the frontend contract for the audit pass:

* GET /api/v1/system/security-status/ carries `coraza` (Caddy-embedded
  WAF truth) and `cf_bouncer` (Cloudflare edge bouncer truth) blocks
  with subprocess mocked (no Docker needed).
* POST /api/v1/system/fail2ban-unban/ mirrors the CrowdSec unban
  contract: admin-only, strict IP + jail validation, fail-closed
  generic errors, best-effort audit.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.core.views.security import Fail2banUnbanView, SecurityStatusView


def _resp(stdout="", returncode=0):
    m = MagicMock()
    m.stdout = stdout
    # stderr MUST be a real string: the view concatenates
    # stdout+stderr in places, and a MagicMock would poison `in`.
    m.stderr = ""
    m.returncode = returncode
    return m


def _run_map():
    """subprocess.run side effect keyed by command shape."""
    def _run(cmd, **kwargs):
        joined = " ".join(cmd)
        if cmd[:2] == ["aa-status", "--enabled"]:
            return _resp("", returncode=1)
        if cmd[:2] == ["docker", "info"]:
            return _resp('["name=seccomp,profile=builtin"]\n')
        if cmd[:2] == ["docker", "ps"] and "--format" in cmd:
            idx = cmd.index("--format")
            # Name-listing probe (coraza caddy discovery) vs status probe.
            if "{{.Names}}" in cmd[idx + 1]:
                return _resp("smsly-hosting-caddy-1\nsmsly-crowdsec\n")
            return _resp("Up 2 hours\n")
        if cmd[:3] == ["docker", "ps", "--filter"] and "caddy" in joined.lower():
            return _resp("smsly-hosting-caddy-1 Up 6 days (healthy)\n")
        if cmd[:2] == ["docker", "ps"]:
            return _resp("Up 2 hours\n")
        if cmd[:2] == ["docker", "exec"] and "list-modules" in joined:
            return _resp("http.handlers.reverse_proxy\nhttp.handlers.waf\n")
        if cmd[:2] == ["docker", "exec"] and "bouncers" in joined:
            return _resp(
                '[{"name": "cloudflare-bouncer", '
                '"last_pull": "2026-10-10T03:00:00Z"}]\n')
        if cmd[:2] == ["docker", "logs"]:
            return _resp("level=info msg=ok\n")
        if "inspect" in cmd:
            return _resp("0\n")
        if "logs" in cmd:
            return _resp("some log without the marker\n")
        if "port" in cmd:
            return _resp("8081/tcp -> 127.0.0.1:18081\n")
        return _resp("", returncode=1)
    return _run


class SecurityIntelBlocksTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="intel-admin", email="intel-admin@test.com",
            password="pass123",
        )
        from apps.deployments.models.core import PlatformConfig
        PlatformConfig.clear_cache()

    def _get(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        req = APIRequestFactory().get("/api/v1/system/security-status/")
        force_authenticate(req, user=self.admin)
        return SecurityStatusView.as_view()(req)

    @patch("apps.core.views.security.subprocess.run")
    def test_coraza_block_wired(self, mock_run):
        mock_run.side_effect = _run_map()
        data = self._get().data
        coraza = data["coraza"]
        self.assertTrue(coraza["module_loaded"])
        self.assertIsInstance(coraza["site_imports"], int)
        self.assertIsInstance(coraza["services_protected"], int)
        self.assertIsInstance(coraza["services_opted_out"], int)
        self.assertIsInstance(coraza["edge_jwt_gated"], int)

    @patch("apps.core.views.security.subprocess.run")
    def test_cf_bouncer_block_wired(self, mock_run):
        mock_run.side_effect = _run_map()
        data = self._get().data
        cf = data["cf_bouncer"]
        self.assertTrue(cf["running"])
        self.assertEqual(cf["last_pull"], "2026-10-10T03:00:00Z")
        self.assertIn("stale", cf)
        self.assertIn("recent_error", cf)

    @patch("apps.core.views.security.subprocess.run")
    def test_cf_bouncer_stale_when_no_pull(self, mock_run):
        def _run(cmd, **kwargs):
            if cmd[:2] == ["docker", "exec"] and "bouncers" in " ".join(cmd):
                return _resp("[]\n")
            return _run_map()(cmd, **kwargs)
        mock_run.side_effect = _run
        data = self._get().data
        # Running with no pull record: stale must read True (never
        # claim a dead stream is healthy).
        self.assertTrue(data["cf_bouncer"]["stale"])


class Fail2banUnbanTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="f2b-admin", email="f2b-admin@test.com",
            password="pass123",
        )
        self.user = User.objects.create_user(
            username="f2b-user", email="f2b-user@test.com",
            password="pass123",
        )
        self.view = Fail2banUnbanView.as_view()

    def _post(self, user, payload):
        from rest_framework.test import APIRequestFactory, force_authenticate
        req = APIRequestFactory().post(
            "/api/v1/system/fail2ban-unban/", payload, format="json")
        force_authenticate(req, user=user)
        return self.view(req)

    @patch("apps.core.views.security.subprocess.run")
    def test_unban_success(self, mock_run):
        mock_run.return_value = _resp("1\n", returncode=0)
        resp = self._post(self.admin, {"ip": "203.0.113.7", "jail": "sshd"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["status"], "removed")
        args = mock_run.call_args[0][0]
        self.assertEqual(args[:3], ["fail2ban-client", "set", "sshd"])
        self.assertIn("203.0.113.7", args)

    def test_unban_rejects_bad_ip(self):
        resp = self._post(self.admin, {"ip": "not-an-ip", "jail": "sshd"})
        self.assertEqual(resp.status_code, 400)

    def test_unban_rejects_unknown_jail(self):
        # The jail allowlist stops command-injection via the jail
        # field (it becomes a subprocess argv element).
        resp = self._post(
            self.admin, {"ip": "203.0.113.7", "jail": "sshd; rm -rf /"})
        self.assertEqual(resp.status_code, 400)

    def test_unban_requires_admin(self):
        resp = self._post(self.user, {"ip": "203.0.113.7", "jail": "sshd"})
        self.assertEqual(resp.status_code, 403)

    @patch("apps.core.views.security.subprocess.run")
    def test_unban_client_failure_is_generic(self, mock_run):
        mock_run.return_value = _resp("SOCK Aurelius internals", returncode=1)
        resp = self._post(self.admin, {"ip": "203.0.113.7", "jail": "sshd"})
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp.data, {"error": "unban failed"})

    @patch("apps.core.views.security.subprocess.run")
    def test_unban_missing_client_reports_503(self, mock_run):
        mock_run.side_effect = FileNotFoundError("fail2ban-client")
        resp = self._post(self.admin, {"ip": "203.0.113.7", "jail": "sshd"})
        self.assertEqual(resp.status_code, 503)
