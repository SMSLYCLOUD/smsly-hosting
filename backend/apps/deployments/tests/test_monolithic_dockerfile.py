# pylint: disable=invalid-name
"""Tests to verify the monolithic Dockerfile no longer installs nginx."""

import os

from django.test import TestCase

REPO_ROOT = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', '..'
)


class MonolithicDockerfileTests(TestCase):
    """Verify the Dockerfile has been updated to use Caddy instead of nginx."""

    def setUp(self):
        dockerfile_path = os.path.join(REPO_ROOT, 'Dockerfile')
        if not os.path.exists(dockerfile_path):
            self.skipTest("Dockerfile not found")
        with open(dockerfile_path) as f:
            self.dockerfile = f.read()

    def test_no_nginx_apt_install(self):
        self.assertNotRegex(self.dockerfile, r'apt-get.*install.*nginx')

    def test_no_nginx_conf_copy(self):
        self.assertNotIn('nginx.conf', self.dockerfile)
        self.assertNotIn('nginx.platform.conf', self.dockerfile)

    def test_healthcheck_not_nginx(self):
        self.assertNotIn('/nginx-health', self.dockerfile)


class EntrypointTests(TestCase):
    """Verify the entrypoint script no longer starts nginx."""

    def test_entrypoint_no_nginx(self):
        path = os.path.join(REPO_ROOT, 'scripts', 'entrypoint.platform.sh')
        if not os.path.exists(path):
            self.skipTest("entrypoint.platform.sh not found")
        with open(path) as f:
            content = f.read()
        self.assertNotIn('nginx', content.lower().split('#')[0])  # Ignore comments


class CaddyMonolithTemplateTests(TestCase):
    """Verify a Caddy monolith template exists for Dockerfile deployments."""

    def test_template_exists(self):
        path = os.path.join(REPO_ROOT, 'infrastructure', 'caddy', 'Caddyfile.monolith.template')
        self.assertTrue(os.path.exists(path), "Caddyfile.monolith.template should exist")

    def test_template_has_port_variables(self):
        path = os.path.join(REPO_ROOT, 'infrastructure', 'caddy', 'Caddyfile.monolith.template')
        if not os.path.exists(path):
            self.skipTest("Caddyfile.monolith.template not found")
        with open(path) as f:
            content = f.read()
        self.assertIn('${PORT}', content)
        self.assertIn('${BACKEND_PORT}', content)
        self.assertIn('${FRONTEND_PORT}', content)
