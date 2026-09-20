"""Tests for the security-status frontend wiring contract.

The Settings and service Security tabs render open-appsec, Falco
capture truth, and first-strike runtime state from
GET /api/v1/system/security-status/. These tests pin the response
shape with subprocess mocked (no Docker needed):

* falco carries restarts + capturing (None when logs unavailable,
  False only on positive scap_init failure evidence).
* openappsec section present with enabled/agent/envoy/policy/verdicts.
* crowdsec carries first_strike_enabled + first_strike_active.
"""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.core.views.security import SecurityStatusView


def _resp(stdout="", returncode=0):
    m = MagicMock()
    m.stdout = stdout
    # stderr MUST be a real string: the view concatenates
    # stdout+stderr, and a MagicMock would __radd__ into a truthy
    # mock on which `in` is always False (false greens).
    m.stderr = ""
    m.returncode = returncode
    return m


def _run_map():
    """subprocess.run side effect keyed by command shape."""
    def _run(cmd, **kwargs):
        joined = " ".join(cmd)
        if cmd[:2] == ["aa-status", "--enabled"]:
            return _resp("", returncode=1)
        if "docker" in cmd[0] and "ps" in cmd:
            return _resp("smsly-x Up 2 hours (healthy)\n")
        if "inspect" in cmd:
            return _resp("3\n")
        if "logs" in cmd:
            if "smsly-falco" in joined:
                return _resp("some log without the marker\n")
            return _resp("got final verict: 1\n")
        if "port" in cmd:
            return _resp("8081/tcp -> 127.0.0.1:18081\n")
        if "exec" in cmd and "local_policy.yaml" in joined:
            return _resp("mode: detect-learn\n")
        if "exec" in cmd and "first-strike" in joined:
            return _resp("3\n")
        if "exec" in cmd and "cscli" in joined:
            return _resp("[]\n")
        if "exec" in cmd and "--list-options" in joined:
            return _resp("modern_ebpf\n")
        return _resp("", returncode=1)
    return _run


class SecurityStatusWiringTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="sec-admin", email="sec-admin@test.com",
            password="pass123",
        )
        from apps.deployments.models.core import PlatformConfig
        # load() caches the singleton in LocMemCache, which Django does
        # NOT flush between tests — without this, test N>1 gets test 1's
        # rolled-back row and save(update_fields) hits zero rows.
        PlatformConfig.clear_cache()
        config = PlatformConfig.load()
        config.enable_crowdsec_waf = True
        config.save(update_fields=["enable_crowdsec_waf"])
        self.view = SecurityStatusView.as_view()

    def _get(self):
        from rest_framework.test import APIRequestFactory, force_authenticate
        req = APIRequestFactory().get("/api/v1/system/security-status/")
        force_authenticate(req, user=self.admin)
        return self.view(req)

    @patch("apps.core.views.security.subprocess.run")
    def test_falco_reports_restarts_and_capturing(self, mock_run):
        mock_run.side_effect = _run_map()
        data = self._get().data
        self.assertTrue(data["falco"]["running"])
        self.assertEqual(data["falco"]["restarts"], 3)
        self.assertTrue(data["falco"]["capturing"])

    @patch("apps.core.views.security.subprocess.run")
    def test_falco_scap_failure_reports_not_capturing(self, mock_run):
        def _run(cmd, **kwargs):
            if "logs" in cmd and "smsly-falco" in " ".join(cmd):
                return _resp("Error: Initialization issues during scap_init\n")
            return _run_map()(cmd, **kwargs)
        mock_run.side_effect = _run
        data = self._get().data
        self.assertTrue(data["falco"]["running"])
        self.assertFalse(data["falco"]["capturing"])

    @patch("apps.core.views.security.subprocess.run")
    def test_openappsec_section_wired(self, mock_run):
        mock_run.side_effect = _run_map()
        with patch.dict("os.environ", {"OPENAPPSEC_ENABLED": "1"}):
            data = self._get().data
        oa = data["openappsec"]
        self.assertTrue(oa["enabled"])
        self.assertTrue(oa["agent_running"])
        self.assertTrue(oa["envoy_running"])
        self.assertEqual(oa["policy_mode"], "detect-learn")
        self.assertTrue(oa["verdicts_recent"])
        self.assertEqual(oa["shadow_port"], 18081)

    @patch("apps.core.views.security.subprocess.run")
    def test_openappsec_disabled_in_db_overrides_env(self, mock_run):
        # DB toggle is source of truth: explicit False wins even when the
        # container env still carries the old install-time value.
        from apps.deployments.models.core import PlatformConfig
        config = PlatformConfig.load()
        config.openappsec_enabled = False
        config.save(update_fields=["openappsec_enabled"])
        mock_run.side_effect = _run_map()
        with patch.dict("os.environ", {"OPENAPPSEC_ENABLED": "1"}):
            data = self._get().data
        self.assertFalse(data["openappsec"]["enabled"])

    @patch("apps.core.views.security.subprocess.run")
    def test_crowdsec_first_strike_runtime_flag(self, mock_run):
        mock_run.side_effect = _run_map()
        data = self._get().data
        self.assertIn("first_strike_active", data["crowdsec"])
        self.assertTrue(data["crowdsec"]["first_strike_active"])
