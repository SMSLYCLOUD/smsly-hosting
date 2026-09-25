# pylint: disable=invalid-name
"""Backend proxy retry budget (2026-09-25 outage).

A backend container recreate leaves a dial gap (old container gone,
gunicorn still booting) — every bare `reverse_proxy backend:8000`
returned instant 502s (user-facing 503s) for ~1 min on each restart.
generate_caddyfile() now rewrites every bare backend proxy into a
block with an lb retry budget via _apply_backend_retry_budget().
"""
import re

from django.test import TestCase

from apps.deployments.models import PlatformConfig
from apps.deployments.services.caddy_manager.config_generation import (
    _apply_backend_retry_budget,
    generate_caddyfile,
)


class BackendRetryBudgetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        config = PlatformConfig.load()
        config.domain = "grid.example.test"
        config.use_ssl = True
        config.wildcard_subdomains = True
        config.server_ip = None
        config.save()
        cls.caddyfile = generate_caddyfile(config)

    def test_no_bare_backend_proxies_remain(self):
        bare = re.findall(
            r"(?m)^[ \t]*reverse_proxy backend:8000[ \t]*$",
            self.caddyfile,
        )
        self.assertEqual(bare, [])

    def test_every_backend_block_has_retry_budget(self):
        blocks = re.findall(
            r"(?m)^[ \t]*reverse_proxy backend:8000 \{$([^}]*)\}",
            self.caddyfile,
        )
        # The platform block alone emits several backend proxies; if
        # the rewrite silently stopped matching, this catches it.
        self.assertGreater(len(blocks), 0)
        for body in blocks:
            self.assertIn("lb_try_duration 60s", body)
            self.assertIn("lb_try_interval 1s", body)
            self.assertIn("lb_retries 8", body)

    def test_non_backend_proxies_untouched(self):
        # frontend/traefik upstreams keep their existing form.
        self.assertIn("reverse_proxy frontend:3000", self.caddyfile)
        self.assertNotIn("frontend:3000 {", self.caddyfile)

    def test_rewrite_is_idempotent(self):
        once = _apply_backend_retry_budget(self.caddyfile)
        # Already-blocked lines end with `{` so nothing matches twice.
        self.assertEqual(once, self.caddyfile)

    def test_rewrite_preserves_indentation(self):
        snippet = "    handle /api/* {\n        reverse_proxy backend:8000\n    }\n"
        out = _apply_backend_retry_budget(snippet)
        self.assertIn(
            "        reverse_proxy backend:8000 {\n"
            "            lb_try_duration 60s\n"
            "            lb_try_interval 1s\n"
            "            lb_retries 8\n"
            "        }",
            out,
        )

    def test_rewrite_enriches_existing_blocks(self):
        # Challenge handles (header_up, no lb options) get the budget
        # injected; second run is a no-op.
        snippet = (
            "        reverse_proxy backend:8000 {\n"
            "            header_up Host localhost\n"
            "        }\n"
        )
        enriched = _apply_backend_retry_budget(snippet)
        self.assertIn("lb_try_duration 60s", enriched)
        self.assertIn("header_up Host localhost", enriched)
        self.assertEqual(_apply_backend_retry_budget(enriched), enriched)
