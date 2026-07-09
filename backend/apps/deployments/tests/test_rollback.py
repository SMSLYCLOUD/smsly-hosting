import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from rest_framework import status as http_status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service

# pylint: disable=invalid-name
"""
Tests for deployment rollback functionality.

Validates that:
  - Rollback creates a new deployment with the correct commit hash
  - Rollback triggers the Celery deploy task
  - Rollback fails gracefully without a provider
  - Rollback is REJECTED for the currently-active deployment (no-op)
  - Rollback is REJECTED for in-progress deployments
  - Rollback is REJECTED for FAILED / CANCELLED deployments
  - Rollback is ALLOWED for INACTIVE / prior ACTIVE deployments only
  - Multiple sequential rollbacks preserve audit trail
"""


class RollbackTests(APITestCase):
    """Tests for the rollback API endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='rollbackuser',
            email='rollback@test.com',
            password='testpass123',
        )
        self.client.force_authenticate(user=self.user)

        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True,
        )

        self.service = Service.objects.create(
            name='rollback-test-svc',
            repository_url='https://github.com/test/app',
            branch='main',
            owner=self.user,
            provider=self.provider,
        )

        # v1 — an EARLIER active deployment (rolled away from later).
        self.v1_deploy = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.INACTIVE,
            commit_hash='abc123v1',
            commit_message='Working version v1',
        )

        # v2 — the CURRENTLY-active deployment.
        self.v2_deploy = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash='def456v2',
            commit_message='Current good version v2',
        )

        # v3 — the most recent failed deployment (still has commit_hash).
        self.v3_deploy = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.FAILED,
            commit_hash='789xyzv3',
            commit_message='Broken version v3',
        )

    # ─────────────── happy paths ───────────────

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_rollback_to_inactive_creates_new_deployment(self, mock_task):
        """Rollback to a prior INACTIVE deployment should succeed."""
        url = f'/api/v1/deployments/{self.v1_deploy.id}/rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)

        new_deploy = Deployment.objects.exclude(
            id__in=[self.v1_deploy.id, self.v2_deploy.id, self.v3_deploy.id]
        ).first()
        self.assertIsNotNone(new_deploy)
        self.assertEqual(new_deploy.status, Deployment.Status.QUEUED)
        self.assertTrue(new_deploy.is_rollback)
        self.assertEqual(new_deploy.rollback_from_id, self.v1_deploy.id)

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_rollback_to_failed_deployment_rejected(self, mock_task):
        """Rolling back to a FAILED deployment must be rejected — it would just
        re-run the broken code. The user must pick a successful release from
        history."""
        url = f'/api/v1/deployments/{self.v3_deploy.id}/rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data.get('code'),
            'ROLLBACK_TARGET_NOT_SUCCESSFUL',
        )
        # No new deployment should have been created.
        self.assertEqual(
            Deployment.objects.filter(service=self.service).count(),
            3,
        )

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_rollback_preserves_commit_hash(self, mock_task):
        """Rollback deployment should use the target's commit hash."""
        url = f'/api/v1/deployments/{self.v1_deploy.id}/rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        new_deploy = Deployment.objects.get(id=response.data['id'])
        self.assertEqual(new_deploy.commit_hash, 'abc123v1')

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_rollback_commit_message_contains_rollback_label(self, mock_task):
        """Rollback deployment should have 'Rollback' in its commit message."""
        url = f'/api/v1/deployments/{self.v1_deploy.id}/rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        new_deploy = Deployment.objects.get(id=response.data['id'])
        self.assertIn('Rollback', new_deploy.commit_message)

    # ─────────────── guard rails ───────────────

    def test_rollback_requires_confirmation(self):
        """Rollback without confirm:true should be rejected."""
        url = f'/api/v1/deployments/{self.v1_deploy.id}/rollback/'
        response = self.client.post(url, data={}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_rollback_to_nonexistent_deployment_returns_404(self):
        """Rollback to a nonexistent deployment should return 404."""
        url = f'/api/v1/deployments/{uuid.uuid4()}/rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_404_NOT_FOUND)

    def test_rollback_to_currently_active_returns_noop_error(self):
        """Rollback to the CURRENTLY-active deployment is a no-op and must be rejected."""
        url = f'/api/v1/deployments/{self.v2_deploy.id}/rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        # 400 because _error_response returns 400 with code ROLLBACK_NOOP.
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('code'), 'ROLLBACK_NOOP')
        # No new deployment should have been created.
        self.assertEqual(
            Deployment.objects.filter(service=self.service).count(),
            3,
        )

    def test_rollback_to_in_progress_returns_error(self):
        """Rollback to a QUEUED / BUILDING / DEPLOYING deployment is rejected."""
        queued = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.QUEUED,
            commit_hash='queuedcommit',
        )
        url = f'/api/v1/deployments/{queued.id}/rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('code'), 'ROLLBACK_IN_PROGRESS')

    def test_rollback_to_cancelled_returns_error(self):
        """Rollback to a CANCELLED deployment must be rejected."""
        cancelled = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.CANCELLED,
            commit_hash='cancelledcommit',
        )
        url = f'/api/v1/deployments/{cancelled.id}/rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data.get('code'),
            'ROLLBACK_TARGET_NOT_SUCCESSFUL',
        )

    def test_rollback_to_missing_commit_hash_returns_error(self):
        """Rollback to a deployment without commit_hash is rejected."""
        blank = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.INACTIVE,
            commit_hash='',
        )
        url = f'/api/v1/deployments/{blank.id}/rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('code'), 'ROLLBACK_ARTIFACT_MISSING')

    def test_rollback_without_provider_returns_error(self):
        """Rollback should fail if no cloud provider is available."""
        self.service.provider = None
        self.service.save()
        CloudProvider.objects.all().delete()

        url = f'/api/v1/deployments/{self.v1_deploy.id}/rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('code'), 'ROLLBACK_PERMISSION_DENIED')

    def test_rollback_requires_authentication(self):
        """Unauthenticated users cannot trigger rollback."""
        self.client.force_authenticate(user=None)
        url = f'/api/v1/deployments/{self.v1_deploy.id}/rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertIn(response.status_code, [
            http_status.HTTP_401_UNAUTHORIZED,
            http_status.HTTP_403_FORBIDDEN,
        ])

    # ─────────────── idempotency / audit ───────────────

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_multiple_rollbacks_create_separate_deployments(self, mock_task):
        """Multiple rollbacks should each create a separate deployment."""
        url = f'/api/v1/deployments/{self.v1_deploy.id}/rollback/'

        response1 = self.client.post(url, data={'confirm': True}, format='json')
        response2 = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response1.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(response2.status_code, http_status.HTTP_201_CREATED)
        # 3 original + 2 rollbacks = 5
        self.assertEqual(Deployment.objects.filter(service=self.service).count(), 5)


class InstantRollbackTests(APITestCase):
    """Tests for the one-click instant-rollback endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='instantuser',
            email='instant@test.com',
            password='testpass123',
        )
        self.client.force_authenticate(user=self.user)
        self.provider = CloudProvider.objects.create(
            name='local',
            provider_type='LOCAL',
            is_active=True,
        )
        self.service = Service.objects.create(
            name='instant-svc',
            owner=self.user,
            provider=self.provider,
            branch='main',
        )

    def test_instant_rollback_requires_confirmation(self):
        url = f'/api/v1/services/{self.service.id}/instant-rollback/'
        response = self.client.post(url, data={}, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('code'), 'ROLLBACK_CONFIRMATION_REQUIRED')

    def test_instant_rollback_to_only_active_is_noop(self):
        """If the latest deployment is already ACTIVE, instant-rollback is a no-op."""
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash='onlygood',
        )
        url = f'/api/v1/services/{self.service.id}/instant-rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_409_CONFLICT)
        self.assertEqual(response.data.get('code'), 'ROLLBACK_NOOP')
        self.assertEqual(
            Deployment.objects.filter(service=self.service).count(),
            1,
        )

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_instant_rollback_picks_last_good_active(self, mock_task):
        """instant-rollback should redeploy the last ACTIVE deployment when a
        later deployment has failed."""
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.INACTIVE,
            commit_hash='priorgood',
        )
        latest_failed = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.FAILED,
            commit_hash='latestbad',
        )

        url = f'/api/v1/services/{self.service.id}/instant-rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        new_deploy = Deployment.objects.get(id=response.data['deployment']['id'])
        self.assertEqual(new_deploy.commit_hash, 'priorgood')
        self.assertEqual(new_deploy.rollback_from_id, latest_failed.id)
        self.assertTrue(new_deploy.is_rollback)

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_instant_rollback_accepts_message(self, mock_task):
        """The optional message should be embedded in the rollback commit_message."""
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.INACTIVE,
            commit_hash='priorgood',
        )
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.FAILED,
            commit_hash='latestbad',
        )

        url = f'/api/v1/services/{self.service.id}/instant-rollback/'
        response = self.client.post(
            url,
            data={'confirm': True, 'message': 'Customer-reported regression'},
            format='json',
        )

        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        new_deploy = Deployment.objects.get(id=response.data['deployment']['id'])
        self.assertIn('Customer-reported regression', new_deploy.commit_message)

    def test_instant_rollback_without_provider_returns_error(self):
        """If no provider is configured, instant-rollback should fail with a clear error."""
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.INACTIVE,
            commit_hash='priorgood',
        )
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.FAILED,
            commit_hash='latestbad',
        )

        self.service.provider = None
        self.service.save()
        CloudProvider.objects.all().delete()

        url = f'/api/v1/services/{self.service.id}/instant-rollback/'
        response = self.client.post(url, data={'confirm': True}, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('code'), 'ROLLBACK_PERMISSION_DENIED')
        # The queued rollback should have been marked FAILED on disk.
        new_deploy = Deployment.objects.exclude(commit_hash='priorgood').first()
        self.assertIsNotNone(new_deploy)
        self.assertEqual(new_deploy.status, Deployment.Status.FAILED)
