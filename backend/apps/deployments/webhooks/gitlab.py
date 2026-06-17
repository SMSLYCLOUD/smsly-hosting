"""GitLab webhook handler — push events trigger deployments."""
import hashlib
import hmac
import logging

from django.conf import settings
from apps.deployments.models import Service, Deployment
from apps.deployments.models_audit import WebhookDelivery
from apps.deployments.tasks import smart_deploy_task

logger = logging.getLogger(__name__)


class GitLabWebhookHandler:
    def verify_signature(self, request) -> bool:
        secret = settings.GITLAB_WEBHOOK_SECRET
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
        return {'message': f'Unhandled event: {event_type}', 'triggered': False}

    def _handle_push(self, payload):
        repo_url = (payload.get('project', {}) or {}).get('git_ssh_url', '') or \
                   (payload.get('project', {}) or {}).get('git_http_url', '')
        branch = (payload.get('ref') or '').replace('refs/heads/', '')
        if not repo_url or not branch:
            return {'message': 'Missing repo URL or branch', 'triggered': False}

        services = Service.objects.filter(
            repository_url__icontains=repo_url.split('/')[-1].replace('.git', ''),
            branch=branch,
            deploy_type='GIT',
            is_preview=False,
        )
        count = 0
        for service in services:
            deployment = Deployment.objects.create(
                service=service,
                status='QUEUED',
                commit_hash=(payload.get('checkout_sha') or payload.get('after', ''))[:40],
                commit_message=(payload.get('commits') or [{}])[-1].get('message', ''),
            )
            smart_deploy_task.delay(str(deployment.id))
            count += 1
        return count > 0

    def _handle_merge_request(self, payload):
        return False


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
