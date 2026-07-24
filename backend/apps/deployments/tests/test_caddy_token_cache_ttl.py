# pylint: disable=invalid-name
"""Tests for the Cloudflare token cache TTL behavior in caddy_manager."""

import json
import os
import tempfile
import time
from unittest.mock import patch

from django.test import SimpleTestCase
from apps.deployments.services import caddy_manager


class CaddyTokenCacheTtlTests(SimpleTestCase):
    """Verify the .cloudflare_token_cache file honors a TTL window."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.cache_path = os.path.join(self._tmpdir.name, ".cloudflare_token_cache")
        self.token_file = os.path.join(self._tmpdir.name, ".cloudflare_token")
        self.clear_file = os.path.join(self._tmpdir.name, ".cloudflare_token_clear")

    def _write_payload(self, token: str, expires_at: float):
        with open(self.cache_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"token": token, "expires_at": expires_at}))

    def test_expired_token_returns_none_and_removes_file(self):
        """A cache entry that already expired is treated as missing."""
        self._write_payload("abc-token", time.time() - 1)
        with patch.object(caddy_manager, "CADDY_TOKEN_CACHE", self.cache_path), \
             patch.object(caddy_manager, "CADDY_TOKEN_FILE", self.token_file), \
             patch.object(caddy_manager, "CADDY_TOKEN_CLEAR_FILE", self.clear_file):
            result = caddy_manager._load_cached_token()
        self.assertEqual(result, "")
        self.assertFalse(os.path.exists(self.cache_path))

    def test_fresh_token_is_returned(self):
        """A 1-day-fresh cache entry is returned to the caller."""
        self._write_payload("fresh-token", time.time() + 86400)
        with patch.object(caddy_manager, "CADDY_TOKEN_CACHE", self.cache_path), \
             patch.object(caddy_manager, "CADDY_TOKEN_FILE", self.token_file), \
             patch.object(caddy_manager, "CADDY_TOKEN_CLEAR_FILE", self.clear_file):
            result = caddy_manager._load_cached_token()
        self.assertEqual(result, "fresh-token")
        self.assertTrue(os.path.exists(self.cache_path))

    def test_legacy_unstructured_cache_is_ignored(self):
        """A cache file in the legacy (plain-token) format is ignored."""
        with open(self.cache_path, "w", encoding="utf-8") as handle:
            handle.write("legacy-plain-token")
        with patch.object(caddy_manager, "CADDY_TOKEN_CACHE", self.cache_path), \
             patch.object(caddy_manager, "CADDY_TOKEN_FILE", self.token_file), \
             patch.object(caddy_manager, "CADDY_TOKEN_CLEAR_FILE", self.clear_file):
            result = caddy_manager._load_cached_token()
        self.assertEqual(result, "")

    def test_missing_cache_file_returns_empty(self):
        """No cache file → empty token, no exception."""
        with patch.object(caddy_manager, "CADDY_TOKEN_CACHE", self.cache_path):
            result = caddy_manager._load_cached_token()
        self.assertEqual(result, "")

    def test_clear_cached_token_removes_file(self):
        """clear_cached_token() deletes the cache file and reports success."""
        self._write_payload("anything", time.time() + 86400)
        with patch.object(caddy_manager, "CADDY_TOKEN_CACHE", self.cache_path), \
             patch.object(caddy_manager, "CADDY_TOKEN_FILE", self.token_file), \
             patch.object(caddy_manager, "CADDY_TOKEN_CLEAR_FILE", self.clear_file):
            result = caddy_manager.clear_cached_token()
        self.assertTrue(result)
        self.assertFalse(os.path.exists(self.cache_path))

    def test_clear_cached_token_returns_false_when_missing(self):
        """clear_cached_token() returns False when there's nothing to clear."""
        with patch.object(caddy_manager, "CADDY_TOKEN_CACHE", self.cache_path):
            result = caddy_manager.clear_cached_token()
        self.assertFalse(result)

    def test_apply_caddyfile_writes_ttl_payload(self):
        """apply_caddyfile stores a JSON payload with expires_at on disk."""
        with patch.object(caddy_manager, "CADDY_CONFIG_DIR", self._tmpdir.name), \
             patch.object(caddy_manager, "CADDY_TOKEN_CACHE", self.cache_path), \
             patch.object(caddy_manager, "CADDY_TOKEN_FILE", self.token_file), \
             patch.object(caddy_manager, "CADDY_TOKEN_CLEAR_FILE", self.clear_file), \
             patch.object(caddy_manager, "caddy_disabled_mode", return_value=False), \
             patch.object(caddy_manager, "_last_caddy_reload_ts", 0.0), \
             patch.object(caddy_manager, "_last_caddy_content_hash", ""), \
             patch.object(
                 caddy_manager,
                 "validate_service_routes_do_not_hit_control_plane",
                 return_value=[],
             ), \
             patch("apps.deployments.services.caddy_manager.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stderr = ""
            with patch("time.time", return_value=1_000_000.0):
                result = caddy_manager.apply_caddyfile(
                    ":80 { reverse_proxy localhost:8090 }",
                    cloudflare_token="t" * 40,
                )

        self.assertTrue(result.get("ok"), msg=result)
        self.assertTrue(os.path.exists(self.cache_path))
        with open(self.cache_path, encoding="utf-8") as handle:
            payload = json.loads(handle.read())
        self.assertEqual(payload["token"], "t" * 40)
        self.assertEqual(
            payload["expires_at"],
            1_000_000.0 + caddy_manager.CADDY_TOKEN_CACHE_TTL_SECONDS,
        )
