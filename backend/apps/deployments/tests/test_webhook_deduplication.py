# pylint: disable=invalid-name
"""
Tests for webhook delivery deduplication via WebhookDelivery.

Behavior contract:
  * New delivery_id  -> processed, deployment created.
  * Same delivery_id -> second request is short-circuited with 200.
  * delivery_id in ``failed`` state -> reprocessed (status reset).
"""
import hashlib
import hmac
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.deployments.models.audit import WebhookDelivery


class WebhookDeliveryDeduplicationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            'dedup_user', 'd@example.com', 'password123')
        self.provider = CloudProvider.objects.create(
            name='dedup-local', provider_type='LOCAL', is_active=True)
        self.service = Service.objects.create(
            name='dedup-svc',
            repository_url='https://github.com/test/repo',
            branch='main',
            owner=self.user,
            provider=self.provider,
        )

    def _signature(self, payload):
        secret = settings.GITHUB_WEBHOOK_SECRET.encode()
        return 'sha256=' + hmac.new(
            secret, payload.encode(), hashlib.sha256
        ).hexdigest()

    def _push_payload(self):
        return (
            '{"ref": "refs/heads/main", '
            '"after": "deadbeef", '
            '"repository": {"html_url": "https://github.com/test/repo"}, '
            '"head_commit": {"message": "test"}}'
        )

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_new_delivery_id_is_processed(self, delay_mock):
        payload = self._push_payload()
        sig = self._signature(payload)
        response = self.client.post(
            '/api/v1/webhooks/github/',
            data=payload,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=sig,
            HTTP_X_GITHUB_EVENT='push',
            HTTP_X_GITHUB_DELIVERY='delivery-001',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(WebhookDelivery.objects.filter(
            delivery_id='delivery-001').exists())

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_duplicate_delivery_id_is_ignored(self, delay_mock):
        payload = self._push_payload()
        sig = self._signature(payload)
        headers = {
            'HTTP_X_HUB_SIGNATURE_256': sig,
            'HTTP_X_GITHUB_EVENT': 'push',
            'HTTP_X_GITHUB_DELIVERY': 'delivery-002',
        }
        first = self.client.post(
            '/api/v1/webhooks/github/',
            data=payload,
            content_type='application/json',
            **headers,
        )
        self.assertEqual(first.status_code, 200)

        # Second identical request must be deduped; no new deployments.
        before = self.service.deployments.count()
        second = self.client.post(
            '/api/v1/webhooks/github/',
            data=payload,
            content_type='application/json',
            **headers,
        )
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data.get('status'), 'duplicate')
        self.assertEqual(self.service.deployments.count(), before)

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_failed_delivery_is_reprocessed(self, delay_mock):
        WebhookDelivery.objects.create(
            delivery_id='delivery-003',
            provider='github',
            event_type='push',
            status='failed',
        )
        payload = self._push_payload()
        sig = self._signature(payload)
        response = self.client.post(
            '/api/v1/webhooks/github/',
            data=payload,
            content_type='application/json',
            HTTP_X_HUB_SIGNATURE_256=sig,
            HTTP_X_GITHUB_EVENT='push',
            HTTP_X_GITHUB_DELIVERY='delivery-003',
        )
        self.assertEqual(response.status_code, 200)
        delivery = WebhookDelivery.objects.get(delivery_id='delivery-003')
        self.assertEqual(delivery.status, 'processed')
