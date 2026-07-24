# pylint: disable=invalid-name
"""
Tests that rollback/bulk-cancel operations write AuditLog entries.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from rest_framework import status as http_status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service
from apps.deployments.models.audit import AuditLog


class RollbackAuditLogTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='audit-roller',
            email='a@example.com',
            password='pwd',
        )
        self.client.force_authenticate(user=self.user)
        self.provider = CloudProvider.objects.create(
            name='audit-local',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name='audit-svc',
            owner=self.user,
            provider=self.provider,
            repository_url='https://github.com/x/y',
            branch='main',
        )
        self.good = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash='goodcommit',
        )

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_instant_rollback_writes_audit_log(self, _mock):
        url = f'/api/v1/services/{self.service.id}/instant-rollback/'
        response = self.client.post(
            url, data={'message': 'shipped a bug'}, format='json',
        )
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        entry = AuditLog.objects.filter(
            actor=self.user.get_username(),
            action='DEPLOYMENT_ROLLBACK_INSTANT',
        ).first()
        self.assertIsNotNone(entry)
        self.assertIn('rolled_back_to_commit', entry.metadata)
        self.assertEqual(entry.metadata['reason'], 'shipped a bug')

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_deployment_viewset_rollback_writes_audit_log(self, _mock):
        url = f'/api/v1/deployments/{self.good.id}/rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        entry = AuditLog.objects.filter(
            actor=self.user.get_username(),
            action='DEPLOYMENT_ROLLBACK',
        ).first()
        self.assertIsNotNone(entry)
        self.assertIn('target_deployment_id', entry.metadata)

    def test_bulk_cancel_writes_audit_log(self):
        d1 = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash='q1',
        )
        d2 = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.BUILDING,
            commit_hash='q2',
        )
        url = '/api/v1/deployments/bulk-cancel/'
        response = self.client.post(
            url, data={'deployment_ids': [str(d1.id), str(d2.id)]},
            format='json',
        )
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data['cancelled'], 2)
        entry = AuditLog.objects.filter(
            actor=self.user.get_username(),
            action='DEPLOYMENT_BULK_CANCEL',
        ).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.metadata['count'], 2)
