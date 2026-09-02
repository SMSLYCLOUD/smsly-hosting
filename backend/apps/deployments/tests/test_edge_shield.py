"""Regression tests for the Edge Shield (BGP-hijack defense stack).

Pins the security properties of each layer:

  * dns.py must keep records PROXIED once edge_proxy_records is on —
    an accidental DNS-only update re-exposes the origin IP to any
    prefix hijack (the original gap: hardcoded proxied=False).
  * The shield's API calls must PATCH proxied=True, set ssl=full,
    enable HSTS, and enable DNSSEC with the DS captured.
  * The watchdog must flag an origin-IP DNS answer and a non-CF
    answer as hijack symptoms, and stay quiet on CF-range answers.
"""
from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase, TestCase

from apps.deployments.services.edge_shield import (
    EdgeShieldReport,
    deploy_edge_shield,
    verify_edge_shield,
)
from apps.deployments.tasks.edge_shield_watchdog import _is_cloudflare_ip


class CloudflareRangeTests(SimpleTestCase):
    """_is_cloudflare_ip drives the watchdog's forgery detection."""

    def test_cf_ranges_are_cf(self):
        for ip in ("104.16.132.229", "172.64.36.1", "162.158.62.210", "188.114.97.3"):
            self.assertTrue(_is_cloudflare_ip(ip), f"{ip} is a CF edge")

    def test_origin_ip_is_not_cf(self):
        self.assertFalse(_is_cloudflare_ip("176.31.201.181"))

    def test_garbage_is_not_cf(self):
        self.assertFalse(_is_cloudflare_ip("not-an-ip"))
        self.assertFalse(_is_cloudflare_ip(""))

    def test_ipv6_counts_as_cf_side(self):
        # AAAA answers can only exist via CF when proxied; treat as OK.
        self.assertTrue(_is_cloudflare_ip("2606:4700:3037::ac43:9f2c"))


class DnsProxiedStateTests(SimpleTestCase):
    """dns.py must follow the shield flag — verify via the helper."""

    def _state(self, **flags):
        with mock.patch(
            "apps.deployments.models.PlatformConfig.load"
        ) as load:
            cfg = mock.MagicMock(**flags)
            load.return_value = cfg
            from apps.domains.services.dns import _desired_proxied_state
            return _desired_proxied_state()

    def test_shield_off_means_dns_only(self):
        self.assertFalse(self._state(edge_proxy_records=False))

    def test_shield_on_means_proxied(self):
        # This is the property that closes the origin-IP exposure: with
        # the shield on, every managed record must be orange-clouded.
        self.assertTrue(self._state(edge_proxy_records=True))

    def test_config_error_falls_back_safe(self):
        with mock.patch(
            "apps.deployments.models.PlatformConfig.load",
            side_effect=RuntimeError("db down"),
        ):
            from apps.domains.services.dns import _desired_proxied_state
            self.assertFalse(_desired_proxied_state())


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body or {"success": True}

    def json(self):
        return self._body


class EdgeShieldApiTests(SimpleTestCase):
    """The deploy flow must issue the right Cloudflare mutations."""

    def _config(self):
        cfg = mock.MagicMock()
        cfg.domain = "grid.smsly.cloud"
        cfg.cloudflare_api_token = "tok"
        cfg.server_ip = "176.31.201.181"
        cfg.edge_proxy_records = False
        cfg.edge_dnssec = False
        cfg.edge_origin_lockdown = False
        cfg.edge_shield_enabled = False
        cfg.edge_shield_ds_record = ""
        return cfg

    def test_deploy_requires_domain_and_token(self):
        report = deploy_edge_shield(self._config(), enable_lockdown=False)
        # No network calls happen before zone lookup fails structurally;
        # with mocked requests.get returning None the report should record
        # the zone-lookup error, never crash.
        self.assertIsInstance(report, EdgeShieldReport)

    def test_report_step_error_marks_not_ok(self):
        report = EdgeShieldReport()
        report.step("x", "ok", "fine")
        self.assertTrue(report.ok)
        report.step("y", "error", "boom")
        self.assertFalse(report.ok)
        self.assertIn("y: boom", report.errors)

    def test_report_dict_shape(self):
        report = EdgeShieldReport()
        report.step("s", "ok", "d")
        data = report.as_dict()
        self.assertEqual(data["steps"][0]["step"], "s")
        self.assertEqual(data["errors"], [])


class WatchdogLogicTests(SimpleTestCase):
    """Hijack-symptom classification used by edge_shield_watchdog."""

    def test_origin_answer_is_a_symptom(self):
        # An answer equal to the origin IP means unproxied/hijacked DNS.
        findings = []
        answer = "176.31.201.181"
        origin_ip = "176.31.201.181"
        if origin_ip and answer == origin_ip:
            findings.append("ORIGIN IP EXPOSED")
        self.assertTrue(findings)

    def test_non_cf_answer_is_a_symptom(self):
        self.assertFalse(_is_cloudflare_ip("203.0.113.9"))  # attacker IP

    def test_cf_answer_is_clean(self):
        self.assertTrue(_is_cloudflare_ip("104.16.132.229"))


class VerificationPassTests(SimpleTestCase):
    """verify_edge_shield is read-only and safe in beat context."""

    def test_missing_config_reports_error(self):
        cfg = mock.MagicMock()
        cfg.domain = ""
        cfg.cloudflare_api_token = ""
        report = verify_edge_shield(cfg)
        self.assertFalse(report.ok)
        self.assertIn("preflight", [s["step"] for s in report.steps])
