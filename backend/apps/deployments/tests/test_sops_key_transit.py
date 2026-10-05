"""SOPS platform keypair transit (node decrypt provisioning)."""
from unittest.mock import patch

from django.test import TestCase

from apps.deployments.services.secrets_sops import store_platform_keypair

PUB = "age1ql5ks Ago public test recipient 0123456789abcdef"
PRIV = "AGE-SECRET-KEY-1testprivatekey0123456789abcdef0123456789ab"


class StorePlatformKeypairTests(TestCase):
    def test_rejects_bad_shapes(self):
        self.assertFalse(store_platform_keypair("", ""))
        self.assertFalse(store_platform_keypair("ssh-rsa AAAA", PRIV))
        self.assertFalse(store_platform_keypair(PUB, "password123"))
        self.assertFalse(store_platform_keypair("age1" + "x" * 200, PRIV))
        self.assertFalse(store_platform_keypair(PUB, "AGE-SECRET-KEY-" + "x" * 300))

    def test_stores_and_idempotent(self):
        self.assertTrue(store_platform_keypair(PUB, PRIV))
        from apps.deployments.models.core import PlatformConfig
        cfg = PlatformConfig.load()
        self.assertEqual(cfg.secrets_age_public_key, PUB)
        self.assertTrue(store_platform_keypair(PUB, PRIV) is False)

    def test_overwrites_stale_pair(self):
        from apps.deployments.models.core import PlatformConfig
        cfg = PlatformConfig.load()
        cfg.secrets_age_public_key = "age1stale00000000000000000000000000000000000000"
        cfg.save(update_fields=["secrets_age_public_key"])
        self.assertTrue(store_platform_keypair(PUB, PRIV))
        cfg.refresh_from_db()
        self.assertEqual(cfg.secrets_age_public_key, PUB)
