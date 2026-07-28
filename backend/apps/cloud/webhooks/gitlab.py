"""GitLab webhook handler — push and merge request events."""
import hmac
import logging
import re

from apps.deployments.models import Deployment, Service
from apps.core.models.audit import WebhookDelivery
from apps.deployments.tasks.deployment.tasks_deploy import smart_deploy_task
from django.conf import settings

logger = logging.getLogger(__name__)


class GitLabWebhookHandler:
    def verify_signature(self, request) -> bool:
        secret = getattr(settings, 'GITLAB_WEBHOOK_SECRET', '')
        try:
            from apps.deployments.models.core import PlatformConfig
            db_secret = PlatformConfig.load().get_webhook_secret('gitlab')
            if db_secret:
                secret = db_secret
        except Exception as exc:
            logger.debug("Failed to load GitLab webhook secret from PlatformConfig: %s", exc)
        if not secret:
            logger.warning("GITLAB_WEBHOOK_SECRET not set, rejecting webhook")
            return False
        signature = request.headers.get('X-Gitlab-Token', '')
        if not signature:
            return False
        return hmac.compare_digest(signature, secret)

    def handle_event(self, event_type, data, delivery_id=None):
        if event_type == 'Push Hook':
            return self._handle_push(data)
        elif event_type == 'Merge Request Hook':
            return self._handle_merge_request(data)
        return False

    def _handle_push(self, payload):
        repo_url = (payload.get('project', {}) or {}).get('git_ssh_url', '') or \
                   (payload.get('project', {}) or {}).get('git_http_url', '')
        branch = (payload.get('ref') or '').replace('refs/heads/', '')
        if not repo_url or not branch:
            return {'message': 'Missing repo URL or branch', 'triggered': False}

        from ..services.repo_matcher import match_service_repo
        candidates = Service.objects.filter(
            branch=branch, deploy_type='GIT', is_preview=False,
        )
        services = [
            s for s in candidates
            if s.repository_url and match_service_repo(s.repository_url, repo_url)
        ]
        count = 0
        for service in services:
            deployment = Deployment.objects.create(
                service=service,
                status='QUEUED',
                commit_hash=(payload.get('checkout_sha') or payload.get('after', ''))[:40],
                commit_message=(payload.get('commits') or [{}])[-1].get('message', ''),
            )
            provider_id = str(service.provider.id) if service.provider else None
            if provider_id:
                skip = getattr(service, 'can_auto_deploy', False)
                smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=provider_id, skip_review=skip)
            else:
                logger.warning("No provider for service %s — webhook deploy not queued.", service.name)
            count += 1
        return count > 0

    def _handle_merge_request(self, payload):
        """Handle Merge Request events for preview environments."""
        attrs = payload.get('object_attributes', {})
        action = attrs.get('action')  # open, close, reopen, update, merge
        mr_iid = attrs.get('iid')
        source_branch = attrs.get('source_branch')
        target_branch = attrs.get('target_branch')
        last_commit = attrs.get('last_commit', {})
        sha = last_commit.get('id', '')

        project = payload.get('project', {})
        repo_url = project.get('git_http_url') or project.get('web_url') or \
                   project.get('git_ssh_url')

        if not all([repo_url, mr_iid, source_branch, target_branch, sha]):
            return False

        # Find the parent service deployed from the target branch
        from ..services.repo_matcher import match_service_repo
        candidates = Service.objects.filter(
            branch=target_branch, deploy_type='GIT', is_preview=False,
        )
        parent_services = [
            s for s in candidates
            if s.repository_url and match_service_repo(s.repository_url, repo_url)
        ]
        if not parent_services:
            return False

        triggered = 0
        for parent in parent_services:
            if action in ('open', 'reopen', 'update'):
                triggered += self._create_or_update_preview(
                    parent, int(mr_iid), source_branch, sha
                )
            elif action in ('close', 'merge'):
                triggered += self._destroy_preview(parent, int(mr_iid))

        return triggered > 0

    def _create_or_update_preview(self, parent, mr_number, branch, commit_hash):
        """Create or update a preview service for a GitLab merge request."""
        preview_name = f"{parent.name}-mr-{mr_number}"
        preview_slug = re.sub(r'[^a-z0-9-]+', '-', preview_name.lower()).strip('-')
        preview_slug = (preview_slug[:48]).strip('-') or f"mr-{mr_number}"
        base_domain = Service.default_public_base_domain()

        preview_service, created = Service.objects.get_or_create(
            name=preview_name,
            defaults={
                'parent_service': parent,
                'is_preview': True,
                'pr_number': mr_number,
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
            commit_message=f"Preview deployment for MR !{mr_number}",
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

    def _destroy_preview(self, parent, mr_number):
        """Destroy the preview service for a GitLab merge request."""
        try:
            preview_service = Service.objects.get(
                parent_service=parent, pr_number=mr_number, is_preview=True,
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
        defaults={'provider': 'gitlab', 'event_type': event_type or '', 'status': 'processed'},
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
