"""Bitbucket webhook handler — push and pull request events."""
import hashlib
import hmac
import logging
import re

from apps.deployments.models import Deployment, Service
from apps.core.models.audit import WebhookDelivery
from apps.deployments.tasks.deployment.tasks_deploy import smart_deploy_task
from django.conf import settings

logger = logging.getLogger(__name__)


class BitbucketWebhookHandler:
    def verify_signature(self, request) -> bool:
        secret = getattr(settings, 'BITBUCKET_WEBHOOK_SECRET', '')
        try:
            from apps.deployments.models.core import PlatformConfig
            db_secret = PlatformConfig.load().get_webhook_secret('bitbucket')
            if db_secret:
                secret = db_secret
        except Exception as exc:
            logger.debug("Failed to load Bitbucket webhook secret from PlatformConfig: %s", exc)
        if not secret:
            logger.warning("BITBUCKET_WEBHOOK_SECRET not set, rejecting webhook")
            return False
        signature = request.headers.get('X-Hub-Signature', '')
        if not signature:
            return False
        body = request.body
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(f'sha256={expected}', signature)

    def handle_event(self, event_type, data, delivery_id=None):
        if event_type.startswith('repo:push'):
            return self._handle_push(data)
        elif event_type.startswith('pullrequest:'):
            return self._handle_pull_request(data, event_type)
        return False

    def _handle_push(self, payload):
        changes = (payload.get('push', {}) or {}).get('changes', [])
        if not changes:
            return False

        change = changes[0]
        branch = (change.get('new', {}) or {}).get('name', '')
        if not branch:
            return False

        repo = payload.get('repository', {}) or {}
        from apps.deployments.services.repo_matcher import match_service_repo
        candidates = Service.objects.filter(
            branch=branch, deploy_type='GIT', is_preview=False,
        )
        services = [
            s for s in candidates
            if s.repository_url and match_service_repo(s.repository_url, repo.get('full_name', ''))
        ]

        count = 0
        for service in services:
            commit_hash = ''
            commit_message = ''
            target = change.get('new', {}) or {}
            commits_list = change.get('commits', [])
            if target.get('target', {}):
                commit_hash = (target['target'].get('hash') or '')[:40]
                commit_message = target['target'].get('message', '')
            elif commits_list:
                commit_hash = (commits_list[-1].get('hash') or '')[:40]
                commit_message = commits_list[-1].get('message', '')

            deployment = Deployment.objects.create(
                service=service, status='QUEUED',
                commit_hash=commit_hash, commit_message=commit_message,
            )
            provider_id = str(service.provider.id) if service.provider else None
            if provider_id:
                smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=provider_id, skip_review=True)
            else:
                logger.warning("No provider for service %s — webhook deploy not queued.", service.name)
            count += 1
        return count > 0

    def _handle_pull_request(self, payload, event_type=''):
        """Handle Pull Request events for preview environments."""
        pr = payload.get('pullrequest', {})
        repo = payload.get('repository', {})

        pr_number = pr.get('id')
        source_branch = (pr.get('source', {}) or {}).get('branch', {}).get('name', '')
        target_branch = (pr.get('destination', {}) or {}).get('branch', {}).get('name', '')
        commit_hash = (pr.get('source', {}) or {}).get('commit', {}).get('hash', '')

        repo_full_name = repo.get('full_name', '')

        if not all([repo_full_name, pr_number, source_branch, target_branch]):
            return False

        # Map event_type to action
        # pullrequest:created, pullrequest:updated, pullrequest:approved → create/update
        # pullrequest:fulfilled, pullrequest:rejected → destroy
        is_create = event_type in (
            'pullrequest:created', 'pullrequest:updated', 'pullrequest:approved',
        )
        is_destroy = event_type in (
            'pullrequest:fulfilled', 'pullrequest:rejected',
        )

        if not (is_create or is_destroy):
            return False

        # Find parent service on the target branch
        from apps.deployments.services.repo_matcher import match_service_repo
        candidates = Service.objects.filter(
            branch=target_branch, deploy_type='GIT', is_preview=False,
        )
        parent_services = [
            s for s in candidates
            if s.repository_url and match_service_repo(s.repository_url, repo_full_name)
        ]
        if not parent_services:
            return False

        triggered = 0
        for parent in parent_services:
            if is_create:
                triggered += self._create_or_update_preview(
                    parent, int(pr_number), source_branch, commit_hash
                )
            elif is_destroy:
                triggered += self._destroy_preview(parent, int(pr_number))

        return triggered > 0

    def _create_or_update_preview(self, parent, pr_number, branch, commit_hash):
        """Create or update a preview service for a Bitbucket pull request."""
        preview_name = f"{parent.name}-pr-{pr_number}"
        preview_slug = re.sub(r'[^a-z0-9-]+', '-', preview_name.lower()).strip('-')
        preview_slug = (preview_slug[:48]).strip('-') or f"pr-{pr_number}"
        base_domain = Service.default_public_base_domain()

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
                'public_domain': f"{preview_slug}.{base_domain}",
            },
        )

        if preview_service.branch != branch:
            preview_service.branch = branch
            preview_service.save()

        deployment = Deployment.objects.create(
            service=preview_service,
            commit_hash=commit_hash,
            commit_message=f"Preview deployment for PR #{pr_number}",
            status=Deployment.Status.QUEUED,
        )

        provider_id = str(preview_service.provider.id) if preview_service.provider else None
        if provider_id:
            skip = getattr(preview_service.parent_service, 'can_auto_deploy', False) if preview_service.parent_service else False
            smart_deploy_task.delay(
                deployment_id=str(deployment.id),
                provider_id=provider_id,
                skip_review=skip,
            )
            return 1
        return 0

    def _destroy_preview(self, parent, pr_number):
        """Destroy the preview service for a Bitbucket pull request."""
        try:
            preview_service = Service.objects.get(
                parent_service=parent, pr_number=pr_number, is_preview=True,
            )
            name = preview_service.name

            from apps.cloud.services.factory import get_cloud_adapter
            if preview_service.provider:
                adapter = get_cloud_adapter(preview_service.provider)
                if hasattr(adapter, 'docker_client') and adapter.docker_client:
                    try:
                        c = adapter.docker_client.containers.get(name)
                        c.remove(force=True)
                    except Exception as exc:
                        logger.debug("Failed to remove preview container %s: %s", name, exc)

            preview_service.delete()
            logger.info("Destroyed preview service %s", name)
            return 1
        except Service.DoesNotExist:
            return 0


def _check_duplicate_delivery(delivery_id, event_type):
    if not delivery_id:
        return None, True
    delivery, created = WebhookDelivery.objects.get_or_create(
        delivery_id=delivery_id,
        defaults={'provider': 'bitbucket', 'event_type': event_type or '', 'status': 'processed'},
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
