import inspect
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.safedeploy import MigrationValidation, PreviewEnvironment
from apps.deployments.services.safedeploy.branch_preview_manager import (
    BranchPreviewManager,
)

User = get_user_model()


class Finding135RebuildPreviewLockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fix135', password='x')
        self.service = Service.objects.create(name='fix135-svc', owner=self.user)
        self.preview = PreviewEnvironment.objects.create(
            service=self.service,
            branch_name='feature/fix135',
            commit_sha='a' * 7,
            status=PreviewEnvironment.Status.READY,
            error_message='previous failure',
        )

    def test_rebuild_preview_uses_select_for_update(self):
        from django.db.models import QuerySet

        original = QuerySet.select_for_update
        lock_mock = MagicMock()

        def _fake(self, *args, **kwargs):
            lock_mock(self, *args, **kwargs)
            return original(self, *args, **kwargs)

        mgr = BranchPreviewManager()
        with patch('django.db.models.QuerySet.select_for_update', new=_fake):
            mgr.rebuild_preview(self.preview, 'b' * 7)

        self.assertGreaterEqual(lock_mock.call_count, 1)

    def test_rebuild_preview_updates_state_and_purges_artifacts(self):
        MigrationValidation.objects.create(
            preview_environment=self.preview,
            status=MigrationValidation.Status.PASSED,
        )
        self.assertEqual(
            MigrationValidation.objects.filter(preview_environment=self.preview).count(),
            1,
        )

        mgr = BranchPreviewManager()
        returned = mgr.rebuild_preview(self.preview, 'c' * 7)

        self.preview.refresh_from_db()
        self.assertEqual(self.preview.commit_sha, 'c' * 7)
        self.assertEqual(self.preview.status, PreviewEnvironment.Status.BUILDING)
        self.assertEqual(self.preview.error_message, '')
        self.assertEqual(
            MigrationValidation.objects.filter(preview_environment=self.preview).count(),
            0,
        )
        self.assertEqual(returned.commit_sha, 'c' * 7)

    def test_rebuild_preview_source_uses_transaction_atomic(self):
        src = inspect.getsource(BranchPreviewManager.rebuild_preview)
        self.assertIn('transaction.atomic', src)
        self.assertIn('select_for_update', src)
