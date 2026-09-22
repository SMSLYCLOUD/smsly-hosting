"""Unit tests for the edge watchdog (no network, no DB)."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.domains.services import edge_watch as ew


class TestCfEdgeIp(TestCase):
    def test_cf_ranges_match(self):
        self.assertTrue(ew.is_cf_edge_ip('104.16.0.1'))
        self.assertTrue(ew.is_cf_edge_ip('172.64.10.20'))
        self.assertTrue(ew.is_cf_edge_ip('131.0.72.5'))

    def test_origin_and_public_ips_rejected(self):
        self.assertFalse(ew.is_cf_edge_ip('89.58.45.52'))
        self.assertFalse(ew.is_cf_edge_ip('8.8.8.8'))
        self.assertFalse(ew.is_cf_edge_ip('not-an-ip'))


class TestPublicDnsIps(TestCase):
    def test_parses_answers(self):
        payload = {"Answer": [
            {"name": "x", "type": 1, "data": "1.2.3.4"},
            {"name": "x", "type": 28, "data": "2606::1"},
            {"name": "x", "type": 5, "data": "alias"},
        ]}
        with patch.object(ew, '_http_json', return_value=payload):
            self.assertEqual(
                ew.public_dns_ips('x'), ['1.2.3.4', '2606::1'])

    def test_failure_returns_empty(self):
        with patch.object(ew, '_http_json', side_effect=RuntimeError('down')):
            self.assertEqual(ew.public_dns_ips('x'), [])


class TestCheckEdge(TestCase):
    def _cfg(self, **kw):
        base = dict(domain='grid.example.com', edge_proxy_records=True,
                    server_ip='9.9.9.9', cloudflare_api_token='tok')
        base.update(kw)
        return SimpleNamespace(**base)

    def _run(self, cfg, a_records, probe):
        with patch('apps.deployments.models.PlatformConfig.load', return_value=cfg), \
                patch.object(ew, 'public_dns_ips', return_value=a_records), \
                patch.object(ew, 'probe_public_url', return_value=probe), \
                patch.object(ew, '_heal_proxy_state', return_value='') as heal:
            return ew.check_edge(), heal

    def test_proxied_healthy(self):
        report, heal = self._run(
            self._cfg(), ['104.16.0.5'], (True, '104.16.0.5 HTTP 200'))
        self.assertTrue(report['ok'])
        self.assertEqual(report['errors'], [])
        heal.assert_not_called()

    def test_drift_detected_and_heal_attempted(self):
        report, heal = self._run(
            self._cfg(), ['9.9.9.9'], (True, '9.9.9.9 HTTP 200'))
        self.assertFalse(report['ok'])
        self.assertTrue(any('drift' in e for e in report['errors']))
        heal.assert_called_once()

    def test_probe_failure_fails(self):
        report, _ = self._run(
            self._cfg(), ['104.16.0.5'], (False, 'timeout'))
        self.assertFalse(report['ok'])
        self.assertTrue(any('probe' in e for e in report['errors']))

    def test_no_domain_configured(self):
        cfg = self._cfg(domain='')
        with patch('apps.deployments.models.PlatformConfig.load', return_value=cfg):
            report = ew.check_edge()
        self.assertFalse(report['ok'])
