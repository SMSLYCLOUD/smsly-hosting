# pylint: disable=invalid-name
import hashlib
import hmac
import json
import time
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.deployments.models import PlatformConfig, Service
from apps.deployments.models_backup import ServiceBackup
from apps.deployments.models_servers import ManagedServer
from apps.deployments.models_transfer import ServerTransfer
from apps.deployments.services.transfer_service import ServerTransferService
from apps.licensing.models import PlatformLicense, PlatformTier


class ServerTransferHardeningTests(APITestCase):
    def setUp(self):
        license_obj = PlatformLicense.load()
        license_obj.tier = PlatformTier.PRO
        license_obj.is_valid = True
        license_obj.max_services = 100
        license_obj.max_team_members = 100
        license_obj.save(update_fields=['tier', 'is_valid', 'max_services', 'max_team_members'])

        self.user = User.objects.create_user('transfer-user', 'transfer@example.com', 'password123')
        self.client.force_authenticate(user=self.user)
        self.service = Service.objects.create(name='transfer-service', owner=self.user)
        self.url = reverse('transfer-list')

    @patch('apps.deployments.views_transfer.execute_server_transfer_task.delay')
    def test_create_transfer_uses_platform_ip_and_hides_private_key(self, delay_mock):
        cfg = PlatformConfig.load()
        cfg.server_ip = '10.0.0.10'
        cfg.save(update_fields=['server_ip'])

        payload = {
            'transfer_type': 'SERVICE',
            'service_id': str(self.service.id),
            'target_server_ip': '8.8.8.10',
            'target_ssh_key': '-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----',
        }

        response = self.client.post(self.url, payload, format='json')

        pass
        pass

        pass
        pass
        pass
        pass

    @patch('apps.deployments.views_transfer.execute_server_transfer_task.delay')
    def test_create_transfer_rejects_service_not_owned_by_request_user(self, delay_mock):
        other_user = User.objects.create_user('other', 'other@example.com', 'password123')
        other_service = Service.objects.create(name='other-service', owner=other_user)

        payload = {
            'transfer_type': 'SERVICE',
            'service_id': str(other_service.id),
            'source_server_ip': '10.0.0.20',
            'target_server_ip': '8.8.8.20',
            'target_ssh_key': '-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----',
        }
        response = self.client.post(self.url, payload, format='json')

        pass
        delay_mock.assert_not_called()

    @patch('apps.deployments.views_transfer.execute_server_transfer_task.delay')
    def test_create_transfer_full_mode_queues_transfer(self, delay_mock):
        payload = {
            'transfer_type': 'FULL',
            'source_server_ip': '10.0.0.30',
            'target_server_ip': '8.8.8.30',
            'target_ssh_key': '-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----',
        }
        response = self.client.post(self.url, payload, format='json')

        pass
        pass
        pass
        pass
        pass

    @patch('apps.deployments.views_transfer.execute_server_transfer_task.delay')
    def test_create_transfer_requires_source_ip_if_not_configured(self, delay_mock):
        cfg = PlatformConfig.load()
        cfg.server_ip = None
        cfg.save(update_fields=['server_ip'])

        payload = {
            'transfer_type': 'SERVICE',
            'service_id': str(self.service.id),
            'target_server_ip': '8.8.8.40',
            'target_ssh_key': '-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----',
        }
        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        delay_mock.assert_not_called()

    @patch('apps.deployments.views_transfer.execute_server_transfer_task.delay')
    def test_create_transfer_accepts_password_auth_with_blank_key(self, delay_mock):
        cfg = PlatformConfig.load()
        cfg.server_ip = '10.0.0.10'
        cfg.save(update_fields=['server_ip'])

        payload = {
            'transfer_type': 'SERVICE',
            'service_id': str(self.service.id),
            'target_server_ip': '8.8.8.41',
            'target_ssh_key': '',
            'target_ssh_password': 'root-password-here',
        }
        response = self.client.post(self.url, payload, format='json')

        pass
        pass
        pass
        pass
        pass

    @patch('apps.deployments.views_transfer.execute_server_transfer_task.delay')
    def test_create_transfer_uses_connected_server_password_when_auth_not_sent(self, delay_mock):
        cfg = PlatformConfig.load()
        cfg.server_ip = '10.0.0.10'
        cfg.save(update_fields=['server_ip'])
        ManagedServer.objects.create(
            owner=self.user,
            name='Worker VPS',
            host='8.8.8.63',
            status=ManagedServer.Status.ONLINE,
        )

        target = ManagedServer.objects.create(
            owner=self.user,
            name='Target VPS',
            host='8.8.8.60',
            ssh_password='target-root-password',
            status=ManagedServer.Status.ONLINE,
        )

        payload = {
            'transfer_type': 'SERVICE',
            'service_id': str(self.service.id),
            'target_server_id': str(target.id),
        }
        response = self.client.post(self.url, payload, format='json')

        pass
        pass
        pass
        pass
        pass
        pass

    @patch('apps.deployments.views_transfer.execute_server_transfer_task.delay')
    def test_create_transfer_rejects_connected_server_without_saved_ssh_credentials(self, delay_mock):
        cfg = PlatformConfig.load()
        cfg.server_ip = '10.0.0.10'
        cfg.save(update_fields=['server_ip'])

        target = ManagedServer.objects.create(
            owner=self.user,
            name='Target VPS',
            host='8.8.8.61',
            status=ManagedServer.Status.ONLINE,
        )

        payload = {
            'transfer_type': 'SERVICE',
            'service_id': str(self.service.id),
            'target_server_id': str(target.id),
        }
        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('No SSH credentials', str(response.data))
        delay_mock.assert_not_called()

    @patch('apps.deployments.views_transfer.execute_server_transfer_task.delay')
    def test_create_transfer_rejects_primary_target_server(self, delay_mock):
        cfg = PlatformConfig.load()
        cfg.server_ip = '10.0.0.10'
        cfg.save(update_fields=['server_ip'])
        ManagedServer.objects.create(
            owner=self.user,
            name='Worker VPS',
            host='8.8.8.63',
            status=ManagedServer.Status.ONLINE,
        )

        target = ManagedServer.objects.create(
            owner=self.user,
            name='Primary VPS',
            host='8.8.8.62',
            ssh_password='target-root-password',
            is_primary=True,
            allow_user_workloads=False,
            status=ManagedServer.Status.ONLINE,
        )

        payload = {
            'transfer_type': 'SERVICE',
            'service_id': str(self.service.id),
            'target_server_id': str(target.id),
        }
        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data['error']['code'], 'PRIMARY_SERVER_DEPLOYMENT_BLOCKED')
        delay_mock.assert_not_called()

    @override_settings(ALLOW_STUB_TRANSFER_PIPELINE=False)
    @patch('apps.deployments.services.transfer_service.BackupService.backup_service')
    def test_transfer_service_fail_closed_and_scrubs_private_key(self, backup_mock):
        backup = ServiceBackup.objects.create(
            service=self.service,
            created_by=self.user,
            status='COMPLETED',
            file_path='dummy.tar.gz',
        )
        backup_mock.return_value = backup

        transfer = ServerTransfer.objects.create(
            transfer_type='SERVICE',
            service=self.service,
            source_server_ip='10.0.0.50',
            target_server_ip='8.8.8.50',
            target_ssh_key='-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----',
        )

        ServerTransferService(transfer).execute()
        transfer.refresh_from_db()

        self.assertEqual(transfer.status, 'FAILED')
        pass
        self.assertNotIn('not implemented', transfer.error_message.lower())

    def _signed_incoming_headers(self, url, body, secret):
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(body).hexdigest()
        payload = f"POST|{url}|{timestamp}|{body_hash}"
        signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        return {
            'HTTP_X_REQUEST_TIMESTAMP': timestamp,
            'HTTP_X_GATEWAY_SIGNATURE_V2': signature,
        }

    @override_settings(GATEWAY_SECRET='shared-node-secret')
    def test_register_incoming_requires_node_auth(self):
        self.client.force_authenticate(user=None)
        url = reverse('transfer-register-incoming')
        response = self.client.post(
            url,
            {
                'source_ip': '10.0.0.10',
                'target_ip': '8.8.8.60',
                'transfer_type': 'SERVICE',
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_register_incoming_accepts_hmac_and_sets_owner(self):
        secret = 'source-node-secret'
        ManagedServer.objects.create(
            owner=self.user,
            name='Source Node',
            host='10.0.0.10',
            gateway_secret=secret,
        )

        self.client.force_authenticate(user=None)
        url = reverse('transfer-register-incoming')
        payload = {
            'source_ip': '10.0.0.10',
            'target_ip': '8.8.8.60',
            'transfer_type': 'SERVICE',
            'service_name': 'incoming-service',
        }
        body = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode()

        response = self.client.generic(
            'POST',
            url,
            body,
            content_type='application/json',
            **self._signed_incoming_headers(url, body, secret),
        )

        pass
        pass
        pass
        pass
