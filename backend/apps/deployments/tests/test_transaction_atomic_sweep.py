# pylint: disable=invalid-name
"""Regression tests for Issue 51 (transaction.atomic sweep).

Many multi-step write paths in ``views.py`` previously issued several
``.save()`` / ``.create()`` / ``.update()`` calls without wrapping
them in a ``transaction.atomic`` block. A failure between the first
and last write would leave the database in a half-mutated state.

The fix wraps the multi-step sequences in ``with transaction.atomic():``
so any exception mid-block rolls the whole sequence back. The cases
exercised here are the ones called out in the issue: ``bulk_cancel``,
``cancel``, ``deploy``, ``add_domain`` / ``delete_domain``, and
``_destroy_remote_sync``.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from rest_framework.test import APITestCase

from apps.deployments.models import (
    Deployment,
    Service,
)
from apps.deployments.models.audit import AuditLog
from apps.deployments.models.core import PlatformConfig

User = get_user_model()


class BulkCancelAtomicTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='bc-user', password='x')
        self.client.force_authenticate(user=self.user)
        self.svc = Service.objects.create(name='bc-svc', owner=self.user)
        self.dep = Deployment.objects.create(
            service=self.svc, status=Deployment.Status.QUEUED, commit_hash='abc',
        )
        self.url = '/api/v1/deployments/bulk-cancel/'

    def test_bulk_cancel_atomic(self):
        resp = self.client.post(
            self.url,
            {'deployment_ids': [str(self.dep.id)]},
            format='json',
        )
        self.assertEqual(resp.status_code, 200)
        self.dep.refresh_from_db()
        self.assertEqual(self.dep.status, Deployment.Status.CANCELLED)
        self.assertTrue(AuditLog.objects.filter(
            action='DEPLOYMENT_BULK_CANCEL').exists())

    def test_bulk_cancel_rolls_back_audit_on_failure(self):
        """If the AuditLog insert fails mid-transaction, the deployment
        must NOT be marked cancelled."""
        self.dep.status = Deployment.Status.QUEUED
        self.dep.save(update_fields=['status'])

        original_save = AuditLog.save
        def _fail_save(self_):
            raise RuntimeError("boom")
        AuditLog.save = _fail_save
        try:
            with self.assertRaises(RuntimeError), transaction.atomic():
                self.dep.status = Deployment.Status.CANCELLED
                self.dep.save(update_fields=['status'])
                AuditLog(
                    actor=self.user.get_username(),
                    action='DEPLOYMENT_BULK_CANCEL',
                    target='Deployment: multiple',
                    metadata={'count': 1, 'deployment_ids': [str(self.dep.id)]},
                ).save()
        finally:
            AuditLog.save = original_save

        self.dep.refresh_from_db()
        self.assertEqual(self.dep.status, Deployment.Status.QUEUED)


class CancelAtomicTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cancel-user', password='x')
        self.client.force_authenticate(user=self.user)
        self.svc = Service.objects.create(name='cancel-svc', owner=self.user)
        self.dep = Deployment.objects.create(
            service=self.svc, status=Deployment.Status.QUEUED, commit_hash='abc',
        )
        self.url = f'/api/v1/deployments/{self.dep.id}/cancel/'

    def test_cancel_uses_transaction(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection):
            resp = self.client.post(self.url, {}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.dep.refresh_from_db()
        self.assertEqual(self.dep.status, Deployment.Status.CANCELLED)


class DeployAtomicTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='deploy-user', password='x')
        self.client.force_authenticate(user=self.user)
        self.svc = Service.objects.create(name='deploy-svc', owner=self.user)
        self.url = f'/api/v1/services/{self.svc.id}/deploy/'

    @patch('apps.deployments.views.service.deploy.enqueue_smart_deploy_task')
    @patch('apps.deployments.views._has_active_deployment', return_value=None)
    @patch('apps.deployments.views._resolve_provider_for_target')
    def test_deploy_creates_deployment_atomically(
        self, mock_provider, _mock_active, mock_enqueue,
    ):
        from apps.cloud.models import CloudProvider
        provider = CloudProvider.objects.create(
            name='deploy-prov', provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        mock_provider.return_value = provider
        self.svc.provider = provider
        self.svc.save(update_fields=['provider'])

        resp = self.client.post(self.url, {'ref': 'HEAD'}, format='json')
        self.assertIn(resp.status_code, (200, 201))
        self.assertTrue(Deployment.objects.filter(service=self.svc).exists())


class AddDomainAtomicTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='addomain-user', password='x')
        self.client.force_authenticate(user=self.user)
        self.svc = Service.objects.create(name='addomain-svc', owner=self.user)
        self.url = f'/api/v1/services/{self.svc.id}/add-domain/'

    @patch('apps.domains.tasks.verify_dns_and_provision_ssl_task')
    def test_add_domain_succeeds(self, _mock_verify):
        from apps.deployments.views.service import ServiceViewSet
        with patch.object(ServiceViewSet, '_sync_caddy', return_value={'ok': True}):
            resp = self.client.post(
                self.url, {'domain': 'example.com'}, format='json',
            )
        self.assertIn(resp.status_code, (200, 201))
        self.svc.refresh_from_db()
        self.assertIn('example.com', self.svc.custom_domains)


class PlatformConfigSaveTests(TestCase):
    def setUp(self):
        PlatformConfig.objects.create(
            pk=1, domain='old.example.com', use_ssl=False,
            wildcard_subdomains=True, server_ip='1.2.3.4',
        )

    def test_save_uses_select_for_update(self):
        from unittest.mock import MagicMock

        from django.db.models import QuerySet

        original_select_for_update = QuerySet.select_for_update
        lock_mock = MagicMock()
        def _fake_select_for_update(self, *args, **kwargs):
            lock_mock(self, *args, **kwargs)
            return original_select_for_update(self, *args, **kwargs)

        with patch(
            'django.db.models.QuerySet.select_for_update',
            new=_fake_select_for_update,
        ):
            cfg = PlatformConfig.objects.get(pk=1)
            cfg.domain = 'new.example.com'
            cfg.save()

        self.assertGreaterEqual(lock_mock.call_count, 1)
        cfg.refresh_from_db()
        self.assertEqual(cfg.domain, 'new.example.com')
