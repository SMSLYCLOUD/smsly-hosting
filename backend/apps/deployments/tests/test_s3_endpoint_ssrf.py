from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models_cloud_storage import CloudStorageDestination


class CloudStorageEndpointSSRFTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="s3-ssrf-user", password="p",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.dest = CloudStorageDestination.objects.create(
            name="ssrf-target",
            provider="minio",
            bucket="b",
            region="us-east-1",
            endpoint="http://169.254.169.254/",
            access_key="a" * 32,
            secret_key="s" * 32,
        )

    def tearDown(self):
        self.dest.delete()
        self.user.delete()

    @patch("apps.deployments.models_cloud_storage.CloudStorageDestination.upload_test_file")
    def test_metadata_endpoint_rejected(self, mock_upload):
        mock_upload.return_value = True
        url = f"/api/v1/cloud-storage/{self.dest.id}/test/"
        resp = self.client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("status"), "error")
        mock_upload.assert_not_called()

    @patch("apps.deployments.models_cloud_storage.CloudStorageDestination.upload_test_file")
    def test_loopback_endpoint_rejected(self, mock_upload):
        self.dest.endpoint = "http://127.0.0.1:9000"
        self.dest.save()
        mock_upload.return_value = True
        url = f"/api/v1/cloud-storage/{self.dest.id}/test/"
        resp = self.client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, 400)
        mock_upload.assert_not_called()

    @patch("apps.deployments.models_cloud_storage.CloudStorageDestination.upload_test_file")
    def test_rfc1918_endpoint_rejected(self, mock_upload):
        self.dest.endpoint = "http://10.0.0.5:9000"
        self.dest.save()
        mock_upload.return_value = True
        url = f"/api/v1/cloud-storage/{self.dest.id}/test/"
        resp = self.client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, 400)
        mock_upload.assert_not_called()
