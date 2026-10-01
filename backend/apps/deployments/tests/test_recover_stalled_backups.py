"""Stalled backup sweeper (no docker)."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.cloud.models.backup import ServerBackup
from apps.deployments.tasks.data.tasks_backup import (
    recover_stalled_backups_task,
)


class RecoverStalledBackupsTests(TestCase):
    def _row(self, status, age_hours):
        row = ServerBackup.objects.create(status='IN_PROGRESS')
        ServerBackup.objects.filter(id=row.id).update(
            status=status,
            created_at=timezone.now() - timedelta(hours=age_hours))
        row.refresh_from_db()
        return row

    def test_sweeps_old_in_progress(self):
        row = self._row('IN_PROGRESS', 7)
        result = recover_stalled_backups_task()
        self.assertEqual(result['server'], 1)
        row.refresh_from_db()
        self.assertEqual(row.status, 'FAILED')
        self.assertTrue(row.error_message)

    def test_sweeps_old_pending_faster(self):
        row = self._row('PENDING', 3)
        result = recover_stalled_backups_task()
        self.assertEqual(result['server'], 1)
        row.refresh_from_db()
        self.assertEqual(row.status, 'FAILED')

    def test_fresh_rows_untouched(self):
        row = self._row('IN_PROGRESS', 1)
        result = recover_stalled_backups_task()
        self.assertEqual(result['server'], 0)
        row.refresh_from_db()
        self.assertEqual(row.status, 'IN_PROGRESS')

    def test_completed_untouched(self):
        row = self._row('COMPLETED', 48)
        recover_stalled_backups_task()
        row.refresh_from_db()
        self.assertEqual(row.status, 'COMPLETED')
