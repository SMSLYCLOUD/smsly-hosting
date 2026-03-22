"""Github module."""
import hashlib
import hmac
import logging
import re
import uuid
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
        expected = 'sha256=' + \
            hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        return hmac.compare_digest(signature, expected)

    def handle_event(self, event_type: str, payload: dict):
        if event_type == 'push':
            return self._handle_push(payload)
        if event_type == 'pull_request':
            return self._handle_pull_request(payload)
        return False

    def _handle_push(self, payload: dict):
        repo_url = payload.get('repository', {}).get(
            'html_url')  # e.g. https://github.com/user/repo
        ref = payload.get('ref')  # refs/heads/main

        if not repo_url or not ref:
            return False

        branch = ref.replace('refs/heads/', '')
        commit_hash = payload.get('after')
        commit_message = payload.get('head_commit', {}).get('message', '')

        # Find services listening to this repo/branch
        services = Service.objects.filter(
            repository_url__icontains=repo_url,
            branch=branch,
            deploy_type='GIT',
            is_preview=False  # Do not trigger on preview services themselves
        )

        triggered_count = 0
        for service in services:
            logger.info(
                f"Triggering deployment for service {service.name} from GitHub Push")

            deployment = Deployment.objects.create(
                service=service,
                commit_hash=commit_hash,
                commit_message=commit_message,
                status=Deployment.Status.QUEUED
            )

            # Use the service's assigned provider
            provider_id = str(
                service.provider.id) if service.provider else None

            if provider_id:
                smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=provider_id,
                                       skip_review=True)
                triggered_count += 1
            else:
                logger.warning(
                    f"Service {service.name} has no provider assigned, skipping webhook deploy")

        return triggered_count > 0

    def _handle_pull_request(self, payload: dict):
        """
        Handle Pull Request events for Preview Environments.
        """
        action = payload.get('action')
        pr_number = payload.get('number')
        repo_url = payload.get('repository', {}).get('html_url')

        # Get the Pull Request info
        pr_info = payload.get('pull_request', {})
        head_ref = pr_info.get('head', {}).get('ref')  # The branch name of the PR
        head_sha = pr_info.get('head', {}).get('sha')
        base_ref = pr_info.get('base', {}).get('ref')  # The target branch (e.g., main)

        if not all([repo_url, pr_number, head_ref, base_ref]):
            return False

        # Find the Parent Service (the one deployed from the base branch)
        parent_services = Service.objects.filter(
            repository_url__icontains=repo_url,
            branch=base_ref,
            deploy_type='GIT',
            is_preview=False
        )

        if not parent_services.exists():
            logger.info(
                f"No parent service found for PR #{pr_number} on {repo_url} (base: {base_ref})")
            return False

        triggered_count = 0

        for parent in parent_services:
            if action in ['opened', 'reopened', 'synchronize']:
                triggered_count += self._create_or_update_preview(
                    parent, pr_number, head_ref, head_sha
                )
            elif action == 'closed':
                triggered_count += self._destroy_preview(parent, pr_number)

        return triggered_count > 0

    def _create_or_update_preview(self, parent: Service, pr_number: int,
                                  branch: str, commit_hash: str):
        """Create a new preview service or update an existing one."""
        preview_name = f"{parent.name}-pr-{pr_number}"
        preview_slug = re.sub(r'[^a-z0-9-]+', '-', preview_name.lower()).strip('-')
        preview_slug = (preview_slug[:48]).strip('-') or f"pr-{pr_number}"
        base_domain = Service.default_public_base_domain()

        # Check if preview already exists
        preview_service, created = Service.objects.get_or_create(
            name=preview_name,
            defaults={
                'parent_service': parent,
                'is_preview': True,
                'pr_number': pr_number,
                'repository_url': parent.repository_url,
                'provider': parent.provider,
                'deploy_type': 'GIT',
                'owner': parent.owner,
                'build_command': parent.build_command,
                'start_command': parent.start_command,
                'root_directory': parent.root_directory,
                'internal_port': parent.internal_port,
                'cpu_cores': parent.cpu_cores,
                'memory_mb': parent.memory_mb,
                # Unique domain for preview
                'public_domain': f"{preview_slug}.{base_domain}"
            }
        )

        # Update branch to the PR's branch
        if preview_service.branch != branch:
            preview_service.branch = branch
            preview_service.save()

        logger.info(
            f"{'Created' if created else 'Updated'} preview service {preview_name}")

        # Trigger Deployment
        deployment = Deployment.objects.create(
            service=preview_service,
            commit_hash=commit_hash,
            commit_message=f"Preview Deployment for PR #{pr_number}",
            status=Deployment.Status.QUEUED
        )

        provider_id = str(
            preview_service.provider.id) if preview_service.provider else None
        if provider_id:
            smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=provider_id,
                                   skip_review=True)
            return 1
        return 0

    def _destroy_preview(self, parent: Service, pr_number: int):
        """Destroy the preview service and its resources."""
        try:
            preview_service = Service.objects.get(
                parent_service=parent,
                pr_number=pr_number,
                is_preview=True
            )
            name = preview_service.name

            # Resource Cleanup
            # Import here to avoid circular dependency
            from apps.cloud.services.factory import get_cloud_adapter

            if preview_service.provider:
                adapter = get_cloud_adapter(preview_service.provider)
                # Attempt to delete by name (assuming standard naming convention in LocalAdapter)
                # Ideally, we should store container_id on Service or fetch last deployment
                # For LocalAdapter, deploy_container uses the service name as the container name
                try:
                    if hasattr(adapter, 'docker_client') and adapter.docker_client:
                        try:
                            c = adapter.docker_client.containers.get(name)
                            c.remove(force=True)
                            logger.info(f"Removed container {name}")
                        except BaseException:
                            pass
                except Exception as e:
                    logger.warning(f"Failed to cleanup container {name}: {e}")

            preview_service.delete()
            logger.info(f"Destroyed preview service {name}")
            return 1
        except Service.DoesNotExist:
            logger.warning(
                f"Preview service for PR #{pr_number} not found during cleanup")
            return 0
