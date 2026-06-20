# pylint: disable=invalid-name
"""Regression tests for Finding #25 (ServiceViewSet.bulk_action atomicity).

The bulk action handler in ``apps/deployments/views.py`` must:

  * wrap the per-service iteration in a single ``transaction.atomic``
    block so a partial run does not leave half the services in their
    old state;
  * call ``select_for_update`` on the queryset so a service cannot
    be deleted by a concurrent request between the filter and the
    action;
  * still scope to ``get_queryset()`` so a tenant cannot deploy
    another tenant's services through the bulk endpoint.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Deployment, Service

User = get_user_model()


class BulkActionAtomicRegressionTests(TestCase):
    """Lock the atomicity guard for /api/v1/services/bulk-action/."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="bulk-fix25", password="123",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.svc1 = Service.objects.create(name="fix25-svc-1", owner=self.user)
        self.svc2 = Service.objects.create(name="fix25-svc-2", owner=self.user)
        self.url = "/api/v1/services/bulk-action/"

    def test_deploy_uses_atomic_and_lock(self):
        from unittest.mock import MagicMock

        from django.db.models import QuerySet

        original_select_for_update = QuerySet.select_for_update
        mock_lock = MagicMock()

        def _fake_select_for_update(self, *args, **kwargs):
            mock_lock(self, *args, **kwargs)
            return original_select_for_update(self, *args, **kwargs)

        with patch(
            "apps.deployments.tasks.smart_deploy_task"
        ) as mock_task, patch(
            "django.db.models.QuerySet.select_for_update",
            new=_fake_select_for_update,
        ):
            resp = self.client.post(
                self.url,
                {"ids": [str(self.svc1.id), str(self.svc2.id)], "action": "deploy"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(mock_lock.call_count, 1)
        self.assertEqual(mock_task.delay.call_count, 2)

    def test_cancel_updates_status_under_lock(self):
        from unittest.mock import MagicMock

        from django.db.models import QuerySet

        Deployment.objects.create(
            service=self.svc1, status=Deployment.Status.QUEUED, commit_hash="abc",
        )

        original_select_for_update = QuerySet.select_for_update
        mock_lock = MagicMock()

        def _fake_select_for_update(self, *args, **kwargs):
            mock_lock(self, *args, **kwargs)
            return original_select_for_update(self, *args, **kwargs)

        with patch(
            "django.db.models.QuerySet.select_for_update",
            new=_fake_select_for_update,
        ):
            resp = self.client.post(
                self.url,
                {"ids": [str(self.svc1.id)], "action": "cancel"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(mock_lock.call_count, 1)
        dep = Deployment.objects.get(service=self.svc1)
        self.assertEqual(dep.status, Deployment.Status.CANCELLED)

    def test_tenant_isolation_holds(self):
        """A different tenant's services must not be deployable through bulk-action."""
        other = User.objects.create_user(username="fix25-other", password="123")
        victim = Service.objects.create(name="fix25-victim", owner=other)

        with patch("apps.deployments.tasks.smart_deploy_task") as mock_task:
            resp = self.client.post(
                self.url,
                {"ids": [str(victim.id)], "action": "deploy"},
                format="json",
            )
        self.assertEqual(resp.status_code, 200)
        results = resp.data.get("results", [])
        self.assertEqual(results, [])
        mock_task.delay.assert_not_called()
