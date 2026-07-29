"""Path safety tests for the volume file_write and service file_upload actions."""
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service, Volume


class FileWritePathSafetyTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='fwtest',
            email='fwtest@example.com',
            password='password123',
        )
        self.provider = CloudProvider.objects.create(
            name='fwtest-provider',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name='fwservice',
            owner=self.user,
            provider=self.provider,
        )
        self.volume = Volume.objects.create(
            service=self.service,
            name='data',
            mount_path='/data/fwservice',
            size_gb=10,
        )
        self.client.force_authenticate(user=self.user)

    def _file_write_url(self):
        return (
            f"/api/v1/services/{self.service.id}/volumes/{self.volume.id}/file-write/"
        )

    def _post_file_write(self, path, content='test'):
        return self.client.post(
            self._file_write_url(),
            {'path': path, 'content': content},
            format='json',
        )

    def test_blocks_traversal_outside_mount(self):
        response = self._post_file_write('/data/other_service/secret.txt')
        self.assertIn(
            response.status_code,
            [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_404_NOT_FOUND,
            ],
        )

    def test_blocks_dotdot_traversal(self):
        response = self._post_file_write('/data/fwservice/../../etc/passwd')
        self.assertIn(
            response.status_code,
            [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_404_NOT_FOUND,
            ],
        )

    def test_blocks_etc_path(self):
        response = self._post_file_write('/etc/passwd')
        self.assertIn(
            response.status_code,
            [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_404_NOT_FOUND,
            ],
        )

    def test_blocks_basename_with_dotdot_segment(self):
        response = self._post_file_write('/data/fwservice//..')
        self.assertIn(
            response.status_code,
            [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_404_NOT_FOUND,
            ],
        )

    @patch('apps.deployments.views.storage.resolve_running_container')
    def test_allows_path_inside_mount(self, mock_resolve):
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (0, b'')
        mock_container.put_archive.return_value = True
        mock_resolve.return_value = mock_container

        response = self._post_file_write('/data/fwservice/subdir/file.txt')
        self.assertIn(
            response.status_code,
            [
                status.HTTP_200_OK,
                status.HTTP_201_CREATED,
                status.HTTP_404_NOT_FOUND,
            ],
        )


class FileUploadPathSafetyTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='fuptest',
            email='fuptest@example.com',
            password='password123',
        )
        self.provider = CloudProvider.objects.create(
            name='fuptest-provider',
            provider_type=CloudProvider.ProviderType.LOCAL,
            is_active=True,
        )
        self.service = Service.objects.create(
            name='fupservice',
            owner=self.user,
            provider=self.provider,
        )
        self.volume = Volume.objects.create(
            service=self.service,
            name='data',
            mount_path='/data/fupservice',
            size_gb=10,
        )
        self.client.force_authenticate(user=self.user)

    def _file_upload_url(self):
        return f"/api/v1/services/{self.service.id}/file-upload/"

    def _post_file_upload(self, path, content_b64='dGVzdA=='):
        return self.client.post(
            self._file_upload_url(),
            {'path': path, 'content': content_b64},
            format='json',
        )

    def test_blocks_traversal_outside_mount(self):
        response = self._post_file_upload('/data/other_service/secret.txt')
        self.assertIn(
            response.status_code,
            [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_404_NOT_FOUND,
            ],
        )

    def test_blocks_dotdot_traversal(self):
        response = self._post_file_upload('/data/fupservice/../../etc/passwd')
        self.assertIn(
            response.status_code,
            [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_404_NOT_FOUND,
            ],
        )

    def test_blocks_etc_path(self):
        response = self._post_file_upload('/etc/passwd')
        self.assertIn(
            response.status_code,
            [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_403_FORBIDDEN,
                status.HTTP_404_NOT_FOUND,
            ],
        )
