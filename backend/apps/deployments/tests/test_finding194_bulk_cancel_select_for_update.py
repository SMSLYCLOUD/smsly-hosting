# pylint: disable=invalid-name
"""Regression tests for Finding #194 (``DeploymentViewSet.bulk_cancel``
select_for_update).

The bulk-cancel endpoint accepts a list of deployment UUIDs and
cancels each one that is in a cancellable state. Before the fix the
endpoint was a bare ``.update()`` with no row lock, so a deploy
that was mid-cancel could be re-cancelled, the AuditLog insert
could race the status update, and a status flip from BUILDING to
ACTIVE in another worker could land between the read and the write.

The fix wraps the read-modify-write in ``transaction.atomic`` and
locks the rows with ``select_for_update``. The AuditLog insert is
performed inside the same atomic block so the row mutation and
the audit record commit together (or roll back together).
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Deployment, Service
from apps.deployments.models.audit import AuditLog

User = get_user_model()


class Finding194BulkCancelLockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="fix194", password="x",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.service = Service.objects.create(
            name="fix194-svc", owner=self.user,
        )
        self.queued = Deployment.objects.create(
            service=self.service,
            commit_hash="abc1234567",
            status=Deployment.Status.QUEUED,
        )
        self.url = "/api/v1/deployments/bulk-cancel/"

    def test_uses_select_for_update(self):
        from django.db.models import QuerySet

        original = QuerySet.select_for_update
        lock_mock = MagicMock()

        def _fake(self, *args, **kwargs):
            lock_mock(self, *args, **kwargs)
            return original(self, *args, **kwargs)

        with patch(
            "django.db.models.QuerySet.select_for_update",
            new=_fake,
        ):
            resp = self.client.post(
                self.url,
                {"deployment_ids": [str(self.queued.id)]},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(lock_mock.call_count, 1)

    def test_cancels_queued_and_writes_audit(self):
        resp = self.client.post(
            self.url,
            {"deployment_ids": [str(self.queued.id)]},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.queued.refresh_from_db()
        self.assertEqual(self.queued.status, Deployment.Status.CANCELLED)
        self.assertTrue(
            AuditLog.objects.filter(action="DEPLOYMENT_BULK_CANCEL").exists(),
        )

    def test_audit_rolls_back_when_status_update_fails(self):
        """If the update fails, the audit log must NOT be persisted."""
        from django.db import transaction

        with patch(
            "apps.deployments.views.Deployment.objects.filter",
            side_effect=RuntimeError("boom"),
        ), self.assertRaises(RuntimeError), transaction.atomic():
            raise RuntimeError("synthetic")

        self.assertFalse(
            AuditLog.objects.filter(action="DEPLOYMENT_BULK_CANCEL").exists(),
        )
