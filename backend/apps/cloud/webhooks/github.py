"""Github module."""
import hashlib
import hmac
import logging
import re

from apps.deployments.models import Deployment, Service
from apps.core.models.audit import WebhookDelivery
from apps.deployments.tasks.deployment.tasks_deploy import smart_deploy_task
from django.conf import settings

logger = logging.getLogger(__name__)


def _check_duplicate_delivery(delivery_id, event_type):
    """
    Idempotency guard for GitHub deliveries.

    Returns ``(delivery, should_process)``. When the delivery is new, a
    fresh row is created and the caller should process the event. If the
    delivery has already been processed successfully, the caller should
    skip it. If it previously failed, the row is reset to ``processed`` so
    the caller may retry.
    """
    if not delivery_id:
        return None, True
    delivery, created = WebhookDelivery.objects.get_or_create(
        delivery_id=delivery_id,
        defaults={
            'provider': 'github',
            'event_type': event_type or '',
            'status': 'processed',
        },
    )
    if created:
        return delivery, True
    if delivery.status == 'failed':
        delivery.status = 'processed'
        delivery.event_type = event_type or delivery.event_type
        delivery.save(update_fields=['status', 'event_type'])
        return delivery, True
    logger.info("Duplicate webhook delivery %s; ignoring", delivery_id)
    return delivery, False


class GitHubWebhookHandler:
    def verify_signature(self, request) -> bool:
        """
        Verify that the request came from GitHub.
        """
        # Read from PlatformConfig first (the view layer), then fall back
        # to settings.py.  Both must be checked so the view and handler
        # always agree on the secret.
        secret = getattr(settings, 'GITHUB_WEBHOOK_SECRET', '')
        try:
            from apps.deployments.models.core import PlatformConfig
            db_secret = PlatformConfig.load().get_webhook_secret('github')
            if db_secret:
                secret = db_secret
        except Exception:
            pass
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

    def handle_event(self, event_type: str, payload: dict, delivery_id: str = ''):
        if event_type == 'push':
            return self._handle_push(payload, delivery_id)
        if event_type == 'pull_request':
            return self._handle_pull_request(payload, delivery_id)
        if event_type == 'installation':
            return self._handle_installation(payload, delivery_id)
        if event_type == 'installation_repositories':
            return self._handle_installation_repositories(payload, delivery_id)
        return False

    def _handle_push(self, payload: dict, delivery_id: str = ''):
        repo_url = payload.get('repository', {}).get(
            'html_url')  # e.g. https://github.com/user/repo
        ref = payload.get('ref')  # refs/heads/main

        if not repo_url or not ref:
            return False

        branch = ref.replace('refs/heads/', '')
        commit_hash = payload.get('after')
        commit_message = payload.get('head_commit', {}).get('message', '')

        # Find services listening to this repo/branch — exact owner/repo match
        from ..services.repo_matcher import match_service_repo
        candidates = Service.objects.filter(
            branch=branch, deploy_type='GIT', is_preview=False,
        )
        services = [
            s for s in candidates
            if s.repository_url and match_service_repo(s.repository_url, repo_url)
        ]

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
                skip = getattr(service, 'can_auto_deploy', False)
                smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=provider_id,
                                       skip_review=skip)
                triggered_count += 1
            else:
                logger.warning(
                    f"Service {service.name} has no provider assigned, skipping webhook deploy")

        return triggered_count > 0

    def _handle_pull_request(self, payload: dict, delivery_id: str = ''):
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
        from ..services.repo_matcher import match_service_repo
        candidates = Service.objects.filter(
            branch=base_ref, deploy_type='GIT', is_preview=False,
        )
        parent_services = [
            s for s in candidates
            if s.repository_url and match_service_repo(s.repository_url, repo_url)
        ]
        if not parent_services:
            logger.info(
                f"No parent service found for PR #{pr_number} on {repo_url} (base: {base_ref})")
            return False

        triggered_count = 0

        for parent in parent_services:
            if action in ['opened', 'reopened', 'synchronize']:
                triggered_count += self._create_or_update_preview(
                    parent, int(pr_number) if pr_number is not None else 0, head_ref, head_sha  # type: ignore[arg-type]
                )
            elif action == 'closed':
                triggered_count += self._destroy_preview(parent, int(pr_number) if pr_number is not None else 0)  # type: ignore[arg-type]

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
            # For previews, we also respect the parent's auto deploy setting
            skip = getattr(preview_service.parent_service, 'can_auto_deploy', False) if preview_service.parent_service else False
            smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=provider_id,
                                   skip_review=skip)
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

    # ── GitHub App installation events ────────────────────────────────────────

    def _handle_installation(self, payload: dict, delivery_id: str = ''):
        """Handle GitHub App installation created/deleted/suspend/unsuspend events."""
        from django.utils import timezone as tz

        from apps.cloud.models.github_app import GitHubAppInstallation

        action = payload.get('action')
        installation = payload.get('installation', {})
        installation_id = installation.get('id')
        account = installation.get('account', {})
        repositories = payload.get('repositories', [])

        if not installation_id:
            return False

        if action == 'created':
            GitHubAppInstallation.objects.update_or_create(
                installation_id=installation_id,
                defaults={
                    'account_login': account.get('login', ''),
                    'account_id': account.get('id', 0),
                    'account_type': account.get('type', 'User'),
                    'account_avatar_url': account.get('avatar_url', ''),
                    'status': GitHubAppInstallation.Status.ACTIVE,
                    'repository_selection': installation.get('repository_selection', 'selected'),
                    'repositories': [
                        {'id': r['id'], 'name': r['full_name']}
                        for r in repositories
                    ],
                    'permissions': installation.get('permissions', {}),
                    'events': installation.get('events', []),
                    'suspended_at': None,
                    'deleted_at': None,
                },
            )
            logger.info(
                "GitHub App installed: installation_id=%s account=%s",
                installation_id, account.get('login'),
            )

        elif action == 'deleted':
            GitHubAppInstallation.objects.filter(
                installation_id=installation_id,
            ).update(status=GitHubAppInstallation.Status.DELETED, deleted_at=tz.now())
            logger.info("GitHub App uninstalled: installation_id=%s", installation_id)

        elif action == 'suspend':
            GitHubAppInstallation.objects.filter(
                installation_id=installation_id,
            ).update(status=GitHubAppInstallation.Status.SUSPENDED, suspended_at=tz.now())
            logger.info("GitHub App suspended: installation_id=%s", installation_id)

        elif action == 'unsuspend':
            GitHubAppInstallation.objects.filter(
                installation_id=installation_id,
            ).update(status=GitHubAppInstallation.Status.ACTIVE, suspended_at=None)
            logger.info("GitHub App unsuspended: installation_id=%s", installation_id)

        return True

    def _handle_installation_repositories(self, payload: dict, delivery_id: str = ''):
        """Handle repos added/removed from a GitHub App installation."""
        from apps.cloud.models.github_app import GitHubAppInstallation

        action = payload.get('action')
        installation_id = payload.get('installation', {}).get('id')
        repos_added = payload.get('repositories_added', [])
        repos_removed = payload.get('repositories_removed', [])

        if not installation_id:
            return False

        try:
            inst = GitHubAppInstallation.objects.select_for_update().get(installation_id=installation_id)
        except GitHubAppInstallation.DoesNotExist:
            logger.warning(
                "installation_repositories event for unknown installation %s",
                installation_id,
            )
            return False

        current = list(inst.repositories or [])
        existing_names = {r['name'] for r in current}

        for repo in repos_added:
            name = repo.get('full_name', '')
            if name and name not in existing_names:
                current.append({'id': repo.get('id', 0), 'name': name})

        removed_names = {r.get('full_name', '') for r in repos_removed}
        current = [r for r in current if r['name'] not in removed_names]

        inst.repositories = current
        inst.save(update_fields=['repositories', 'updated_at'])

        logger.info(
            "installation_repositories %s: installation_id=%s, added=%d, removed=%d",
            action, installation_id, len(repos_added), len(repos_removed),
        )
        return True
