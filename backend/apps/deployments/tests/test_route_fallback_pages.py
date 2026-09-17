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
