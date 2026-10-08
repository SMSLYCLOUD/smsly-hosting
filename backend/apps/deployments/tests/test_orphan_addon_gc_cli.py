"""Orphan addon GC must keep shared per-service CLI containers."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Addon, Service
from apps.deployments.tasks.infra import tasks_container_hygiene as hygiene

User = get_user_model()


class OrphanGcCliTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="orgc", password="x")
        self.service = Service.objects.create(name="orgcsvc", owner=self.user)

    def _addon(self, type_, name, status=Addon.Status.ACTIVE):
        return Addon.objects.create(
            service=self.service, name=name, addon_type=type_,
            status=status, provision_mode='container',
            connection_url=f"http://{name}:1/")

    def _run_gc(self, ps_lines):
        proc = mock.Mock()
        proc.stdout = "\n".join(ps_lines)
        removed = []

        def _fake_sh(cmd, timeout=None):
            if cmd[:2] == ['docker', 'ps']:
                return proc
            if cmd[:2] == ['docker', 'rm']:
                removed.append(cmd[-1])
                ok = mock.Mock()
                ok.returncode = 0
                return ok
            raise AssertionError(f"unexpected: {cmd}")

        with mock.patch.object(hygiene, '_sh', side_effect=_fake_sh):
            result = hygiene.orphan_addon_gc_task()
        return result, removed

    def test_shared_cli_container_kept_with_active_row(self):
        self._addon('OPENCODE', 'opencode-x')
        shared = f"smsly-addon-cli-{self.service.id}"
        result, removed = self._run_gc([f"{shared}\tUp 5 minutes"])
        self.assertEqual(removed, [])
        self.assertEqual(result["removed"], [])

    def test_shared_cli_container_kept_with_backend_missing_row(self):
        # 2026-10-08: hourly GC removed a live shared CLI container
        # whose only row was BACKEND_MISSING (awaiting recovery).
        self._addon('OPENCODE', 'opencode-x',
                    status=Addon.Status.BACKEND_MISSING)
        shared = f"smsly-addon-cli-{self.service.id}"
        result, removed = self._run_gc([f"{shared}\tUp 5 minutes"])
        self.assertEqual(removed, [])
        self.assertEqual(result["removed"], [])

    def test_shared_cli_container_removed_when_rows_deleted(self):
        self._addon('OPENCODE', 'opencode-x',
                    status=Addon.Status.DELETED)
        shared = f"smsly-addon-cli-{self.service.id}"
        result, removed = self._run_gc([f"{shared}\tUp 5 minutes"])
        self.assertEqual(removed, [shared])

    def test_shared_cli_container_removed_when_no_cli_rows(self):
        self._addon('REDIS', 'redis-x')
        shared = f"smsly-addon-cli-{self.service.id}"
        result, removed = self._run_gc([f"{shared}\tUp 5 minutes"])
        self.assertEqual(removed, [shared])

    def test_ordinary_orphan_still_removed(self):
        pg = self._addon('POSTGRES', 'pg-x')
        orphan = "smsly-addon-postgres-deadbeef-1234-5678-9abc-def012345678"
        result, removed = self._run_gc([
            f"smsly-addon-postgres-{pg.id}\tUp 2 hours",
            f"{orphan}\tExited (0) 3 days ago",
            "smsly-backend-1\tUp 2 hours",
        ])
        self.assertEqual(removed, [orphan])
