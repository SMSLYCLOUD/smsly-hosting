# pylint: disable=invalid-name
"""Regression tests for Finding #155 (DeploymentArtifact soft-delete).

Before the fix, ``rebuild_preview`` hard-deleted every
``DeploymentArtifact`` attached to the preview, losing the build log
of a failed prior run before the next build started. The fix adds an
``is_archived`` boolean and uses ``.update(is_archived=True)`` so the
audit trail is preserved across rebuilds.

These tests verify:
  * ``DeploymentArtifact._meta`` exposes an ``is_archived`` field.
  * ``rebuild_preview`` flips ``is_archived=True`` on the prior run's
    artifacts instead of removing them.
  * New artifacts created for the rebuild are visible (not archived).
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.deployments.models import Service
from apps.deployments.models.safedeploy import (
    DeploymentArtifact,
    PreviewEnvironment,
)
from apps.deployments.services.safedeploy.branch_preview_manager import (
    BranchPreviewManager,
)

User = get_user_model()


class Finding155DeploymentArtifactSoftDeleteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='fix155-user', password='x',
        )
        self.service = Service.objects.create(
            name='fix155-svc', owner=self.user,
        )
        self.preview = PreviewEnvironment.objects.create(
            service=self.service,
            branch_name='feature/x',
            commit_sha='a' * 7,
            created_by=self.user,
        )
        self.prior_log = DeploymentArtifact.objects.create(
            service=self.service,
            preview_environment=self.preview,
            artifact_type=DeploymentArtifact.ArtifactType.BUILD_LOG,
            content='prior build failed at step 3',
        )
        self.prior_risk = DeploymentArtifact.objects.create(
            service=self.service,
            preview_environment=self.preview,
            artifact_type=DeploymentArtifact.ArtifactType.RISK_REPORT,
            content='{"risk_level":"HIGH"}',
        )

    def test_is_archived_field_exists(self):
        """``DeploymentArtifact`` exposes an ``is_archived`` boolean."""
        field = DeploymentArtifact._meta.get_field('is_archived')
        self.assertIsNotNone(field)
        self.assertFalse(field.default)

    def test_rebuild_soft_archives_existing_artifacts(self):
        """``rebuild_preview`` flips ``is_archived=True`` rather than
        hard-deleting the prior run's artifacts."""
        manager = BranchPreviewManager()
        manager.rebuild_preview(self.preview, 'b' * 7)

        self.prior_log.refresh_from_db()
        self.prior_risk.refresh_from_db()
        self.assertTrue(self.prior_log.is_archived)
        self.assertTrue(self.prior_risk.is_archived)
        self.assertEqual(
            DeploymentArtifact._base_manager.filter(pk=self.prior_log.pk).count(),
            1,
        )
        self.assertEqual(
            DeploymentArtifact._base_manager.filter(pk=self.prior_risk.pk).count(),
            1,
        )

    def test_freshly_created_artifact_is_not_archived(self):
        """An artifact created for a new run is not flagged archived."""
        DeploymentArtifact.objects.create(
            service=self.service,
            preview_environment=self.preview,
            artifact_type=DeploymentArtifact.ArtifactType.MIGRATION_PLAN,
            content='new plan',
        )
        self.assertFalse(
            DeploymentArtifact.objects.filter(
                preview_environment=self.preview,
                artifact_type=DeploymentArtifact.ArtifactType.MIGRATION_PLAN,
            ).first().is_archived,
        )
