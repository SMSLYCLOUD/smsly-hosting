import hashlib
import hmac
import logging
from django.conf import settings
from apps.deployments.models import Service, Deployment
from apps.deployments.tasks import smart_deploy_task

logger = logging.getLogger(__name__)

class GitHubWebhookHandler:
    def verify_signature(self, request) -> bool:
        """
        Verify that the request came from GitHub.
        """
        secret = settings.GITHUB_WEBHOOK_SECRET
        if not secret:
            logger.warning("GITHUB_WEBHOOK_SECRET not set, rejecting webhook")
            return False  # SECURITY: Fail closed - never skip verification

        signature = request.headers.get('X-Hub-Signature-256')
        if not signature:
            return False

        body = request.body
        expected = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        return hmac.compare_digest(signature, expected)

    def handle_event(self, event_type: str, payload: dict):
        if event_type == 'push':
            return self._handle_push(payload)
        # Add 'pull_request' handler later for previews
        return False

    def _handle_push(self, payload: dict):
        repo_url = payload.get('repository', {}).get('html_url') # e.g. https://github.com/user/repo
        ref = payload.get('ref') # refs/heads/main

        if not repo_url or not ref:
            return False

        branch = ref.replace('refs/heads/', '')
        commit_hash = payload.get('after')
        commit_message = payload.get('head_commit', {}).get('message', '')

        # Find services listening to this repo/branch
        # Note: We might store repo_url as 'git@github.com:...' or 'https://...'
        # Ideally, we normalize URLs. For now, simple containment check.

        services = Service.objects.filter(
            repository_url__icontains=repo_url,
            branch=branch,
            deploy_type='GIT'
        )

        triggered_count = 0
        for service in services:
            logger.info(f"Triggering deployment for service {service.name} from GitHub Push")

            deployment = Deployment.objects.create(
                service=service,
                commit_hash=commit_hash,
                commit_message=commit_message,
                status=Deployment.Status.QUEUED
            )

            # Use the service's assigned provider
            provider_id = str(service.provider.id) if service.provider else None

            if provider_id:
                smart_deploy_task.delay(str(deployment.id), provider_id)
                triggered_count += 1
            else:
                logger.warning(f"Service {service.name} has no provider assigned, skipping webhook deploy")

        return triggered_count > 0
