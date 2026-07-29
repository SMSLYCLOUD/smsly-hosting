# pylint: disable=invalid-name
"""
Regression tests for Issue 62 (tunnels.register accepts any
local_port, including 22 / 3306 / etc).

Before the fix, a user could register a tunnel with
``local_port=22`` and the platform would forward SSH attempts
at ``subdomain.tunnel.<DOMAIN>`` to their local SSH daemon.
After the fix, the CLI register endpoint rejects
* denied service ports (22, 80, 443, 3306, …)
* ports outside the 1024–9999 / 20000–29999 ranges
* non-integer values
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models.tunnels import Tunnel
from apps.deployments.views.tunnels import (
    ALLOWED_TUNNEL_PORTS,
    DENIED_TUNNEL_PORTS,
)

User = get_user_model()


class TunnelPortRestrictionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='tunnel-port-user', password='123',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = '/api/v1/tunnels/register/'

    def test_denied_port_22_is_rejected(self):
        resp = self.client.post(
            self.url, {'subdomain': 'cli-ssh', 'local_port': 22},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('reserved', str(resp.data).lower())
        self.assertFalse(
            Tunnel.objects.filter(subdomain='cli-ssh').exists()
        )

    def test_denied_port_3306_is_rejected(self):
        resp = self.client.post(
            self.url, {'subdomain': 'cli-mysql', 'local_port': 3306},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_denied_port_5432_is_rejected(self):
        resp = self.client.post(
            self.url, {'subdomain': 'cli-pg', 'local_port': 5432},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_denied_port_6379_is_rejected(self):
        resp = self.client.post(
            self.url, {'subdomain': 'cli-redis', 'local_port': 6379},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_port_below_allowed_range_is_rejected(self):
        # 80 is denied, but 25 is also denied; test 1023
        # (just below allowed range) which is neither in
        # ALLOWED nor in DENIED.
        resp = self.client.post(
            self.url, {'subdomain': 'cli-low', 'local_port': 1023},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('outside', str(resp.data).lower())

    def test_port_above_allowed_range_is_rejected(self):
        # 30000 is just above the 20000-29999 allowed range.
        resp = self.client.post(
            self.url, {'subdomain': 'cli-high', 'local_port': 30000},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_non_integer_port_is_rejected(self):
        resp = self.client.post(
            self.url, {'subdomain': 'cli-str', 'local_port': 'abc'},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('integer', str(resp.data).lower())

    def test_allowed_port_8080_is_accepted(self):
        resp = self.client.post(
            self.url, {'subdomain': 'cli-ok', 'local_port': 8080},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        tunnel = Tunnel.objects.get(subdomain='cli-ok')
        self.assertEqual(tunnel.local_port, 8080)

    def test_allowed_port_in_high_range_is_accepted(self):
        resp = self.client.post(
            self.url, {'subdomain': 'cli-ok-hi', 'local_port': 25000},
            format='json',
        )
        self.assertEqual(resp.status_code, 201)
        tunnel = Tunnel.objects.get(subdomain='cli-ok-hi')
        self.assertEqual(tunnel.local_port, 25000)

    def test_denied_check_runs_before_allowed_check(self):
        # 3306 (MySQL) is in the user-allowed range (1024-9999)
        # AND in the explicit deny list. The contract is that
        # ``DENIED_TUNNEL_PORTS`` is checked first, so a port
        # that's in both lists is rejected.
        self.assertIn(3306, ALLOWED_TUNNEL_PORTS)
        self.assertIn(3306, DENIED_TUNNEL_PORTS)
        resp = self.client.post(
            self.url,
            {'subdomain': 'cli-deny-wins', 'local_port': 3306},
            format='json',
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('reserved', str(resp.data).lower())
