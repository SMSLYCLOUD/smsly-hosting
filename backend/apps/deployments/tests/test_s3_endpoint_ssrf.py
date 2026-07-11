from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models_cloud_storage import CloudStorageDestination
from apps.deployments.views_cloud_storage import CloudStorageViewSet


class CloudStorageEndpointSSRFTests(TestCase):
    """Tests that the cloud-storage test action rejects SSRF-prone endpoints.

    The defense-in-depth validation in the ``test`` action calls
    ``validate_endpoint_url()`` before attempting any upload.  Endpoints
    that point to cloud metadata services or untrusted external hosts
    over plain HTTP are rejected.
    """

    def setUp(self):
        # Disable throttling for these tests.  We are verifying SSRF-protection
        # logic, not rate-limiting behaviour.  Removing throttle_classes also
        # makes the tests independent of whatever state other tests leave in
        # DRF's api_settings cache (e.g. the throttle-test @override_settings
        # only populates "cloud_templates", which would cause an
        # ImproperlyConfigured crash for the "cloud_test" scope when those
        # tests run first).
        self._throttle_patcher = patch.object(
            CloudStorageViewSet, "throttle_classes", new=[]
        )
        self._throttle_patcher.start()

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
        self._throttle_patcher.stop()
        self.dest.delete()
        self.user.delete()

    @patch("apps.deployments.models_cloud_storage.CloudStorageDestination.upload_test_file")
    def test_cloud_metadata_endpoint_rejected(self, mock_upload):
        """AWS/GCP metadata endpoint must be rejected."""
        mock_upload.return_value = True
        url = f"/api/v1/cloud-storage/{self.dest.id}/test/"
        resp = self.client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("status"), "error")
        mock_upload.assert_not_called()

    @patch("apps.deployments.models_cloud_storage.CloudStorageDestination.upload_test_file")
    def test_external_http_endpoint_rejected(self, mock_upload):
        """Plain HTTP to an untrusted external host must be rejected."""
        self.dest.endpoint = "http://attacker.example/"
        self.dest.save()
        mock_upload.return_value = True
        url = f"/api/v1/cloud-storage/{self.dest.id}/test/"
        resp = self.client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("status"), "error")
        mock_upload.assert_not_called()

    @patch("apps.deployments.models_cloud_storage.CloudStorageDestination.upload_test_file")
    def test_internal_http_endpoint_allowed(self, mock_upload):
        """HTTP to internal hosts (localhost, RFC 1918) is intentionally
        allowed for self-hosted MinIO/NAS on private networks."""
        self.dest.endpoint = "http://10.0.0.5:9000"
        self.dest.save()
        mock_upload.return_value = True
        url = f"/api/v1/cloud-storage/{self.dest.id}/test/"
        resp = self.client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("status"), "ok")
        mock_upload.assert_called_once()

    @patch("apps.deployments.models_cloud_storage.CloudStorageDestination.upload_test_file")
    def test_https_external_endpoint_allowed(self, mock_upload):
        """HTTPS to external hosts (R2, S3, B2) is always allowed."""
        self.dest.endpoint = "https://r2.cloudflarestorage.com"
        self.dest.save()
        mock_upload.return_value = True
        url = f"/api/v1/cloud-storage/{self.dest.id}/test/"
        resp = self.client.post(url, {}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("status"), "ok")
        mock_upload.assert_called_once()
