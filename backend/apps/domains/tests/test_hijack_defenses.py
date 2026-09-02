"""Regression tests for L7 domain-hijack defenses.

Three attacks pinned here:

1. TENANT CLAIMS THE PLATFORM DOMAIN — nothing stopped a tenant from
   adding `grid.smsly.cloud` (or any subdomain of it) as their custom
   domain; verification passed trivially (the platform domain points
   at the platform) and the tenant's container received the platform's
   hostname, sessions included. Guard: _is_platform_owned_domain +
   add_domain 403 + host_aliases serializer rejection.

2. DNS REBINDING / FAKE-IP VERIFICATION — verification used the
   system resolver (Docker embedded DNS → host), and libc fallbacks
   consulted /etc/hosts. An attacker's authoritative NS could answer
   the verification query with the platform IP and real user queries
   with their own server (or flip records right after). Guard:
   verification now resolves ONLY via independent public resolvers and
   requires a QUORUM to agree the domain points at the platform.

3. EDGE-IP FALSE MATCH — with the platform Cloudflare-proxied,
   resolve(platform_domain) returns CF edge IPs shared by every CF
   customer; an A-record match against those would let anyone's
   proxied domain verify. Guard: only the ORIGIN IP (server_ip) is an
   acceptable A-record match; CNAME chains are the recommended path.

4. STICKY VERIFICATION — verified=True was permanent; a domain
   repointed at an attacker kept its cert + routing forever. Guard:
   reverify_custom_domains_task re-quorums hourly and demotes failures,
   triggering a Caddy resync so routing dies with the demotion.
"""
from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

from apps.domains.verification import (
    PUBLIC_VERIFICATION_RESOLVERS,
    VERIFICATION_QUORUM,
    _resolver,
    verify_custom_domain_dns,
)


class _FakeAnswer:
    def __init__(self, address="", target=""):
        self.address = address
        self.target = target


class ResolverPinningTests(SimpleTestCase):
    """The resolver must NEVER use the system configuration."""

    def test_resolver_uses_public_nameservers_only(self):
        r = _resolver()
        self.assertEqual(
            sorted(r.nameservers),
            sorted(PUBLIC_VERIFICATION_RESOLVERS),
        )

    def test_quorum_is_strict_minority_rejecting(self):
        # Quorum of 2-of-3 means a single lying resolver cannot pass.
        self.assertGreaterEqual(VERIFICATION_QUORUM, 2)
        self.assertEqual(len(PUBLIC_VERIFICATION_RESOLVERS), 3)


class QuorumVerificationTests(SimpleTestCase):
    """Simulate resolver answers via the _resolve_rrset seam."""

    def _domain_obj(self, name="evil.example.com"):
        d = mock.MagicMock()
        d.domain_name = name
        d.service.public_domain = "svc-abc123.grid.smsly.cloud"
        return d

    def _config(self, server_ip="176.31.201.181", domain="grid.smsly.cloud"):
        cfg = mock.MagicMock()
        cfg.server_ip = server_ip
        cfg.domain = domain
        return cfg

    def _with_rrset(self, per_resolver: dict):
        """per_resolver: {'1.1.1.1': (chain, ips), ...} keyed by first NS.

        The seam receives nameservers=; we key the side effect on the
        tuple's first entry to simulate per-resolver divergence.
        """
        def fake_resolve(hostname, record_type, timeout=2.0, nameservers=None):
            ns = (nameservers or PUBLIC_VERIFICATION_RESOLVERS)[0]
            chain, ips = per_resolver.get(ns, ([], set()))
            if record_type == "CNAME":
                return [_FakeAnswer(target=t) for t in chain]
            # A / AAAA
            return [_FakeAnswer(address=a) for a in ips if ":" not in a or record_type == "AAAA"]

        return mock.patch(
            "apps.domains.verification._resolve_rrset",
            side_effect=fake_resolve,
        )

    def test_all_resolvers_agree_cname_verifies(self):
        good = (["svc-abc123.grid.smsly.cloud"], set())
        with self._with_rrset({ns: good for ns in PUBLIC_VERIFICATION_RESOLVERS}):
            result = verify_custom_domain_dns(self._domain_obj(), self._config())
        self.assertTrue(result.verified)
        self.assertIn("CNAME", result.matched_by)
        self.assertIn("3/3", result.matched_by)

    def test_all_resolvers_agree_origin_ip_verifies(self):
        good = ([], {"176.31.201.181"})
        with self._with_rrset({ns: good for ns in PUBLIC_VERIFICATION_RESOLVERS}):
            result = verify_custom_domain_dns(self._domain_obj(), self._config())
        self.assertTrue(result.verified)
        self.assertIn("IP 176.31.201.181", result.matched_by)

    def test_single_lying_resolver_cannot_verify(self):
        # 1.1.1.1 answers the platform IP; Google and Quad9 both see the
        # attacker's real server. OLD code: one lucky lookup verified.
        lying = ([], {"176.31.201.181"})
        truth = ([], {"203.0.113.9"})
        with self._with_rrset({
            "1.1.1.1": lying,
            "8.8.8.8": truth,
            "9.9.9.9": truth,
        }):
            result = verify_custom_domain_dns(self._domain_obj(), self._config())
        self.assertFalse(result.verified)
        self.assertIn("rebinding", result.error.lower())

    def test_single_agreeing_two_down_does_not_verify(self):
        # Only one resolver reachable at all (others NXDOMAIN): 1/3.
        agree = ([], {"176.31.201.181"})
        with self._with_rrset({
            "1.1.1.1": agree,
            "8.8.8.8": ([], set()),
            "9.9.9.9": ([], set()),
        }):
            result = verify_custom_domain_dns(self._domain_obj(), self._config())
        self.assertFalse(result.verified)

    def test_cloudflare_edge_ip_is_never_accepted(self):
        # The attacker CNAMEd their domain to another Cloudflare-proxied
        # site; all resolvers return CF edge IPs (104.16.x). OLD code
        # resolved the platform domain to the SAME edge IPs and matched.
        # NEW: only server_ip (origin) is acceptable for A matches.
        edge_only = ([], {"104.16.132.229", "172.67.166.238"})
        with self._with_rrset({ns: edge_only for ns in PUBLIC_VERIFICATION_RESOLVERS}):
            result = verify_custom_domain_dns(self._domain_obj(), self._config())
        self.assertFalse(result.verified)
