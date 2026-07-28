from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.deployments.models.core import Service, Deployment

User = get_user_model()

class ServiceLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='lctest', password='testpass')

    def test_service_creation(self):
        svc = Service.objects.create(name='lifecycle-svc', repository_url='https://github.com/test/repo', owner=self.user)
        self.assertEqual(str(svc), 'lifecycle-svc')
        self.assertEqual(svc.status, Service.Status.ACTIVE)

    def test_service_slug_generated(self):
        svc = Service.objects.create(name='Slug Test', repository_url='https://github.com/test/repo', owner=self.user)
        self.assertIsNotNone(svc.slug)

    def test_service_verification_token(self):
        svc = Service.objects.create(name='Token Test', repository_url='https://github.com/test/repo', owner=self.user)
        self.assertIsNotNone(svc.verification_token)

    def test_service_health_token(self):
        svc = Service.objects.create(name='Health Token', repository_url='https://github.com/test/repo', owner=self.user)
        self.assertIsNotNone(svc.health_webhook_token)

    def test_service_to_deletion_pending(self):
        svc = Service.objects.create(name='Delete Test', repository_url='https://github.com/test/repo', owner=self.user)
        svc.status = Service.Status.DELETION_PENDING
        svc.save(update_fields=['status'])
        svc.refresh_from_db()
        self.assertEqual(svc.status, Service.Status.DELETION_PENDING)

class DeploymentLifecycleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='lctest2', password='testpass')
        self.service = Service.objects.create(name='dep-lifecycle', repository_url='https://github.com/test/repo', owner=self.user)

    def test_deployment_queued(self):
        dep = Deployment.objects.create(service=self.service, status=Deployment.Status.QUEUED, commit_hash='abc1234')
        self.assertEqual(dep.status, Deployment.Status.QUEUED)

    def test_deployment_flow(self):
        dep = Deployment.objects.create(service=self.service, status=Deployment.Status.QUEUED, commit_hash='abc1234')
        dep.status = Deployment.Status.BUILDING
        dep.save(update_fields=['status'])
        dep.status = Deployment.Status.ACTIVE
        dep.save(update_fields=['status'])
        dep.refresh_from_db()
        self.assertEqual(dep.status, Deployment.Status.ACTIVE)

    def test_deployment_failure(self):
        dep = Deployment.objects.create(service=self.service, status=Deployment.Status.BUILDING, commit_hash='abc1234')
        dep.status = Deployment.Status.FAILED
        dep.save(update_fields=['status'])
        self.assertEqual(dep.status, Deployment.Status.FAILED)

    def test_deployment_active_deactivates_others(self):
        d1 = Deployment.objects.create(service=self.service, status=Deployment.Status.ACTIVE, commit_hash='aaa1111')
        d2 = Deployment.objects.create(service=self.service, status=Deployment.Status.ACTIVE, commit_hash='bbb2222')
        d1.refresh_from_db()
        self.assertEqual(d1.status, Deployment.Status.INACTIVE)
        self.assertEqual(d2.status, Deployment.Status.ACTIVE)
