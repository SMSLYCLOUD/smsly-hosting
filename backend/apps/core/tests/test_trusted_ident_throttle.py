"""Tests for trusted-ident throttles behind Cloudflare/Caddy.

Root cause (live-verified): DRF SimpleRateThrottle.get_ident with
NUM_PROXIES=None returns the WHOLE X-Forwarded-For string. Behind
Cloudflare the XFF is a rotating CF edge IP — every request gets a
unique bucket and anon throttles NEVER fire.
"""
from django.test import TestCase, override_settings

from apps.core.rate_limiting import (
    AttestationVerifyRateThrottle,
    LoginRateThrottle,
    NodeTokenExchangeThrottle,
    PasswordResetRateThrottle,
    RegistrationRateThrottle,
    TrustedIdentMixin,
    TwoFactorLoginRateThrottle,
)


class _FakeRequest:
    def __init__(self, meta):
        self.META = meta


class TrustedIdentTests(TestCase):
    def _ident(self, **meta):
        t = TrustedIdentMixin()
        return t.get_ident(_FakeRequest(meta))

    def test_cf_connecting_ip_wins(self):
        # Cloudflare sets CF-Connecting-IP; Caddy forwards it. Even with
        # rotating XFF edge IPs, the real client is stable.
        self.assertEqual(
            self._ident(
                HTTP_CF_CONNECTING_IP="203.0.113.50",
                HTTP_X_FORWARDED_FOR="172.71.122.206",
                REMOTE_ADDR="172.18.0.29",
            ),
            "203.0.113.50",
        )

    def test_xff_alone_is_ignored(self):
        # THE BUG: DRF would return '172.71.122.206' (unique per request
        # behind CF). The mixin must ignore bare XFF.
        self.assertEqual(
            self._ident(
                HTTP_X_FORWARDED_FOR="172.71.122.206",
                REMOTE_ADDR="172.18.0.29",
            ),
            "172.18.0.29",
        )

    def test_trusted_proxy_x_real_ip(self):
        with override_settings(TRUSTED_PROXY_IPS=["172.18.0.29"]):
            self.assertEqual(
                self._ident(
                    HTTP_X_REAL_IP="203.0.113.50",
                    REMOTE_ADDR="172.18.0.29",
                ),
                "203.0.113.50",
            )

    def test_untrusted_proxy_x_real_ip_ignored(self):
        # A client cannot spoof X-Real-IP when the peer is not a
        # trusted proxy.
        with override_settings(TRUSTED_PROXY_IPS=["10.0.0.9"]):
            self.assertEqual(
                self._ident(
                    HTTP_X_REAL_IP="6.6.6.6",
                    REMOTE_ADDR="172.18.0.29",
                ),
                "172.18.0.29",
            )

    def test_direct_connection_uses_remote_addr(self):
        self.assertEqual(
            self._ident(REMOTE_ADDR="198.51.100.7"),
            "198.51.100.7",
        )

    def test_all_auth_throttles_use_mixin(self):
        for cls in (
            LoginRateThrottle,
            PasswordResetRateThrottle,
            RegistrationRateThrottle,
            NodeTokenExchangeThrottle,
            AttestationVerifyRateThrottle,
            TwoFactorLoginRateThrottle,
        ):
            self.assertTrue(
                issubclass(cls, TrustedIdentMixin),
                f"{cls.__name__} must use TrustedIdentMixin",
            )

    def test_login_throttle_actually_fires(self):
        """Full allow_request cycle with a stable ident: 10 allowed, 11th blocked."""
        from django.core.cache import cache

        class _View:
            throttle_classes = [LoginRateThrottle]

        view = _View()
        meta = {
            "HTTP_CF_CONNECTING_IP": "203.0.113.50",
            "REMOTE_ADDR": "172.18.0.29",
        }
        codes = []
        for _ in range(12):
            t = LoginRateThrottle()
            t.rate = None
            req = _FakeRequest(dict(meta))
            allowed = t.allow_request(req, view)
            codes.append(allowed)
        self.assertTrue(all(codes[:10]))
        self.assertFalse(any(codes[10:]))
