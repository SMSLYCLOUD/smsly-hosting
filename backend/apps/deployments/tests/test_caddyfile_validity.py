# pylint: disable=invalid-name
"""Tests to verify the Caddyfile is valid and routes correctly after nginx removal."""

import os

from django.test import TestCase

CADDYFILE_PATH = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', '..', 'caddy-config', 'Caddyfile'
)


class CaddyfileValidityTests(TestCase):
    """Verify the static Caddyfile has correct structure after nginx removal."""

    def setUp(self):
        with open(CADDYFILE_PATH) as f:
            self.caddyfile = f.read()

    def test_caddyfile_exists(self):
        self.assertTrue(os.path.exists(CADDYFILE_PATH))

    def test_no_nginx_references(self):
        """The Caddyfile should not reference nginx:80 anymore."""
        # Allow the comment mentioning 'no nginx' but not as a reverse_proxy target
        lines = self.caddyfile.split('\n')
        for line in lines:
            stripped = line.strip()
            if 'reverse_proxy' in stripped and 'nginx' in stripped:
                self.fail(f"Caddyfile still proxies to nginx: {stripped}")

    def test_proxies_to_backend(self):
        self.assertIn('reverse_proxy backend:8000', self.caddyfile)

    def test_proxies_to_frontend(self):
        self.assertIn('reverse_proxy frontend:3000', self.caddyfile)

    def test_on_demand_tls_asks_backend(self):
        self.assertIn('ask http://backend:8000/api/v1/services/check-domain/', self.caddyfile)

    def test_api_route(self):
        self.assertIn('@api path /api/*', self.caddyfile)

    def test_websocket_route(self):
        self.assertIn('@ws path /ws/*', self.caddyfile)

    def test_admin_route(self):
        self.assertIn('@admin path /admin/*', self.caddyfile)

    def test_health_route(self):
        self.assertIn('@health path /health', self.caddyfile)

    def test_static_file_serving(self):
        self.assertIn('@static path /static/*', self.caddyfile)
        self.assertIn('root * /app/staticfiles', self.caddyfile)

    def test_media_file_serving(self):
        self.assertIn('@media path /media/*', self.caddyfile)
        self.assertIn('root * /app/media', self.caddyfile)

    def test_caddy_health_endpoint(self):
        self.assertIn('respond /caddy-health 200', self.caddyfile)

    def test_gzip_encoding(self):
        self.assertIn('encode gzip', self.caddyfile)

    def test_backup_download_no_buffering(self):
        self.assertIn('flush_interval -1', self.caddyfile)

    def test_websocket_timeout(self):
        """WebSocket routes should have long timeouts."""
        self.assertIn('read_timeout 3600s', self.caddyfile)

    def test_oauth_routes_on_backend(self):
        """OAuth routes must stay on backend, not frontend."""
        self.assertIn('/accounts/github/*', self.caddyfile)
        self.assertIn('/accounts/google/*', self.caddyfile)

    def test_oauth_connect_routes_on_frontend(self):
        """OAuth connect callback routes must go to frontend."""
        self.assertIn('@oauth_github_connect', self.caddyfile)
        self.assertIn('@oauth_google_connect', self.caddyfile)
        self.assertIn('@oauth_gitlab_connect', self.caddyfile)
        self.assertIn('@oauth_bitbucket_connect', self.caddyfile)

    def test_http_to_https_redirect(self):
        """The :80 block should redirect to HTTPS for non-IP hosts."""
        self.assertIn('redir @redirectable https://{host}{uri} 308', self.caddyfile)

    def test_acme_challenge_handling(self):
        """ACME challenge path should be handled."""
        self.assertIn('/.well-known/acme-challenge/*', self.caddyfile)

    def test_domain_block_structure(self):
        """The {$DOMAIN} block should exist."""
        self.assertIn('{$DOMAIN} {', self.caddyfile)

    def test_port80_block_structure(self):
        """:80 block should exist for HTTP fallback."""
        self.assertIn(':80 {', self.caddyfile)
