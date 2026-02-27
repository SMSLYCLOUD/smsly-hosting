# pylint: disable=invalid-name
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.deployments.models import PlatformConfig, Service
from apps.deployments.models_backup import ServiceBackup
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
            'target_server_ip': '203.0.113.10',
            'target_ssh_key': '-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----',
        }

        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('target_ssh_key', response.data)

        transfer = ServerTransfer.objects.get(id=response.data['id'])
        self.assertEqual(transfer.source_server_ip, '10.0.0.10')
        self.assertEqual(str(transfer.service_id), str(self.service.id))
        delay_mock.assert_called_once_with(str(transfer.id))

    @patch('apps.deployments.views_transfer.execute_server_transfer_task.delay')
    def test_create_transfer_rejects_service_not_owned_by_request_user(self, delay_mock):
        other_user = User.objects.create_user('other', 'other@example.com', 'password123')
        other_service = Service.objects.create(name='other-service', owner=other_user)

        payload = {
            'transfer_type': 'SERVICE',
            'service_id': str(other_service.id),
            'source_server_ip': '10.0.0.20',
            'target_server_ip': '203.0.113.20',
            'target_ssh_key': '-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----',
        }
        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        delay_mock.assert_not_called()

    @patch('apps.deployments.views_transfer.execute_server_transfer_task.delay')
    def test_create_transfer_full_mode_not_available(self, delay_mock):
        payload = {
            'transfer_type': 'FULL',
            'source_server_ip': '10.0.0.30',
            'target_server_ip': '203.0.113.30',
            'target_ssh_key': '-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----',
        }
        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_501_NOT_IMPLEMENTED)
        delay_mock.assert_not_called()

    @patch('apps.deployments.views_transfer.execute_server_transfer_task.delay')
    def test_create_transfer_requires_source_ip_if_not_configured(self, delay_mock):
        cfg = PlatformConfig.load()
        cfg.server_ip = None
        cfg.save(update_fields=['server_ip'])

        payload = {
            'transfer_type': 'SERVICE',
            'service_id': str(self.service.id),
            'target_server_ip': '203.0.113.40',
            'target_ssh_key': '-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----',
        }
        response = self.client.post(self.url, payload, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
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
            target_server_ip='203.0.113.50',
            target_ssh_key='-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----',
        )

        ServerTransferService(transfer).execute()
        transfer.refresh_from_db()

        self.assertEqual(transfer.status, 'FAILED')
        self.assertEqual(transfer.target_ssh_key, '')
        self.assertIn('not implemented', transfer.error_message.lower())
