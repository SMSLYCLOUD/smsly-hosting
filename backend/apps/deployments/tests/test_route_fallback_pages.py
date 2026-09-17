"""Route-fallback error pages: branding, diagnostics, and edge wiring.

Every 503 the platform serves must identify the product, carry a
per-request ID correlatable with edge logs, and stay out of search
indexes. These tests pin the static contract (the rendering itself is
verified live against the running container).
"""
import os

from django.test import SimpleTestCase

REPO_ROOT = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', '..'
)


def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as fh:
        return fh.read()


class RouteFallbackPagesTests(SimpleTestCase):
    def test_waking_page_has_brand(self):
        html = _read('infrastructure', 'route-fallback', 'index.html')
        self.assertIn('Grid', html)
        self.assertIn('Trulay', html)
        self.assertIn('<svg', html)

    def test_disabled_page_has_brand(self):
        html = _read('infrastructure', 'route-fallback', 'disabled.html')
        self.assertIn('Grid', html)
        self.assertIn('Trulay', html)
        self.assertIn('<svg', html)

    def test_pages_carry_request_id_slot(self):
        # Rendered server-side by Caddy templates (http.request.uuid);
        # JS degrades to "unavailable" when templates are off.
        for page in ('index.html', 'disabled.html'):
            html = _read('infrastructure', 'route-fallback', page)
            self.assertIn('http.request.uuid', html)
            self.assertIn('id="reqid"', html)
            self.assertIn('unavailable', html)

    def test_no_stray_template_actions(self):
        # Go template parsing fails the ENTIRE page on any malformed
        # action — including one inside a JS comment (2026-09-18: our own
        # fallback check contained a literal "{{" and every 503 served a
        # truncated empty body). Exactly one action per page: the
        # request-ID placeholder.
        for page in ('index.html', 'disabled.html'):
            html = _read('infrastructure', 'route-fallback', page)
            self.assertEqual(
                html.count('{{'), 1,
                f"{page}: want exactly one template action (request ID)",
            )
            self.assertIn('{{placeholder "http.request.uuid"}}', html)

    def test_pages_stay_out_of_search_indexes(self):
        for page in ('index.html', 'disabled.html'):
            html = _read('infrastructure', 'route-fallback', page)
            self.assertIn('noindex', html)

    def test_pages_show_utc_timestamp_slot(self):
        for page in ('index.html', 'disabled.html'):
            html = _read('infrastructure', 'route-fallback', page)
            self.assertIn('id="ts"', html)

    def test_caddyfile_emits_request_id_header(self):
        caddyfile = _read('infrastructure', 'route-fallback', 'Caddyfile')
        self.assertIn('X-SMSLY-Request-ID', caddyfile)
        self.assertIn('http.request.uuid', caddyfile)

    def test_caddyfile_renders_templates(self):
        caddyfile = _read('infrastructure', 'route-fallback', 'Caddyfile')
        self.assertIn('templates', caddyfile)

    def test_caddyfile_keeps_json_access_log(self):
        caddyfile = _read('infrastructure', 'route-fallback', 'Caddyfile')
        self.assertIn('format json', caddyfile)

    def test_pages_have_no_external_dependencies(self):
        # Fallback pages must render while the edge is degraded: no
        # external stylesheets, scripts, images, or fonts.
        for page in ('index.html', 'disabled.html'):
            html = _read('infrastructure', 'route-fallback', page)
            self.assertNotIn('src="http', html)
            self.assertNotIn('href="http', html)
            self.assertNotIn('@import', html)

    def test_pages_have_inline_favicon(self):
        # Browsers request /favicon.ico on every error page view; a
        # data-URI icon avoids an extra edge round-trip and keeps the
        # tab branded while everything else is degraded. The icon must
        # be the platform logo mark (layered stack), not a placeholder.
        for page in ('index.html', 'disabled.html'):
            html = _read('infrastructure', 'route-fallback', page)
            self.assertIn('rel="icon"', html)
            self.assertIn('data:image/svg+xml', html)
            self.assertIn('3EDB9C', html)
            self.assertIn('M15 35 L30 27.5 L45 35', html)

    def test_compose_uses_directory_bind(self):
        # Single-file binds go stale on git pull (replaced inodes): the
        # container keeps serving the deleted inode until recreated.
        # A directory bind follows replacements (2026-09-18 incident).
        with open(os.path.join(REPO_ROOT, 'docker-compose.prod.yml')) as fh:
            compose = fh.read()
        idx = compose.find('route-fallback:')
        self.assertNotEqual(idx, -1)
        block = compose[idx:idx + 2000]
        self.assertIn('./infrastructure/route-fallback:/etc/rb-fallback', block)
        self.assertNotIn('/srv/index.html', block)
        self.assertIn('--config /etc/rb-fallback/Caddyfile', block)
