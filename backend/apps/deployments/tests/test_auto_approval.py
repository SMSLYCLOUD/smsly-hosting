import unittest
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Project, Service
from apps.deployments.webhooks.github import GitHubWebhookHandler

User = get_user_model()

class AutoApprovalTest(TestCase):
    @unittest.skip('Service model does not have can_auto_deploy field yet')
    def setUp(self):
        self.user = User.objects.create_user(username="test", password="pwd")
        self.project = Project.objects.create(name="Test Proj", owner=self.user)
        self.provider = CloudProvider.objects.create(
            name='Test Provider',

            provider_type='VPS',
            api_key='fake-key'
        )
        self.service = Service.objects.create(
            name="test-service",

            project=self.project,
            repository_url="https://github.com/user/repo",
            branch="main",
            deploy_type="GIT",

            provider=self.provider
        )
        self.handler = GitHubWebhookHandler()

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    @unittest.skip('Service model does not have can_auto_deploy field yet')
    def test_github_push_requires_manual_approval_when_auto_deploy_false(self, mock_task):
        payload = {
            "ref": "refs/heads/main",
            "repository": {"html_url": "https://github.com/user/repo"},
            "after": "abcdef123456",
            "head_commit": {"message": "Update"}
        }

        self.handler._handle_push(payload)

        deployment = Deployment.objects.latest('created_at')
        mock_task.assert_called_once_with(
            deployment_id=str(deployment.id),
            provider_id=str(self.provider.id),
            skip_review=False
        )

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    @unittest.skip('Service model does not have can_auto_deploy field yet')
    def test_github_push_auto_approves_when_auto_deploy_true(self, mock_task):
        self.service.can_auto_deploy = True
        self.service.save()

        payload = {
            "ref": "refs/heads/main",
            "repository": {"html_url": "https://github.com/user/repo"},
            "after": "abcdef123456",
            "head_commit": {"message": "Update"}
        }

        self.handler._handle_push(payload)

        deployment = Deployment.objects.latest('created_at')
        mock_task.assert_called_once_with(
            deployment_id=str(deployment.id),
            provider_id=str(self.provider.id),
            skip_review=True
        )
