# pylint: disable=invalid-name
"""Regression tests for Finding #107 (``_cancel_stale_in_progress_deployments``
service-row lock).

The helper runs from inside the deploy hot-path. Without a
``select_for_update`` on the owning ``Service`` row, a concurrent
``POST /services/{id}/deploy/`` can create a brand-new ``QUEUED``
deployment AFTER the stale-cleanup read but BEFORE its update runs.
That freshly-created ``QUEUED`` row then gets clobbered by the
canceller, leaving the user staring at a "Deployment already in
progress" message for a deploy that no longer exists.

The fix wraps the entire helper in ``transaction.atomic`` and
re-fetches the ``Service`` row with ``select_for_update()`` so the
sequence of reads and updates is serialised against any other
writer that holds the same service row.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.deployments.models import Service, Deployment
from apps.deployments import views as deployments_views


User = get_user_model()


class Finding107ServiceLockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="fix107-user", password="x",
        )
        self.service = Service.objects.create(
            name="fix107-svc", owner=self.user,
        )

    def test_helper_acquires_service_row_lock(self):
        """``_cancel_stale_in_progress_deployments`` must call
        ``select_for_update`` on the Service row inside
        ``transaction.atomic``."""
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
            deployments_views._cancel_stale_in_progress_deployments(self.service)

        self.assertGreaterEqual(lock_mock.call_count, 1)

    def test_helper_marks_stuck_building_as_failed(self):
        """A deployment stuck in BUILDING for > 15 min must be moved
        to FAILED with the diagnostic message."""
        old = Deployment.objects.create(
            service=self.service,
            commit_hash="a1b2c3d4e5",
            status=Deployment.Status.BUILDING,
        )
        Deployment.objects.filter(pk=old.pk).update(
            updated_at=timezone.now() - timedelta(minutes=30),
        )

        deployments_views._cancel_stale_in_progress_deployments(self.service)

        old.refresh_from_db()
        self.assertEqual(old.status, Deployment.Status.FAILED)
        self.assertIn("stuck in BUILDING", old.ai_diagnosis or "")

    def test_helper_does_not_cancel_newer_active_run(self):
        """A QUEUED deployment that is NEWER than the most recent
        ACTIVE deployment must be left alone."""
        active = Deployment.objects.create(
            service=self.service,
            commit_hash="aa" * 20,
            status=Deployment.Status.ACTIVE,
        )
        newer = Deployment.objects.create(
            service=self.service,
            commit_hash="bb" * 20,
            status=Deployment.Status.QUEUED,
        )
        Deployment.objects.filter(pk=active.pk).update(
            created_at=timezone.now() - timedelta(hours=1),
        )

        deployments_views._cancel_stale_in_progress_deployments(self.service)

        newer.refresh_from_db()
        self.assertEqual(newer.status, Deployment.Status.QUEUED)
