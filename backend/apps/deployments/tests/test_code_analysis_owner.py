# pylint: disable=invalid-name
"""Tests for SEC (Issue 52): code_analysis result owner check."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class CodeAnalysisOwnerTests(TestCase):
    """``result`` must 404 a task_id the caller didn't start."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="ca-owner", email="ca@example.com", password="pw"
        )
        self.other = User.objects.create_user(
            username="ca-other", email="other@example.com", password="pw"
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_unknown_task_id_is_404(self):
        """No cache entry => 404 regardless of caller."""
        cache.clear()
        response = self.client.get("/api/v1/cloud/code-analysis/result/no-such-task/")
        self.assertEqual(response.status_code, 404)

    def test_other_users_task_id_is_404(self):
        """A task_id owned by a different user is hidden from the caller."""
        cache.clear()
        cache.set("code_analysis_owner:other-task", self.other.id, timeout=60)
        response = self.client.get("/api/v1/cloud/code-analysis/result/other-task/")
        self.assertEqual(response.status_code, 404)

    def test_owner_can_poll_their_task_id(self):
        """The user that triggered the analysis is allowed through the gate."""
        cache.clear()
        cache.set("code_analysis_owner:my-task", self.user.id, timeout=60)
        with patch("celery.result.AsyncResult") as mock_async:
            result = MagicMock()
            result.state = "SUCCESS"
            result.result = {"summary": "ok"}
            mock_async.return_value = result
            response = self.client.get(
                "/api/v1/cloud/code-analysis/result/my-task/"
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "complete")

    def test_analyze_caches_task_id_owner(self):
        """The analyze action binds task_id -> user_id for later ownership checks."""
        from apps.cloud.models import CloudProvider
        from apps.deployments.models import Service

        provider = CloudProvider.objects.create(
            name="ca-prov", provider_type="LOCAL", is_active=True
        )
        service = Service.objects.create(
            name="ca-svc",
            repository_url="https://github.com/x/y",
            owner=self.user,
            provider=provider,
        )
        cache.clear()
        with patch("apps.cloud.views.code_analysis.analyze_service_code_task.delay") as mock_task:
            mock_task.return_value = MagicMock(id="test-task-id")
            response = self.client.post(
                "/api/v1/cloud/code-analysis/analyze/",
                {"service_id": str(service.id)},
                format="json",
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            cache.get("code_analysis_owner:test-task-id"),
            self.user.id,
        )
