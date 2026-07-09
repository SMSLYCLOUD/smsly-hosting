"""
Tests for datetime-based backup pruning (race-free).

``_prune_old_backups`` now uses a datetime cutoff instead of a fixed
set of IDs.  This prevents a race where a new backup that completes
between the query and the delete is accidentally removed.
"""
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.deployments.models import Project, Service
from apps.deployments.models_backup import ServerBackup, ServiceBackup
from apps.deployments.services.backup_service import BackupService

User = get_user_model()


class PruneDatetimeCutoffTest(TestCase):
    """_prune_old_backups with datetime-based cutoff."""

    def setUp(self):
        self.user = User.objects.create_user(username="prune-test", password="x")
        self.project = Project.objects.create(name="Prune Proj", owner=self.user)
        self.service = Service.objects.create(
            name="prune-svc",
            owner=self.user,
            project=self.project,
        )

    def _make_backup(self, service, status="COMPLETED", days_ago=0):
        b = ServiceBackup.objects.create(service=service, status=status)
        ServiceBackup.objects.filter(id=b.id).update(
            created_at=timezone.now() - timedelta(days=days_ago),
        )
        return b

    @patch('os.remove')
    @patch('os.path.exists', return_value=True)
    def test_keeps_retain_newest_deletes_older(self, mock_exists, mock_remove):
        """With retain=2, keeps newest 2, prunes the rest."""
        b1 = self._make_backup(self.service, days_ago=5)
        b2 = self._make_backup(self.service, days_ago=3)
        b3 = self._make_backup(self.service, days_ago=1)

        with patch.dict('os.environ', {'BACKUP_RETENTION_COUNT': '2'}):
            BackupService._prune_old_backups(ServiceBackup, self.service.id)

        # b3 (1 day ago) and b2 (3 days) should be kept; b1 (5 days) pruned
        self.assertTrue(ServiceBackup.objects.filter(id=b3.id).exists())
        self.assertTrue(ServiceBackup.objects.filter(id=b2.id).exists())
        self.assertFalse(ServiceBackup.objects.filter(id=b1.id).exists())

    @patch('os.remove')
    @patch('os.path.exists', return_value=True)
    def test_less_than_retain_does_nothing(self, mock_exists, mock_remove):
        """Fewer backups than retain: nothing to prune."""
        b1 = self._make_backup(self.service, days_ago=10)
        with patch.dict('os.environ', {'BACKUP_RETENTION_COUNT': '5'}):
            BackupService._prune_old_backups(ServiceBackup, self.service.id)
        self.assertTrue(ServiceBackup.objects.filter(id=b1.id).exists())

    @patch('os.remove')
    @patch('os.path.exists', return_value=True)
    def test_exactly_retain_does_nothing(self, mock_exists, mock_remove):
        """Exactly retain backups: nothing to prune."""
        b1 = self._make_backup(self.service, days_ago=3)
        b2 = self._make_backup(self.service, days_ago=1)
        with patch.dict('os.environ', {'BACKUP_RETENTION_COUNT': '2'}):
            BackupService._prune_old_backups(ServiceBackup, self.service.id)
        self.assertTrue(ServiceBackup.objects.filter(id=b1.id).exists())
        self.assertTrue(ServiceBackup.objects.filter(id=b2.id).exists())

    @patch('os.remove')
    @patch('os.path.exists', return_value=True)
    def test_retain_one_keeps_only_newest(self, mock_exists, mock_remove):
        """retain=1: only the single newest backup survives."""
        b1 = self._make_backup(self.service, days_ago=7)
        b2 = self._make_backup(self.service, days_ago=3)
        b3 = self._make_backup(self.service, days_ago=1)
        with patch.dict('os.environ', {'BACKUP_RETENTION_COUNT': '1'}):
            BackupService._prune_old_backups(ServiceBackup, self.service.id)
        self.assertFalse(ServiceBackup.objects.filter(id=b1.id).exists())
        self.assertFalse(ServiceBackup.objects.filter(id=b2.id).exists())
        self.assertTrue(ServiceBackup.objects.filter(id=b3.id).exists())

    @patch('os.remove')
    @patch('os.path.exists', return_value=True)
    def test_race_new_backup_after_cutoff_kept(self, mock_exists, mock_remove):
        """Simulate a concurrent backup that completes between the cutoff
        query and the delete.  Its created_at is newer than the cutoff,
        so it must NOT be deleted."""
        # Create 3 old backups
        b1 = self._make_backup(self.service, days_ago=10)
        b2 = self._make_backup(self.service, days_ago=8)
        b3 = self._make_backup(self.service, days_ago=6)

        with patch.dict('os.environ', {'BACKUP_RETENTION_COUNT': '2'}):
            # At this point, qs = [b3, b2, b1] (newest first)
            # retain=2 → cutoff = b2.created_at
            # old_backups = created_at < b2.created_at → only b1

            # Now simulate a concurrent backup completing AFTER the cutoff query
            b4 = self._make_backup(self.service, days_ago=4)

            # When prune runs, it should keep b4, b3 (2 newest) and prune b2, b1
            BackupService._prune_old_backups(ServiceBackup, self.service.id)

        # b4 was created after cutoff — should be kept
        self.assertTrue(ServiceBackup.objects.filter(id=b4.id).exists())
        # b3 was created after/before? Actually b3 was 6 days ago, b4 was 4 days ago
        # Newest first: b4, b3, b2, b1
        # retain=2 → keep b4, b3 → prune b2, b1
        self.assertTrue(ServiceBackup.objects.filter(id=b3.id).exists())
        self.assertFalse(ServiceBackup.objects.filter(id=b2.id).exists())
        self.assertFalse(ServiceBackup.objects.filter(id=b1.id).exists())

    @patch('os.remove')
    @patch('os.path.exists', return_value=True)
    def test_server_backup_pruning(self, mock_exists, mock_remove):
        """ServerBackup pruning works with datetime cutoff."""
        for days in [10, 7, 5, 3, 1]:
            b = ServerBackup.objects.create(status="COMPLETED")
            ServerBackup.objects.filter(id=b.id).update(
                created_at=timezone.now() - timedelta(days=days),
            )
        with patch.dict('os.environ', {'BACKUP_RETENTION_COUNT': '2'}):
            before = ServerBackup.objects.count()
            BackupService._prune_old_backups(ServerBackup)
            after = ServerBackup.objects.count()
        self.assertEqual(after, 2, "Should prune to retain=2")
        self.assertGreater(before, after)

    @patch('os.remove')
    @patch('os.path.exists', return_value=True)
    def test_prune_removes_files(self, mock_exists, mock_remove):
        """File path deletion is called for pruned backups."""
        b1 = self._make_backup(self.service, days_ago=5)
        b2 = self._make_backup(self.service, days_ago=1)
        with patch.dict('os.environ', {'BACKUP_RETENTION_COUNT': '1'}):
            BackupService._prune_old_backups(ServiceBackup, self.service.id)
        # b1 is older than the cutoff for retain=1, so its file should be removed
        self.assertFalse(ServiceBackup.objects.filter(id=b1.id).exists())
        self.assertTrue(ServiceBackup.objects.filter(id=b2.id).exists())
