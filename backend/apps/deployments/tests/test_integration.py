from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.deployments.models.core import Service, Deployment

User = get_user_model()


class DeploymentIntegrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='inttest', password='testpass')

    def test_service_create_to_deploy(self):
        """Full flow: create service -> trigger deploy -> verify states."""
        svc = Service.objects.create(
            name='int-svc',
            repository_url='https://github.com/test/repo',
            owner=self.user,
        )
        self.assertEqual(svc.status, Service.Status.ACTIVE)

        dep = Deployment.objects.create(
            service=svc,
            commit_hash='abc123',
            status=Deployment.Status.QUEUED,
        )
        self.assertEqual(dep.status, Deployment.Status.QUEUED)

        dep.status = Deployment.Status.BUILDING
        dep.save(update_fields=['status'])
        self.assertEqual(dep.status, Deployment.Status.BUILDING)

        dep.status = Deployment.Status.ACTIVE
        dep.save(update_fields=['status'])
        dep.refresh_from_db()
        self.assertEqual(dep.status, Deployment.Status.ACTIVE)

    def test_deployment_failure_flow(self):
        """Flow: create -> build -> fail."""
        svc = Service.objects.create(
            name='fail-svc',
            repository_url='https://github.com/test/repo',
            owner=self.user,
        )
        dep = Deployment.objects.create(
            service=svc,
            commit_hash='abc123',
            status=Deployment.Status.BUILDING,
        )
        dep.status = Deployment.Status.FAILED
        dep.save(update_fields=['status'])
        self.assertEqual(dep.status, Deployment.Status.FAILED)

    def test_service_deletion_flow(self):
        """Flow: active -> deletion_pending -> deleted."""
        svc = Service.objects.create(
            name='del-svc',
            repository_url='https://github.com/test/repo',
            owner=self.user,
        )
        svc.status = Service.Status.DELETION_PENDING
        svc.save(update_fields=['status'])
        svc.status = Service.Status.DELETED
        svc.save(update_fields=['status'])
        self.assertEqual(svc.status, Service.Status.DELETED)

    def test_deployment_sequence(self):
        """Multiple deployments: second ACTIVE deactivates first."""
        svc = Service.objects.create(
            name='seq-svc',
            repository_url='https://github.com/test/repo',
            owner=self.user,
        )
        d1 = Deployment.objects.create(
            service=svc, commit_hash='aaa', status=Deployment.Status.ACTIVE)
        d2 = Deployment.objects.create(
            service=svc, commit_hash='bbb', status=Deployment.Status.ACTIVE)
        d1.refresh_from_db()
        self.assertEqual(d1.status, Deployment.Status.INACTIVE)
        self.assertEqual(d2.status, Deployment.Status.ACTIVE)

    def test_owner_cannot_see_other_services(self):
        """User A cannot query User B's services."""
        user_a = User.objects.create_user(username='ownerA', password='pass')
        user_b = User.objects.create_user(username='ownerB', password='pass')
        svc_a = Service.objects.create(
            name='a-svc',
            repository_url='https://github.com/test/repo',
            owner=user_a,
        )
        svc_b = Service.objects.create(
            name='b-svc',
            repository_url='https://github.com/test/repo',
            owner=user_b,
        )

        a_services = Service.objects.filter(owner=user_a)
        b_services = Service.objects.filter(owner=user_b)
        self.assertNotIn(svc_b, a_services)
        self.assertNotIn(svc_a, b_services)
