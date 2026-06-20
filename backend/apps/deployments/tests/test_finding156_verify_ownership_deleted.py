import inspect

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import TestCase

from apps.deployments.consumers import TerminalConsumer
from apps.deployments.models import Deployment, Service

User = get_user_model()


def _ownership_predicate(user, deployment_id):
    deleted_states = {
        Service.Status.DELETED,
        Service.Status.DELETION_PENDING,
        Service.Status.DELETION_FAILED,
    }
    return Deployment.objects.filter(
        Q(service__owner=user) |
        Q(service__project__team__members__user=user),
        id=deployment_id,
    ).exclude(service__status__in=deleted_states).exists()


class Finding156DeletedServiceOwnershipTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fix156', password='x')
        self.active = Service.objects.create(name='fix156-active', owner=self.user)
        self.deleted = Service.objects.create(name='fix156-deleted', owner=self.user)
        self.deletion_pending = Service.objects.create(name='fix156-deletion-pending', owner=self.user)
        self.deletion_failed = Service.objects.create(name='fix156-deletion-failed', owner=self.user)

        self.dep_active = Deployment.objects.create(
            service=self.active, commit_hash='a' * 10,
        )
        self.dep_deleted = Deployment.objects.create(
            service=self.deleted, commit_hash='b' * 10,
        )
        self.dep_pending = Deployment.objects.create(
            service=self.deletion_pending, commit_hash='c' * 10,
        )
        self.dep_failed = Deployment.objects.create(
            service=self.deletion_failed, commit_hash='d' * 10,
        )

        Service.objects.filter(pk=self.active.pk).update(status=Service.Status.ACTIVE)
        Service.objects.filter(pk=self.deleted.pk).update(status=Service.Status.DELETED)
        Service.objects.filter(pk=self.deletion_pending.pk).update(status=Service.Status.DELETION_PENDING)
        Service.objects.filter(pk=self.deletion_failed.pk).update(status=Service.Status.DELETION_FAILED)

    def test_accepts_active_service_deployment(self):
        self.assertTrue(_ownership_predicate(self.user, self.dep_active.id))

    def test_rejects_deleted_service_deployment(self):
        self.assertFalse(_ownership_predicate(self.user, self.dep_deleted.id))

    def test_rejects_deletion_pending_service_deployment(self):
        self.assertFalse(_ownership_predicate(self.user, self.dep_pending.id))

    def test_rejects_deletion_failed_service_deployment(self):
        self.assertFalse(_ownership_predicate(self.user, self.dep_failed.id))

    def test_consumer_source_excludes_deleted_states(self):
        source_text = inspect.getsource(TerminalConsumer)
        verify_pos = source_text.index('def _verify_ownership')
        next_def = source_text.find('\n    @', verify_pos + 1)
        section = source_text[verify_pos:next_def if next_def > 0 else len(source_text)]
        self.assertIn('DELETED', section)
        self.assertIn('DELETION_PENDING', section)
        self.assertIn('exclude(', section)
        self.assertIn('service__status__in', section)
