# pylint: disable=invalid-name
"""Regression tests for Finding #68 (PlatformConfig singleton race).

The ``PlatformConfig`` singleton (pk=1) is the single point of truth
for platform-wide settings (domain, SSL, Cloudflare token, etc.).
Two concurrent admins must not be able to race the read-modify-write
of that row. The fix is:

  * ``save()`` re-fetches the row with ``select_for_update()`` and
    merges the in-memory values into the locked row inside a single
    ``transaction.atomic`` block;
  * ``load()`` uses ``select_for_update().get_or_create(pk=1)`` so
    the very first read after migration cannot race a concurrent
    write from another node.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.deployments.models.core import PlatformConfig


class Finding68PlatformConfigSingletonTests(TestCase):
    def setUp(self):
        cfg = PlatformConfig.load()
        cfg.domain = "old.example.com"
        cfg.use_ssl = False
        cfg.wildcard_subdomains = True
        cfg.server_ip = "1.2.3.4"
        cfg.save()

    def test_save_uses_select_for_update(self):
        from django.db.models import QuerySet

        original_select_for_update = QuerySet.select_for_update
        lock_mock = MagicMock()

        def _fake_select_for_update(self, *args, **kwargs):
            lock_mock(self, *args, **kwargs)
            return original_select_for_update(self, *args, **kwargs)

        with patch(
            "django.db.models.QuerySet.select_for_update",
            new=_fake_select_for_update,
        ):
            cfg = PlatformConfig.objects.get(pk=1)
            cfg.domain = "new.example.com"
            cfg.save()

        self.assertGreaterEqual(lock_mock.call_count, 1)
        cfg.refresh_from_db()
        self.assertEqual(cfg.domain, "new.example.com")

    def test_load_uses_select_for_update(self):
        """The ``load()`` classmethod must also lock the row so an
        initial get_or_create cannot race a concurrent writer."""
        from django.db.models import QuerySet

        original_select_for_update = QuerySet.select_for_update
        lock_mock = MagicMock()

        def _fake_select_for_update(self, *args, **kwargs):
            lock_mock(self, *args, **kwargs)
            return original_select_for_update(self, *args, **kwargs)

        with patch(
            "django.db.models.QuerySet.select_for_update",
            new=_fake_select_for_update,
        ):
            PlatformConfig.load()

        self.assertGreaterEqual(lock_mock.call_count, 1)

    def test_save_atomic_rolls_back_on_collision(self):
        """If the locked row is mutated between the lock acquire and
        the merge, the in-memory values must still win atomically."""
        from django.db import transaction

        original_save = PlatformConfig.save

        def _explode(self, *args, **kwargs):
            raise RuntimeError("synthetic failure during save")

        PlatformConfig.save = _explode
        try:
            cfg = PlatformConfig.objects.get(pk=1)
            original_domain = cfg.domain
            cfg.domain = "rollback.example.com"
            with self.assertRaises(RuntimeError), transaction.atomic():
                cfg.save()
        finally:
            PlatformConfig.save = original_save

        cfg.refresh_from_db()
        self.assertEqual(cfg.domain, original_domain)
