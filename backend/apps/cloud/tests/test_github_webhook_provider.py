"""Webhook push must resolve a provider when the service has none.

A null service.provider is normal (creation flows leave it empty; every
other dispatch path resolves at send time). The old code skipped silently,
leaving a QUEUED row that never ran (2026-09-17 live incident).
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.cloud.webhooks.github import GitHubWebhookHandler
from apps.deployments.models import Deployment, Service


def _payload():
    return {
        "repository": {"html_url": "https://github.com/acme/web"},
        "ref": "refs/heads/main",
        "after": "d" * 40,
        "head_commit": {"message": "ship it"},
        "commits": [],
    }


class WebhookProviderResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="wh-user", password="x")
        self.service = Service.objects.create(
            name="wh-svc",
            owner=self.user,
            branch="main",
            deploy_type="GIT",
            repository_url="https://github.com/acme/web.git",
        )
        self.handler = GitHubWebhookHandler()

    def test_push_without_service_provider_still_enqueues(self):
        provider = CloudProvider.objects.create(
            name="local",
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.assertIsNone(self.service.provider)

        with patch("apps.cloud.webhooks.github.smart_deploy_task") as mock_task:
            result = self.handler._handle_push(_payload())

        self.assertTrue(result)
        mock_task.delay.assert_called_once()
        _, kwargs = mock_task.delay.call_args
        self.assertEqual(kwargs["provider_id"], str(provider.id))
        dep = Deployment.objects.filter(service=self.service).order_by("-created_at").first()
        self.assertIsNotNone(dep)
        self.assertEqual(dep.status, Deployment.Status.QUEUED)

    def test_push_with_no_provider_anywhere_leaves_note(self):
        with patch("apps.cloud.webhooks.github.smart_deploy_task") as mock_task:
            result = self.handler._handle_push(_payload())

        self.assertFalse(result)
        mock_task.delay.assert_not_called()
        dep = Deployment.objects.filter(service=self.service).order_by("-created_at").first()
        self.assertIsNotNone(dep)
        self.assertIn("No active provider", dep.build_logs or "")
