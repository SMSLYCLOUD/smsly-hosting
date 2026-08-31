"""Regression tests for the health-path + status-range hardening.

Bugs this suite pins down:

1. ``braid-reasoning-engine`` served /api/health while /health and /
   both 404'd. The Docker healthcheck passed (it tries a fallback list)
   but the Traefik label pointed at /health only — Traefik marked the
   backend DOWN and the domain returned "no available server".
   Fixed by: probe the running container for a path that actually
   answers within the acceptable status range and use THAT path for
   the promoted container's Traefik healthcheck label.

2. Health acceptance was 200-only in some probes. A health probe
   answers "is the process serving HTTP?" — 2xx/3xx (redirects, 204s)
   all qualify, matching Traefik's internal 200 <= code < 400 rule.
   Configurable via HEALTH_CHECK_STATUS_MIN / MAX.

3. nftables fallback: stock alpine ships neither iptables nor nft,
   so the host shim produced "sh: iptables: not found" for every
   egress rule and scoped bridges shipped with no host-level
   isolation. The shim now prefers smsly/iptables-shim, falls back to
   on-the-fly apk install, then to nft rule translation.
"""
from __future__ import annotations

import os
from unittest import mock

from django.test import SimpleTestCase, override_settings

from apps.cloud.adapters.local import (
    _health_paths,
    _health_status_range,
    _nft_fallback_command,
    _normalize_health_path,
)


class HealthFallbackPathsTests(SimpleTestCase):
    def test_default_list_covers_common_frameworks(self):
        paths = _health_paths("/health")
        self.assertIn("/", paths)
        self.assertIn("/health", paths)
        self.assertIn("/api/health", paths)      # Next.js / API gateway
        self.assertIn("/healthz", paths)         # k8s liveness
        self.assertIn("/ready", paths)           # k8s readiness
        self.assertIn("/up", paths)              # Rails 7.1+ uptime
        self.assertIn("/ping", paths)            # SPA liveness

    def test_primary_path_first(self):
        paths = _health_paths("/api/health")
        self.assertEqual(paths[0], "/api/health")

    def test_env_override(self):
        with mock.patch.dict(os.environ, {"DOCKER_HEALTHCHECK_FALLBACK_PATHS": "/,/custom-health"}):
            paths = _health_paths("/health")
            self.assertEqual(paths, ["/health", "/", "/custom-health"])

    def test_normalization_prepends_slash_and_strips_metachars(self):
        self.assertEqual(_normalize_health_path("health"), "/health")
        self.assertEqual(_normalize_health_path("/health;rm -rf"), "/healthrm-rf")


class HealthStatusRangeTests(SimpleTestCase):
    def test_default_matches_traefik_rule(self):
        # Traefik internally treats 200 <= code < 400 as healthy. If we
        # are stricter than Traefik we'd mark healthy what it marks DOWN
        # (or vice versa) — keep the defaults identical.
        lo, hi = _health_status_range()
        self.assertEqual((lo, hi), (200, 399))

    def test_configurable(self):
        env = {"HEALTH_CHECK_STATUS_MIN": "200", "HEALTH_CHECK_STATUS_MAX": "299"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(_health_status_range(), (200, 299))

    def test_max_cannot_go_below_min(self):
        env = {"HEALTH_CHECK_STATUS_MIN": "300", "HEALTH_CHECK_STATUS_MAX": "200"}
        with mock.patch.dict(os.environ, env):
            lo, hi = _health_status_range()
            self.assertGreaterEqual(hi, lo)

    def test_range_accepts_204_and_302(self):
        lo, hi = _health_status_range()
        self.assertTrue(lo <= 204 <= hi)  # No Content
        self.assertTrue(lo <= 302 <= hi)  # redirect to login


class NftFallbackTranslationTests(SimpleTestCase):
    """The iptables->nft translator used when the host has no iptables."""

    def test_cross_bridge_drop(self):
        cmd = ["iptables", "-I", "DOCKER-USER", "-i", "br-abc", "-o", "br-+", "-j", "DROP"]
        nft = _nft_fallback_command(cmd)
        self.assertIn("insert rule ip filter DOCKER-USER", nft)
        self.assertIn('iifname "br-abc"', nft)
        self.assertIn('oifname "br-*"', nft)  # iptables wildcard br-+ -> br-*
        self.assertIn("drop", nft)

    def test_same_bridge_return(self):
        cmd = ["iptables", "-I", "DOCKER-USER", "-i", "br-abc", "-o", "br-abc", "-j", "RETURN"]
        nft = _nft_fallback_command(cmd)
        self.assertIn("return", nft)
        self.assertIn('iifname "br-abc"', nft)
        self.assertIn('oifname "br-abc"', nft)

    def test_metadata_drop(self):
        cmd = ["iptables", "-I", "DOCKER-USER", "-i", "br-abc", "-d", "169.254.169.254/32", "-j", "DROP"]
        nft = _nft_fallback_command(cmd)
        self.assertIn("ip daddr 169.254.169.254/32", nft)
        self.assertIn("drop", nft)

    def test_dns_udp_return(self):
        cmd = ["iptables", "-I", "DOCKER-USER", "-i", "br-abc", "-p", "udp", "--dport", "53", "-j", "RETURN"]
        nft = _nft_fallback_command(cmd)
        self.assertIn("udp", nft)
        self.assertIn("th dport 53", nft)
        self.assertIn("return", nft)

    def test_established_return(self):
        cmd = ["iptables", "-I", "DOCKER-USER", "-i", "br-abc",
               "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "RETURN"]
        nft = _nft_fallback_command(cmd)
        self.assertIn("ct state { ESTABLISHED, RELATED }", nft)
        self.assertIn("return", nft)

    def test_comment_preserved(self):
        cmd = ["iptables", "-I", "DOCKER-USER", "-i", "br-abc", "-j", "DROP",
               "-m", "comment", "--comment", "smsly-egress-abc"]
        nft = _nft_fallback_command(cmd)
        self.assertIn('comment "smsly-egress-abc"', nft)

    def test_untranslatable_falls_back_to_raw(self):
        cmd = ["iptables", "-S", "DOCKER-USER"]
        nft = _nft_fallback_command(cmd)
        self.assertTrue(nft.startswith("nft "))
