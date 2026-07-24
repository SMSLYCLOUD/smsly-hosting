from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models.servers import ManagedServer
from apps.deployments.views.server.helpers import _build_remote_headers

User = get_user_model()


class RemoteSyncHeaderTrustTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="trust-user", password="p",
        )

    def tearDown(self):
        ManagedServer.objects.filter(owner=self.user).delete()
        self.user.delete()

    def _make(self, **overrides):
        defaults = {
            "owner": self.user,
            "name": "trust-test",
            "host": "203.0.113.10",
            "api_url": "https://203.0.113.10",
            "api_token": "tok",
            "gateway_secret": "secret",
            "is_lite_agent": False,
        }
        defaults.update(overrides)
        return ManagedServer.objects.create(**defaults)

    def test_trusted_full_server_gets_header(self):
        server = self._make(is_lite_agent=False, gateway_secret="peer-secret")
        headers = _build_remote_headers(server, method="GET", path="/api/v1/services/")
        self.assertEqual(headers.get("X-SMSLY-Remote-Sync"), "1")

    def test_lite_agent_server_does_not_get_header(self):
        server = self._make(is_lite_agent=True, gateway_secret="peer-secret")
        headers = _build_remote_headers(server, method="GET", path="/api/v1/services/")
        self.assertNotIn("X-SMSLY-Remote-Sync", headers)

    def test_server_without_gateway_secret_does_not_get_header(self):
        server = self._make(is_lite_agent=False, gateway_secret="")
        headers = _build_remote_headers(server, method="GET", path="/api/v1/services/")
        self.assertNotIn("X-SMSLY-Remote-Sync", headers)

    def test_user_registered_server_without_trust_does_not_get_header(self):
        server = self._make(is_lite_agent=False, gateway_secret="")
        headers = _build_remote_headers(server, method="GET", path="/api/v1/services/")
        self.assertNotIn("X-SMSLY-Remote-Sync", headers)
