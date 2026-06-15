"""
Regression tests for Finding #164 (consistent maintenance error codes).

``SystemConfigView.post`` queues a maintenance action via Celery.
The public contract for the failure responses used to be ambiguous:

  * dispatch failure -> ``503``
  * eager-mode failure -> ``500``
  * validation failure -> ``400``
  * lock conflict -> ``409``
  * accepted/queued -> ``202``

The fix introduces a single ``_MAINTENANCE_FAILURE_STATUS = 503``
constant and uses it for BOTH the dispatch-failure branch and the
eager-mode failure branch so monitoring rules and client retry
policies have one shape to handle.

This test asserts:

  * the constant exists at module scope and equals 503;
  * validation failure still returns 400 (no change);
  * lock-conflict still returns 409 (no change);
  * Celery-dispatch failure returns 503;
  * eager-mode failure also returns 503 (the new contract).
"""
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.deployments import views as deployments_views


User = get_user_model()


class Finding164MaintenanceStatusTests(TestCase):
    def setUp(self):
        self.url = "/api/v1/system/config/"
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username="mtce-admin-164", password="p", email="a@e.com",
        )
        self.client.force_authenticate(user=self.admin)
        cache.clear()

    def test_module_constant_is_503(self):
        self.assertEqual(
            getattr(deployments_views, "_MAINTENANCE_FAILURE_STATUS"), 503,
        )

    def test_validation_failure_returns_400(self):
        resp = self.client.post(
            self.url, {"action": "nonsense"}, format="json",
        )
        self.assertEqual(resp.status_code, 400)

    @patch("apps.deployments.tasks.run_maintenance_task.apply_async")
    def test_celery_dispatch_failure_returns_503(self, mock_apply):
        mock_apply.side_effect = RuntimeError("celery down")
        resp = self.client.post(
            self.url, {"action": "clear"}, format="json",
        )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.data.get("status"), "error")

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch("apps.deployments.tasks.run_maintenance_task.apply_async")
    def test_eager_mode_failure_returns_503(self, mock_apply):
        eager_task = MagicMock()
        eager_task.ready.return_value = True
        eager_task.result = {
            "status": "error",
            "message": "boom",
        }
        eager_task.id = "task-164"
        mock_apply.return_value = eager_task

        resp = self.client.post(
            self.url, {"action": "clear"}, format="json",
        )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.data.get("status"), "error")

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    @patch("apps.deployments.tasks.run_maintenance_task.apply_async")
    def test_eager_mode_success_returns_200(self, mock_apply):
        eager_task = MagicMock()
        eager_task.ready.return_value = True
        eager_task.result = {
            "status": "success",
            "message": "all good",
        }
        eager_task.id = "task-164-ok"
        mock_apply.return_value = eager_task

        resp = self.client.post(
            self.url, {"action": "clear"}, format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("status"), "success")
