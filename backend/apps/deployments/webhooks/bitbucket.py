"""Bitbucket webhook handler — push events trigger deployments."""
import hashlib
import hmac
import json
import logging

from django.conf import settings
from apps.deployments.models import Service, Deployment
from apps.deployments.models_audit import WebhookDelivery
from apps.deployments.tasks_deploy import smart_deploy_task

logger = logging.getLogger(__name__)


class BitbucketWebhookHandler:
    def verify_signature(self, request) -> bool:
        secret = getattr(settings, 'BITBUCKET_WEBHOOK_SECRET', '')
        try:
            from apps.deployments.models_core import PlatformConfig
            db_secret = PlatformConfig.load().get_webhook_secret('bitbucket')
            if db_secret:
                secret = db_secret
        except Exception:
            pass
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
            return self._handle_pull_request(data)
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
        from ..services.repo_matcher import match_service_repo
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
                smart_deploy_task.delay(deployment_id=str(deployment.id), provider_id=provider_id)
            else:
                logger.warning("No provider for service %s — webhook deploy not queued.", service.name)
            count += 1
        return count > 0

    def _handle_pull_request(self, payload):
        return False


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
