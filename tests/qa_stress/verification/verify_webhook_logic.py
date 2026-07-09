# pylint: disable=protected-access
import os
from unittest.mock import patch

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.webhooks.github import GitHubWebhookHandler

# Setup Mock Data
provider = CloudProvider.objects.create(name="TestProvider", provider_type="LOCAL")
parent = Service.objects.create(
    name="production-app",
    repository_url="https://github.com/test/repo",
    branch="main",
    deploy_type="GIT",
    provider=provider
)

handler = GitHubWebhookHandler()

# Simulate PR Open Payload
payload = {
    "action": "opened",
    "number": 101,
    "repository": {
        "html_url": "https://github.com/test/repo"
    },
    "pull_request": {
        "head": {"ref": "feature-branch", "sha": "abc1234"},
        "base": {"ref": "main"}
    }
}

# Run Handler
print("--- Triggering PR Open ---")
with patch('apps.deployments.tasks.smart_deploy_task.delay') as mock_deploy:
    handler._handle_pull_request(payload)
    if mock_deploy.called:
        print("SUCCESS: Deployment triggered for PR Open")
    else:
        print("FAILURE: Deployment NOT triggered")

# Verify Service Created
preview = Service.objects.filter(name="production-app-pr-101", is_preview=True).first()
if preview:
    print(f"SUCCESS: Preview Service Created: {preview.name}")
    print(f"       Branch: {preview.branch}")
    print(f"       Parent: {preview.parent_service.name}")
else:
    print("FAILURE: Preview Service NOT created")

# Simulate PR Close Payload
close_payload = {
    "action": "closed",
    "number": 101,
    "repository": {
        "html_url": "https://github.com/test/repo"
    },
    "pull_request": {
        "head": {"ref": "feature-branch", "sha": "abc1234"},
        "base": {"ref": "main"}
    }
}

print("\n--- Triggering PR Close ---")
handler._handle_pull_request(close_payload)

# Verify Service Deleted
if not Service.objects.filter(name="production-app-pr-101").exists():
    print("SUCCESS: Preview Service Deleted")
else:
    print("FAILURE: Preview Service STILL EXISTS")

# Cleanup
parent.delete()
provider.delete()
