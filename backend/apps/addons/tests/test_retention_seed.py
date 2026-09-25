"""Soft-delete retention for addon deprovision.

- Deprovision snapshots first (best-effort), retains the data volume,
  stamps deleted_at — never a silent data-loss event.
- Purge task removes retained volumes only past the retention window.
- Delete endpoint requires typed confirmation (addon name).
- Seed hook runs post-promotion, best-effort, stamps last run.
"""
import datetime
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from apps.addons import tasks
from apps.addons.services import addon_provisioner as _provisioner_mod
from apps.addons.tasks import crud as crud_tasks
from apps.deployments.tasks.deploy import seed as seed_mod
from apps.deployments.models import Addon, Service


def _user(username="retention"):
    return User.objects.create_user(username=username, password="x")


class DeprovisionSafetyTests(TestCase):
    def test_snapshot_before_destroy_and_volume_retained(self):
        user = _user()
        svc = Service.objects.create(name="retsvc", owner=user)
        addon = Addon.objects.create(
            service=svc, name="pg-ret", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="container",
            coolify_uuid="cid-123", connection_url="postgresql://u:p@h:5432/d")
        with mock.patch.object(
                _provisioner_mod.addon_provisioner, "create_backup",
                return_value="/backups/x.dump") as snap, \
             mock.patch.object(
                _provisioner_mod.addon_provisioner, "deprovision_dispatch",
                return_value=True) as deprov:
            crud_tasks.deprovision_addon_task.run(str(addon.id))
        snap.assert_called_once()
        _, kwargs = deprov.call_args
        self.assertTrue(kwargs.get("retain_volume"))
        addon.refresh_from_db()
        self.assertEqual(addon.status, Addon.Status.DELETED)
        self.assertIsNotNone(addon.deleted_at)
        self.assertEqual(addon.retired_volume, "smsly-addon-postgres-%s-data" % addon.id)

    def test_snapshot_failure_does_not_block(self):
        user = _user()
        svc = Service.objects.create(name="retsvc2", owner=user)
        addon = Addon.objects.create(
            service=svc, name="pg-ret2", addon_type="POSTGRES",
            status=Addon.Status.ACTIVE, provision_mode="container",
            coolify_uuid="cid-456", connection_url="postgresql://u:p@h:5432/d")
        with mock.patch.object(
                _provisioner_mod.addon_provisioner, "create_backup",
                side_effect=RuntimeError("no space")), \
             mock.patch.object(
                _provisioner_mod.addon_provisioner, "deprovision_dispatch",
                return_value=True):
            crud_tasks.deprovision_addon_task.run(str(addon.id))
        addon.refresh_from_db()
        self.assertEqual(addon.status, Addon.Status.DELETED)


class PurgeTests(TestCase):
    def _addon(self, svc, name, days_ago, vol):
        addon = Addon.objects.create(
            service=svc, name=name, addon_type="POSTGRES",
            status=Addon.Status.DELETED, provision_mode="container",
            connection_url="postgresql://u:p@h:5432/d")
        addon.deleted_at = timezone.now() - datetime.timedelta(days=days_ago)
        addon.retired_volume = vol
        addon.save()
        return addon

    def test_purges_only_past_window(self):
        user = _user()
        svc = Service.objects.create(name="purgesvc", owner=user)
        old = self._addon(svc, "old", 20, "vol-old-data")
        young = self._addon(svc, "young", 2, "vol-young-data")
        proc = mock.MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        with mock.patch("subprocess.run", return_value=proc) as run:
            crud_tasks.purge_retired_addon_volumes_task.run()
        old.refresh_from_db()
        young.refresh_from_db()
        self.assertEqual(old.retired_volume, "")
        self.assertEqual(young.retired_volume, "vol-young-data")
        cmds = [" ".join(c.args[0]) for c in run.call_args_list]
        self.assertTrue(any("vol-old-data" in c for c in cmds))
        self.assertFalse(any("vol-young-data" in c for c in cmds))

    def test_missing_volume_counts_as_purged(self):
        user = _user()
        svc = Service.objects.create(name="purgesvc2", owner=user)
        addon = self._addon(svc, "gone", 30, "vol-gone-data")
        proc = mock.MagicMock()
        proc.returncode = 1
        proc.stderr = "Error: No such volume: vol-gone-data"
        with mock.patch("subprocess.run", return_value=proc):
            crud_tasks.purge_retired_addon_volumes_task.run()
        addon.refresh_from_db()
        self.assertEqual(addon.retired_volume, "")


class SeedHookTests(TestCase):
    def test_seed_runs_and_stamps(self):
        user = _user()
        svc = Service.objects.create(
            name="seedsvc", owner=user, seed_command="python seed.py")
        dep = mock.MagicMock()
        dep.container_id = "ctr-1"
        proc = mock.MagicMock()
        proc.returncode = 0
        proc.stdout = "ok"
        proc.stderr = ""
        with mock.patch("subprocess.run", return_value=proc) as run:
            out = seed_mod.run_post_promote_seed(dep, svc)
        self.assertEqual(out["status"], "ok")
        svc.refresh_from_db()
        self.assertIsNotNone(svc.seed_last_run)
        self.assertIn("ctr-1", " ".join(run.call_args.args[0]))

    def test_seed_skipped_when_blank(self):
        user = _user()
        svc = Service.objects.create(name="seedsvc2", owner=user)
        out = seed_mod.run_post_promote_seed(mock.MagicMock(), svc)
        self.assertEqual(out["status"], "skipped")
