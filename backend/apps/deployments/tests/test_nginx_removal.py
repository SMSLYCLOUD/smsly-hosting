# pylint: disable=invalid-name
"""Tests to verify nginx has been fully removed from the platform routing chain."""

import os
from django.test import TestCase


REPO_ROOT = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', '..'
)


class NginxRemovalFromComposeTests(TestCase):
    """Verify docker-compose files no longer reference nginx."""

    def _read_file(self, relative_path):
        path = os.path.join(REPO_ROOT, relative_path)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    def test_prod_compose_no_nginx_service(self):
        content = self._read_file('docker-compose.prod.yml')
        self.assertIsNotNone(content)
        # Should not define an nginx service
        self.assertNotRegex(content, r'^\s+nginx:\s*$', msg="docker-compose.prod.yml still defines nginx service")

    def test_dev_compose_no_nginx_service(self):
        content = self._read_file('docker-compose.yml')
        self.assertIsNotNone(content)
        self.assertNotRegex(content, r'^\s+nginx:\s*$', msg="docker-compose.yml still defines nginx service")

    def test_prod_compose_caddy_not_dependent_on_nginx(self):
        content = self._read_file('docker-compose.prod.yml')
        self.assertIsNotNone(content)
        # Caddy should depend on backend, not nginx
        self.assertNotIn('depends_on:\n      - nginx', content)
        self.assertNotIn('depends_on:\n    - nginx', content)

    def test_prod_compose_no_nginx_image(self):
        content = self._read_file('docker-compose.prod.yml')
        self.assertIsNotNone(content)
        # The route-fallback should use caddy, not nginx
        lines = content.split('\n')
        in_route_fallback = False
        for line in lines:
            if 'route-fallback:' in line:
                in_route_fallback = True
            if in_route_fallback and 'image:' in line:
                self.assertNotIn('nginx', line, "route-fallback still uses nginx image")
                break

    def test_prod_compose_caddy_mounts_static_volumes(self):
        content = self._read_file('docker-compose.prod.yml')
        self.assertIsNotNone(content)
        self.assertIn('static_volume:/app/staticfiles', content)
        self.assertIn('media_volume:/app/media', content)


class NginxRemovalFromScriptsTests(TestCase):
    """Verify deploy scripts no longer reference nginx."""

    def _read_file(self, relative_path):
        path = os.path.join(REPO_ROOT, relative_path)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    def test_deploy_script_no_nginx(self):
        content = self._read_file('scripts/deploy.sh')
        if content is None:
            self.skipTest("deploy.sh not found")
        self.assertNotIn('up -d nginx', content)

    def test_segment_script_no_nginx(self):
        content = self._read_file('scripts/segment.sh')
        if content is None:
            self.skipTest("segment.sh not found")
        self.assertNotIn('"nginx"', content)
        self.assertNotIn("'nginx'", content)

    def test_fix_domain_script_no_nginx(self):
        content = self._read_file('scripts/fix-domain.sh')
        if content is None:
            self.skipTest("fix-domain.sh not found")
        self.assertNotIn('nginx:80', content)


class NginxRemovalFromBackendTests(TestCase):
    """Verify backend code no longer references nginx in critical paths."""

    def _read_file(self, relative_path):
        path = os.path.join(REPO_ROOT, relative_path)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    def test_settings_no_nginx_in_allowed_hosts(self):
        from django.conf import settings
        self.assertNotIn('nginx', settings.ALLOWED_HOSTS)

    def test_ssl_verifier_no_nginx_t(self):
        content = self._read_file('backend/apps/deployments/services/ssl_verifier.py')
        self.assertIsNotNone(content)
        # Should not call nginx -t
        self.assertNotIn('["nginx", "-t"]', content)

    def test_autoscaler_no_nginx_prefix(self):
        content = self._read_file('backend/apps/autoscaler/registry.py')
        self.assertIsNotNone(content)
        self.assertNotIn('smsly-hosting-nginx', content)


class NginxRemovalFromCITests(TestCase):
    """Verify CI workflow no longer checks for nginx health."""

    def _read_file(self, relative_path):
        path = os.path.join(REPO_ROOT, relative_path)
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()

    def test_ci_no_nginx_health_check(self):
        content = self._read_file('.github/workflows/test.yml')
        if content is None:
            self.skipTest("test.yml not found")
        self.assertNotIn('/nginx-health', content)


class NginxRemovalFromHelmTests(TestCase):
    """Verify Helm chart has nginx disabled."""

    def test_helm_nginx_disabled(self):
        path = os.path.join(REPO_ROOT, 'charts', 'smsly-hosting', 'values.yaml')
        if not os.path.exists(path):
            self.skipTest("values.yaml not found")
        with open(path, 'r') as f:
            content = f.read()
        # nginx.enabled should be false
        self.assertRegex(content, r'nginx:\s*\n\s*enabled:\s*false')


class NginxRemovalFromMonitoringTests(TestCase):
    """Verify monitoring no longer scrapes nginx."""

    def test_prometheus_no_nginx_job(self):
        path = os.path.join(REPO_ROOT, 'infrastructure', 'monitoring', 'prometheus.yml')
        if not os.path.exists(path):
            self.skipTest("prometheus.yml not found")
        with open(path, 'r') as f:
            content = f.read()
        self.assertNotIn("job_name: 'nginx'", content)


class NginxRemovalDeadFilesTests(TestCase):
    """Verify dead nginx files have been deleted."""

    def _assert_not_exists(self, relative_path):
        path = os.path.join(REPO_ROOT, relative_path)
        self.assertFalse(os.path.exists(path), f"File should have been deleted: {relative_path}")

    def test_deploy_nginx_auth_deleted(self):
        self._assert_not_exists('scripts/deploy_nginx_auth.py')

    def test_ssl_check_deleted(self):
        self._assert_not_exists('scripts/ssl_check.py')

    def test_fix_implementation_deleted(self):
        self._assert_not_exists('backend/fix_implementation.py')

    def test_traefik_adapter_deleted(self):
        self._assert_not_exists('infrastructure/docker/docker-compose.traefik-adapter.yml')

    def test_diagnose_node1_deleted(self):
        self._assert_not_exists('scratch/diagnose_node1.py')

    def test_ssh_node1_check_nginx_deleted(self):
        self._assert_not_exists('scratch/ssh_node1_check_nginx_config.py')

    def test_nginx_platform_conf_template_deleted(self):
        self._assert_not_exists('infrastructure/nginx/nginx.platform.conf.template')


class RouteFallbackReplacementTests(TestCase):
    """Verify route-fallback uses Caddy instead of nginx."""

    def test_route_fallback_caddyfile_exists(self):
        path = os.path.join(REPO_ROOT, 'infrastructure', 'route-fallback', 'Caddyfile')
        self.assertTrue(os.path.exists(path), "route-fallback Caddyfile should exist")

    def test_route_fallback_caddyfile_content(self):
        path = os.path.join(REPO_ROOT, 'infrastructure', 'route-fallback', 'Caddyfile')
        if not os.path.exists(path):
            self.skipTest("route-fallback Caddyfile not found")
        with open(path, 'r') as f:
            content = f.read()
        # Should proxy recheck to backend
        self.assertIn('reverse_proxy backend:8000', content)
        # Should serve the waking up page
        self.assertIn('respond', content)

    def test_route_fallback_index_html_exists(self):
        path = os.path.join(REPO_ROOT, 'infrastructure', 'route-fallback', 'index.html')
        self.assertTrue(os.path.exists(path), "route-fallback index.html should exist")
