"""Consecutive-failure demotion for custom-domain reverify.

A single transient (resolver blip, slow token serve) must not demote a
live domain the way trulay.co was demoted: demotion requires 3
consecutive failures, and any success resets the counter.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from apps.domains.tasks import reverify as reverify_mod


def _domain(fails=0):
    d = mock.MagicMock()
    d.domain_name = "example.com"
    d.service = SimpleNamespace(name="svc")
    d.verify_fail_count = fails
    d.status = "active"
    d.verified = True
    return d


def _result(ok):
    return SimpleNamespace(verified=ok, error="" if ok else "nope", actual="x")


def _run(domains, ok):
    qs = mock.MagicMock()
    qs.filter.return_value = qs
    qs.select_related.return_value = domains
    with mock.patch("apps.domains.models.Domain") as mock_domain, \
         mock.patch("apps.domains.verification.verify_custom_domain_dns",
                    return_value=_result(ok)), \
         mock.patch("apps.domains.verification.ensure_verification_token"), \
         mock.patch("apps.deployments.models.PlatformConfig"):
        mock_domain.objects.filter.return_value = qs
        out = reverify_mod.reverify_custom_domains_task.run()
    return out


class DemotionCounterTests(SimpleTestCase):
    def test_first_two_failures_keep_status(self):
        for fails in (0, 1):
            d = _domain(fails=fails)
            _run([d], ok=False)
            self.assertTrue(d.verified)
            self.assertEqual(d.verify_fail_count, fails + 1)
            self.assertNotEqual(d.status, "dns_pending")

    def test_third_failure_demotes(self):
        d = _domain(fails=2)
        out = _run([d], ok=False)
        self.assertFalse(d.verified)
        self.assertEqual(d.status, "dns_pending")
        self.assertIn("example.com", out["demoted"])

    def test_success_resets_counter(self):
        d = _domain(fails=2)
        _run([d], ok=True)
        self.assertEqual(d.verify_fail_count, 0)
        self.assertTrue(d.verified)


class RepromoteTests(SimpleTestCase):
    """A demoted row that passes the full bar must self-heal.

    Regression for trulay.co 2026-09-26: three hourly read-timeouts
    demoted the apex, and nothing ever re-promoted it — it stayed red
    until a human clicked verify, even though every later pass would
    have cleared it.
    """

    def _demoted(self, fails=3):
        d = _domain(fails=fails)
        d.verified = False
        d.status = "dns_pending"
        d.ssl_active = False
        d.last_error = "old"
        return d

    def _run_repromote(self, domains, ok):
        from apps.domains.models import DomainStatus
        qs = mock.MagicMock()
        qs.filter.return_value = qs
        qs.select_related.return_value = domains
        with mock.patch("apps.domains.models.Domain") as mock_domain, \
             mock.patch("apps.domains.verification.verify_custom_domain_dns",
                        return_value=_result(ok)), \
             mock.patch("apps.domains.verification.ensure_verification_token"), \
             mock.patch("apps.deployments.models.PlatformConfig"), \
             mock.patch("apps.domains.tasks.verify_dns_and_provision_ssl_task") as mock_ssl, \
             mock.patch("apps.deployments.tasks.deploy.caddy.sync_caddy_task") as mock_caddy:
            mock_domain.objects.filter.return_value = qs
            mock_domain.DomainStatus = DomainStatus
            out = reverify_mod.reverify_custom_domains_task.run()
        return out, mock_ssl, mock_caddy

    def test_passing_demoted_domain_repromotes(self):
        d = self._demoted()
        out, mock_ssl, mock_caddy = self._run_repromote([d], ok=True)
        self.assertTrue(d.verified)
        self.assertEqual(d.verify_fail_count, 0)
        self.assertNotIn("example.com", out["demoted"])
        mock_ssl.delay.assert_called_once()
        mock_caddy.delay.assert_called_once()

    def test_failing_demoted_domain_stays_quiet(self):
        d = self._demoted(fails=5)
        out, mock_ssl, mock_caddy = self._run_repromote([d], ok=False)
        self.assertFalse(d.verified)
        self.assertNotIn("example.com", out["demoted"])
        mock_ssl.delay.assert_not_called()
        mock_caddy.delay.assert_not_called()
