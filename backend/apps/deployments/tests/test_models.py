# pylint: disable=invalid-name
"""Tests for Service and Deployment model logic."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models.core import Deployment, Service

User = get_user_model()


class ServiceModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='modeltest', password='testpass',
        )

    def _create_service(self, name='my-service', **kwargs):
        return Service.objects.create(
            name=name,
            repository_url='https://github.com/test/repo',
            owner=self.user,
            **kwargs,
        )

    def test_str(self):
        svc = self._create_service(name='my-service')
        self.assertEqual(str(svc), f'{svc.name} ({svc.slug})')

    def test_slug_generated(self):
        svc = self._create_service(name='My Service')
        self.assertIsNotNone(svc.slug)
        self.assertIn('my', svc.slug)

    def test_verification_token_generated(self):
        svc = self._create_service(name='token-test')
        self.assertTrue(svc.verification_token.startswith('smsly-verify-'))
        self.assertTrue(len(svc.verification_token) > 15)

    def test_health_webhook_token_generated(self):
        svc = self._create_service(name='health-test')
        self.assertIsNotNone(svc.health_webhook_token)
        self.assertTrue(len(svc.health_webhook_token) > 10)

    def test_status_default_is_active(self):
        svc = self._create_service(name='status-test')
        self.assertEqual(svc.status, Service.Status.ACTIVE)

    def test_slug_uniqueness(self):
        svc1 = self._create_service(name='dup-slug')
        svc2 = self._create_service(name='dup-slug')
        self.assertNotEqual(svc1.slug, svc2.slug)

    def test_public_domain_generated(self):
        svc = self._create_service(name='domain-test')
        self.assertIsNotNone(svc.public_domain)
        self.assertIn(svc.slug, svc.public_domain)

    def test_deletion_pending_status(self):
        svc = self._create_service(name='del-test')
        svc.status = Service.Status.DELETION_PENDING
        svc.save()
        self.assertEqual(svc.status, Service.Status.DELETION_PENDING)

    def test_repository_url_persisted(self):
        svc = self._create_service(name='repo-test')
        self.assertEqual(svc.repository_url, 'https://github.com/test/repo')


class DeploymentModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='deptest', password='testpass',
        )
        self.service = Service.objects.create(
            name='dep-svc',
            repository_url='https://github.com/test/repo',
            owner=self.user,
        )

    def test_deployment_str(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash='abc1234def5678',
            status=Deployment.Status.QUEUED,
        )
        expected = f'{self.service.name} - {dep.commit_hash[:7]} ({dep.status})'
        self.assertEqual(str(dep), expected)

    def test_deployment_default_status(self):
        dep = Deployment.objects.create(
            service=self.service,
            commit_hash='abc1234def5678',
        )
        self.assertEqual(dep.status, Deployment.Status.QUEUED)

    def test_deployment_active_deactivates_previous(self):
        d1 = Deployment.objects.create(
            service=self.service,
            commit_hash='aaa1111',
            status=Deployment.Status.ACTIVE,
        )
        d2 = Deployment.objects.create(
            service=self.service,
            commit_hash='bbb2222',
            status=Deployment.Status.ACTIVE,
        )
        d1.refresh_from_db()
        self.assertEqual(d1.status, Deployment.Status.INACTIVE)
        self.assertEqual(d2.status, Deployment.Status.ACTIVE)
