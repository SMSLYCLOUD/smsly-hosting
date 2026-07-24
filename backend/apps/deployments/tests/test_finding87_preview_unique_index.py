# pylint: disable=invalid-name
"""Regression tests for Finding #87 (PreviewEnvironment unique index).

Before the fix, two ``PreviewEnvironment`` rows could be created for
the same ``(service, branch_name, commit_sha)`` triple, leaving
duplicate preview records on a re-trigger. The fix adds a
``unique_together = (('service', 'branch_name', 'commit_sha'),)``
constraint at the model level and a matching migration so the
database enforces it.

These tests verify:
  * ``PreviewEnvironment._meta.unique_together`` contains the triple.
  * Inserting a duplicate triple raises ``IntegrityError``.
  * Two preview rows that differ by any single column are allowed.
"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.safedeploy import PreviewEnvironment

User = get_user_model()


class Finding87PreviewUniqueIndexTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='fix87-user', password='x',
        )
        self.service = Service.objects.create(
            name='fix87-svc', owner=self.user,
        )

    def test_unique_together_declares_service_branch_commit(self):
        """The model's Meta.unique_together contains the triple."""
        self.assertIn(
            ('service', 'branch_name', 'commit_sha'),
            PreviewEnvironment._meta.unique_together,
        )

    def test_duplicate_triple_raises_integrity_error(self):
        """Inserting a duplicate ``(service, branch, commit)`` row fails."""
        PreviewEnvironment.objects.create(
            service=self.service,
            branch_name='feature/x',
            commit_sha='a' * 7,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            PreviewEnvironment.objects.create(
                service=self.service,
                branch_name='feature/x',
                commit_sha='a' * 7,
            )

    def test_different_branch_or_commit_is_allowed(self):
        """Rows that differ by branch OR commit may coexist."""
        PreviewEnvironment.objects.create(
            service=self.service,
            branch_name='feature/a',
            commit_sha='a' * 7,
        )
        PreviewEnvironment.objects.create(
            service=self.service,
            branch_name='feature/b',
            commit_sha='a' * 7,
        )
        PreviewEnvironment.objects.create(
            service=self.service,
            branch_name='feature/a',
            commit_sha='b' * 7,
        )
        self.assertEqual(
            PreviewEnvironment.objects.filter(service=self.service).count(),
            3,
        )
