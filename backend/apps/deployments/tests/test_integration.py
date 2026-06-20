# pylint: disable=invalid-name
"""
Integration tests for SMSLY Hosting deployment pipeline.
Tests the complete flow from service creation to deployment.
"""
import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from rest_framework import status as http_status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Deployment, Service


class DeploymentPipelineTests(APITestCase):
    """Integration tests for the full deployment pipeline."""

    def setUp(self):
        """Set up test fixtures."""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)

        # Create a mock cloud provider
        self.provider = CloudProvider.objects.create(
            name='test-local',
            provider_type='LOCAL',
            is_active=True
        )

        # Create a test service
        self.service = Service.objects.create(
            name='test-service',
            repository_url='https://github.com/test/repo',
            branch='main',
            owner=self.user,
            provider=self.provider
        )

    def test_deployment_trigger_creates_queued_deployment(self):
        """Test that triggering a deployment creates a QUEUED deployment."""
        url = '/api/v1/deployments/trigger/'
        data = {
            'service_id': str(self.service.id),
            'provider_id': str(self.provider.id)
        }

        with patch('apps.deployments.tasks.smart_deploy_task.delay') as mock_task:
            response = self.client.post(url, data, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertIn('deployment_id', response.data)

        # Verify deployment was created
        deployment = Deployment.objects.get(id=response.data['deployment_id'])
        self.assertEqual(deployment.status, Deployment.Status.QUEUED)
        mock_task.assert_called_once()

    def test_deployment_trigger_with_invalid_service(self):
        """Test that invalid service_id returns 404."""
        url = '/api/v1/deployments/trigger/'
        data = {
            'service_id': str(uuid.uuid4()),  # Random UUID
            'provider_id': str(self.provider.id)
        }

        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_404_NOT_FOUND)

    def test_deployment_trigger_with_invalid_provider(self):
        """Test that invalid provider_id returns 404."""
        url = '/api/v1/deployments/trigger/'
        data = {
            'service_id': str(self.service.id),
            'provider_id': str(uuid.uuid4())  # Random UUID
        }

        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_404_NOT_FOUND)

    def test_deployment_trigger_unauthenticated(self):
        """Test that unauthenticated users cannot trigger deployments."""
        self.client.force_authenticate(user=None)
        url = '/api/v1/deployments/trigger/'
        data = {
            'service_id': str(self.service.id),
            'provider_id': str(self.provider.id)
        }

        response = self.client.post(url, data, format='json')
        self.assertIn(response.status_code, [
            http_status.HTTP_401_UNAUTHORIZED,
            http_status.HTTP_403_FORBIDDEN
        ])


class FileUploadSecurityTests(APITestCase):
    """Security tests for the file upload endpoint."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='uploaduser',
            email='upload@example.com',
            password='uploadpass123'
        )
        self.client.force_authenticate(user=self.user)

        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True
        )

        self.service = Service.objects.create(
            name='upload-test-service',
            repository_url='https://github.com/test/repo',
            owner=self.user,
            provider=self.provider
        )

    def test_upload_rejects_non_zip_files(self):
        """Test that non-zip files are rejected."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        url = '/api/v1/deployments/upload/'
        fake_file = SimpleUploadedFile(
            "malicious.exe",
            b"fake executable content",
            content_type="application/octet-stream"
        )
        data = {
            'service_id': str(self.service.id),
            'file': fake_file
        }

        response = self.client.post(url, data, format='multipart')
        self.assertEqual(
            response.status_code,
            http_status.HTTP_400_BAD_REQUEST)
        self.assertIn('Invalid file type', response.data.get('error', ''))

    @override_settings(MAX_UPLOAD_SIZE=500)
    def test_upload_enforces_size_limit(self):
        """Test that oversized files are rejected."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        url = '/api/v1/deployments/upload/'
        # Create a file larger than 500 bytes
        large_file = SimpleUploadedFile(
            "large.zip",
            b"x" * 1024,  # 1KB content
            content_type="application/zip"
        )

        data = {
            'service_id': str(self.service.id),
            'file': large_file
        }

        response = self.client.post(url, data, format='multipart')
        self.assertEqual(response.status_code,
                         http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    def test_upload_requires_authentication(self):
        """Test that unauthenticated users cannot upload files."""
        self.client.force_authenticate(user=None)

        url = '/api/v1/deployments/upload/'
        response = self.client.post(url, {}, format='multipart')

        self.assertIn(response.status_code, [
            http_status.HTTP_401_UNAUTHORIZED,
            http_status.HTTP_403_FORBIDDEN
        ])


class RemediatorTests(TestCase):
    """Tests for the AI auto-remediation engine."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='remediator_test',
            email='remediate@example.com',
            password='testpass'
        )

        self.provider = CloudProvider.objects.create(
            name='test-provider',
            provider_type='LOCAL',
            is_active=True
        )

        self.service = Service.objects.create(
            name='crash-test-service',
            repository_url='https://github.com/test/crashy',
            owner=self.user,
            provider=self.provider,
            memory_mb=512
        )

        # Create a successful deployment to rollback to
        self.good_deploy = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.ACTIVE,
            commit_hash='abc123good',
            commit_message='Working version'
        )

        # Create a failed deployment
        self.bad_deploy = Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.FAILED,
            commit_hash='def456bad',
            commit_message='Broken version'
        )

    def test_suggest_fix_returns_recommendation(self):
        """Test that suggest_fix returns appropriate recommendations."""
        from apps.intelligence.remediator import RemediationEngine

        engine = RemediationEngine()

        oom_fix = engine.suggest_fix('OOM_KILLED')
        self.assertIsNotNone(oom_fix)
        self.assertEqual(oom_fix['action'], 'SCALE_UP')
        self.assertEqual(oom_fix['resource'], 'MEMORY')

        crash_fix = engine.suggest_fix('CRASH_LOOP')
        self.assertIsNotNone(crash_fix)
        self.assertEqual(crash_fix['action'], 'ROLLBACK')

    @patch('apps.deployments.tasks.smart_deploy_task.delay')
    def test_apply_fix_scales_memory(self, mock_task):
        """Test that OOM_KILLED fix increases memory."""
        from apps.intelligence.remediator import RemediationEngine

        engine = RemediationEngine()
        original_memory = self.service.memory_mb

        result = engine.apply_fix('OOM_KILLED', str(self.service.id))

        self.assertTrue(result)
        self.service.refresh_from_db()
        self.assertEqual(self.service.memory_mb, original_memory + 256)

    def test_suggest_fix_returns_none_for_unknown(self):
        """Test that unknown issue types return None."""
        from apps.intelligence.remediator import RemediationEngine

        engine = RemediationEngine()
        fix = engine.suggest_fix('UNKNOWN_ISSUE')
        self.assertIsNone(fix)
