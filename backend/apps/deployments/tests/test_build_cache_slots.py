# pylint: disable=invalid-name
"""Tests for build caching + fleet build slots (2026-09-16 slow builds).

* ``_registry_cache_settings``: registry-qualified images get
  ``cache_from`` AND ``BUILDKIT_INLINE_CACHE=1`` (without inline
  metadata ``cache_from`` is dead weight — every redeploy recompiles
  all layers once local BuildKit cache is pruned). Bare names get
  neither (docker.io insufficient_scope trap).
* ``fleet_build_lock`` slot keys: the historic single key is slot 0;
  extra slots honour ``max_concurrent_builds`` so multi-service
  deploys stop serializing on one global slot.
* Slot integration (Django cache): N holders coexist on N distinct
  keys; a stale slot-0 owner is stolen exactly like the legacy lock.
"""
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.deployments.services.pipeline.build import _registry_cache_settings
from apps.deployments.tasks.deploy.build_compose import (
    _build_lock_keys,
    _fleet_lock_owner_is_stale,
    _host_pressure_slots,
    _max_build_slots,
    fleet_build_lock,
)


class RegistryCacheSettingsTests(TestCase):
    def test_registry_qualified_gets_inline_cache(self):
        cache_from, args = _registry_cache_settings(
            "registry:5000/svc:abc1234", {"FOO": "bar"})
        self.assertEqual(cache_from, ["registry:5000/svc:abc1234"])
        self.assertEqual(args["BUILDKIT_INLINE_CACHE"], "1")
        self.assertEqual(args["FOO"], "bar")

    def test_bare_name_gets_no_cache(self):
        cache_from, args = _registry_cache_settings(
            "smsly/name:tag", {"FOO": "bar"})
        self.assertEqual(cache_from, [])
        self.assertNotIn("BUILDKIT_INLINE_CACHE", args)

    def test_explicit_inline_value_wins(self):
        _, args = _registry_cache_settings(
            "registry:5000/svc:abc1234", {"BUILDKIT_INLINE_CACHE": "0"})
        self.assertEqual(args["BUILDKIT_INLINE_CACHE"], "0")

    def test_none_args_ok(self):
        cache_from, args = _registry_cache_settings(
            "registry.example.com/svc:t", None)
        self.assertEqual(cache_from, ["registry.example.com/svc:t"])
        self.assertEqual(args["BUILDKIT_INLINE_CACHE"], "1")


class BuildSlotKeyTests(TestCase):
    def test_single_slot_is_legacy_key(self):
        self.assertEqual(_build_lock_keys(1), ["smsly_fleet_build_lock"])

    def test_extra_slots_suffixed(self):
        keys = _build_lock_keys(3)
        self.assertEqual(keys, [
            "smsly_fleet_build_lock",
            "smsly_fleet_build_lock:slot:1",
            "smsly_fleet_build_lock:slot:2",
        ])

    def test_max_slots_default_and_clamp(self):
        self.assertEqual(_max_build_slots(SimpleNamespace(max_concurrent_builds=5)), 5)
        self.assertEqual(_max_build_slots(SimpleNamespace(max_concurrent_builds=None)), 1)
        self.assertEqual(_max_build_slots(SimpleNamespace(max_concurrent_builds=0)), 1)
        self.assertEqual(_max_build_slots(SimpleNamespace(max_concurrent_builds=99)), 10)
        self.assertEqual(_max_build_slots(SimpleNamespace()), 1)


class HostPressureSlotsTests(TestCase):
    """_host_pressure_slots: elongate when calm, contract under load.

    This is the burn-out governor: parallel gcc/pip storms are what
    get a host throttled, so slots follow live pressure, not just the
    configured ceiling.
    """

    @patch("os.getloadavg", create=True, return_value=(4.0, 0, 0))
    @patch("os.cpu_count", create=True, return_value=8)
    def test_calm_keeps_configured(self, _mock_cpu, _mock_load):
        self.assertEqual(_host_pressure_slots(5), 5)

    @patch("os.getloadavg", create=True, return_value=(10.0, 0, 0))
    @patch("os.cpu_count", create=True, return_value=8)
    def test_moderate_contracts_to_two(self, _mock_cpu, _mock_load):
        self.assertEqual(_host_pressure_slots(5), 2)
        self.assertEqual(_host_pressure_slots(1), 1)

    @patch("os.getloadavg", create=True, return_value=(90.0, 0, 0))
    @patch("os.cpu_count", create=True, return_value=8)
    def test_severe_contracts_to_one(self, _mock_cpu, _mock_load):
        self.assertEqual(_host_pressure_slots(5), 1)

    @patch("os.getloadavg", create=True, side_effect=OSError("no proc"))
    def test_unreadable_fails_open(self, _mock_load):
        self.assertEqual(_host_pressure_slots(4), 4)


class FleetBuildSlotsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="slot-user", password="password123")
        self.provider = CloudProvider.objects.create(
            name="slot-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="slot-svc", owner=self.user, provider=self.provider)
        from apps.deployments.models.core import PlatformConfig
        config = PlatformConfig.load()
        config.max_concurrent_builds = 3
        config.save(update_fields=["max_concurrent_builds"])
        # Pin calm host pressure: on a loaded Linux box the governor
        # would (correctly) contract slots and the multi-holder tests
        # below would block instead of proving parallelism.
        _calm_load = patch("os.getloadavg", create=True,
                           return_value=(1.0, 0, 0))
        _calm_cpu = patch("os.cpu_count", create=True, return_value=8)
        _calm_load.start()
        _calm_cpu.start()
        self.addCleanup(_calm_load.stop)
        self.addCleanup(_calm_cpu.stop)

    def _deployment(self, status=Deployment.Status.BUILDING):
        return Deployment.objects.create(
            service=self.service, status=status, commit_hash="abc1234")

    def tearDown(self):
        # LocMemCache survives TestCase DB rollback — never leak slot
        # keys into other tests (a leaked live holder would serialize
        # or stall unrelated lock tests).
        from django.core.cache import cache
        cache.delete("smsly_fleet_build_lock")
        cache.delete("smsly_fleet_build_lock:heartbeat")
        for i in range(1, 10):
            cache.delete(f"smsly_fleet_build_lock:slot:{i}")
            cache.delete(f"smsly_fleet_build_lock:slot:{i}:heartbeat")

    def test_three_holders_coexist_on_distinct_slots(self):
        from django.core.cache import cache
        first = self._deployment()
        second = self._deployment()
        third = self._deployment()
        with fleet_build_lock(first):
            with fleet_build_lock(second):
                with fleet_build_lock(third):
                    held = {
                        cache.get("smsly_fleet_build_lock"),
                        cache.get("smsly_fleet_build_lock:slot:1"),
                        cache.get("smsly_fleet_build_lock:slot:2"),
                    }
                    self.assertEqual(
                        held, {str(first.id), str(second.id), str(third.id)})
        self.assertIsNone(cache.get("smsly_fleet_build_lock"))
        self.assertIsNone(cache.get("smsly_fleet_build_lock:slot:1"))
        self.assertIsNone(cache.get("smsly_fleet_build_lock:slot:2"))

    def test_stale_base_slot_stolen(self):
        from django.core.cache import cache
        stale = self._deployment(status=Deployment.Status.CANCELLED)
        current = self._deployment(status=Deployment.Status.BUILDING)
        cache.set("smsly_fleet_build_lock", str(stale.id), timeout=300)
        with fleet_build_lock(current):
            self.assertEqual(
                cache.get("smsly_fleet_build_lock"), str(current.id))
        self.assertIsNone(cache.get("smsly_fleet_build_lock"))

    def test_live_owner_blocks_only_its_slot(self):        # A live owner on slot 0 must not block a waiter when slot 1
        # is free — this is the exact single-slot serialization the
        # refactor removes.
        from django.core.cache import cache
        from django.utils import timezone
        import datetime
        holder = self._deployment(status=Deployment.Status.BUILDING)
        waiter = self._deployment(status=Deployment.Status.BUILDING)
        cache.set("smsly_fleet_build_lock", str(holder.id), timeout=300)
        cache.set(
            "smsly_fleet_build_lock:heartbeat",
            {"owner": str(holder.id), "timestamp": timezone.now().timestamp()},
            timeout=300,
        )
        Deployment.objects.filter(id=holder.id).update(
            updated_at=timezone.now() - datetime.timedelta(seconds=5))
        with fleet_build_lock(waiter):
            self.assertEqual(
                cache.get("smsly_fleet_build_lock:slot:1"), str(waiter.id))
            # Slot 0 untouched — the live holder keeps building.
            self.assertEqual(
                cache.get("smsly_fleet_build_lock"), str(holder.id))


class FleetLockOwnerStaleTests(TestCase):
    """_fleet_lock_owner_is_stale: liveness beats row status.

    Regression for 2026-09-22: a CANCELLED owner with a fresh heartbeat
    (worker still building — cancel is DB-status-only) was stolen,
    running two docker builds on the same daemon/tag until one's image
    vanished ("No such image"). A live worker must never be stolen from.
    """

    STALE_SECONDS = 600
    HB_KEY = "test-fleet-hb"

    def setUp(self):
        self.user = User.objects.create_user(
            username="stale-user", password="password123")
        self.provider = CloudProvider.objects.create(
            name="stale-provider",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name="stale-svc", owner=self.user, provider=self.provider)

    def tearDown(self):
        from django.core.cache import cache
        cache.delete(self.HB_KEY)

    def _deployment(self, status):
        return Deployment.objects.create(
            service=self.service, status=status, commit_hash="abc1234")

    def _heartbeat(self, owner_id, age_seconds):
        import time
        from django.core.cache import cache
        cache.set(
            self.HB_KEY,
            {"owner": str(owner_id), "timestamp": time.time() - age_seconds},
            timeout=900,
        )

    def test_fresh_heartbeat_cancelled_owner_is_not_stale(self):
        # THE incident: worker alive (fresh heartbeat) but row CANCELLED.
        owner = self._deployment(status=Deployment.Status.CANCELLED)
        self._heartbeat(owner.id, age_seconds=5)
        is_stale, reason = _fleet_lock_owner_is_stale(
            str(owner.id), self.HB_KEY, self.STALE_SECONDS)
        self.assertFalse(is_stale, reason)

    def test_fresh_heartbeat_building_owner_is_not_stale(self):
        owner = self._deployment(status=Deployment.Status.BUILDING)
        self._heartbeat(owner.id, age_seconds=5)
        is_stale, reason = _fleet_lock_owner_is_stale(
            str(owner.id), self.HB_KEY, self.STALE_SECONDS)
        self.assertFalse(is_stale, reason)

    def test_no_heartbeat_cancelled_owner_is_stale(self):
        # Preserves test_stale_base_slot_stolen: dead worker, terminal
        # row — still stolen.
        owner = self._deployment(status=Deployment.Status.CANCELLED)
        is_stale, reason = _fleet_lock_owner_is_stale(
            str(owner.id), self.HB_KEY, self.STALE_SECONDS)
        self.assertTrue(is_stale)
        self.assertIn("CANCELLED", reason)

    def test_stale_heartbeat_building_owner_is_stale(self):
        owner = self._deployment(status=Deployment.Status.BUILDING)
        self._heartbeat(owner.id, age_seconds=self.STALE_SECONDS + 60)
        is_stale, reason = _fleet_lock_owner_is_stale(
            str(owner.id), self.HB_KEY, self.STALE_SECONDS)
        self.assertTrue(is_stale)
        self.assertIn("stale", reason)

    def test_missing_owner_is_stale(self):
        import uuid
        is_stale, reason = _fleet_lock_owner_is_stale(
            str(uuid.uuid4()), self.HB_KEY, self.STALE_SECONDS)
        self.assertTrue(is_stale)
        self.assertIn("no longer exists", reason)

    def test_legacy_grace_period_without_heartbeat(self):
        import datetime
        from django.utils import timezone
        owner = self._deployment(status=Deployment.Status.BUILDING)
        Deployment.objects.filter(id=owner.id).update(
            updated_at=timezone.now() - datetime.timedelta(
                seconds=self.STALE_SECONDS + 60))
        is_stale, _ = _fleet_lock_owner_is_stale(
            str(owner.id), self.HB_KEY, self.STALE_SECONDS)
        self.assertTrue(is_stale)
