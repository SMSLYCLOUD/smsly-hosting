from unittest.mock import patch

import pytest

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.webhooks.github import GitHubWebhookHandler


@pytest.fixture
def webhook_handler():
    return GitHubWebhookHandler()

@pytest.fixture
def mock_service(db):
    provider = CloudProvider.objects.create(name="TestProvider", provider_type="LOCAL")
    service = Service.objects.create(
        name="test-service",
        repository_url="https://github.com/test/repo",
        branch="main",
        deploy_type="GIT",
        provider=provider,
        is_preview=False
    )
    return service

@patch('apps.deployments.tasks.smart_deploy_task.delay')
@pytest.mark.django_db
def test_handle_pull_request_opened(mock_deploy, webhook_handler, mock_service):
    payload = {
        "action": "opened",
        "number": 101,
        "repository": {"html_url": "https://github.com/test/repo"},
        "pull_request": {
            "head": {"ref": "feature-branch", "sha": "abc123"},
            "base": {"ref": "main"}
        }
    }

    result = webhook_handler._handle_pull_request(payload)

    assert result is True
    assert mock_deploy.called

    # Verify Preview Service Created
    preview = Service.objects.get(name="test-service-pr-101")
    assert preview.is_preview is True
    assert preview.branch == "feature-branch"
    assert preview.parent_service == mock_service

@pytest.mark.django_db
def test_handle_pull_request_closed(webhook_handler, mock_service):
    # Setup existing preview
    preview = Service.objects.create(
        name="test-service-pr-101",
        repository_url="https://github.com/test/repo",
        branch="feature-branch",
        deploy_type="GIT",
        is_preview=True,
        pr_number=101,
        parent_service=mock_service
    )

    payload = {
        "action": "closed",
        "number": 101,
        "repository": {"html_url": "https://github.com/test/repo"},
        "pull_request": {
            "head": {"ref": "feature-branch", "sha": "abc123"},
            "base": {"ref": "main"}
        }
    }

    result = webhook_handler._handle_pull_request(payload)

    assert result is True
    assert not Service.objects.filter(id=preview.id).exists()
