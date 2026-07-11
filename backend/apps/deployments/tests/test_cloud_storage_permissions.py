"""Tests for CloudStorageViewSet tenant and superuser permissions."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import Project, Service
from apps.deployments.models_cloud_storage import CloudStorageDestination

User = get_user_model()


class CloudStoragePermissionsTest(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser("admin", "admin@smsly.cloud", "pass")
        self.user = User.objects.create_user(username="regular", password="pwd")
        self.other_user = User.objects.create_user(username="other", password="pwd")

        self.project = Project.objects.create(name="Proj", owner=self.user)
        self.service = Service.objects.create(name="my-service", owner=self.user, project=self.project)

        self.platform_dest = CloudStorageDestination.objects.create(
            name="Platform S3",
            provider="s3",
            bucket="platform-bucket",
            access_key="ak",
            secret_key="sk",
            service=None,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_regular_user_cannot_create_platform_wide_destination(self):
        response = self.client.post(
            "/api/v1/cloud-storage/",
            {
                "name": "Attacker Platform S3",
                "provider": "s3",
                "bucket": "attacker-bucket",
                "access_key": "ak",
                "secret_key": "sk",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_regular_user_can_create_service_destination(self):
        response = self.client.post(
            "/api/v1/cloud-storage/",
            {
                "name": "My Service S3",
                "provider": "s3",
                "bucket": "service-bucket",
                "access_key": "ak",
                "secret_key": "sk",
                "service": str(self.service.id),
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)

    def test_regular_user_cannot_delete_platform_wide_destination(self):
        response = self.client.delete(f"/api/v1/cloud-storage/{self.platform_dest.id}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(CloudStorageDestination.objects.filter(id=self.platform_dest.id).exists())

    def test_superuser_can_create_and_delete_platform_wide_destination(self):
        self.client.force_authenticate(user=self.superuser)
        create_resp = self.client.post(
            "/api/v1/cloud-storage/",
            {
                "name": "Admin S3",
                "provider": "s3",
                "bucket": "admin-bucket",
                "access_key": "ak",
                "secret_key": "sk",
            },
            format="json",
        )
        self.assertEqual(create_resp.status_code, 201)
        dest_id = create_resp.data["id"]

        del_resp = self.client.delete(f"/api/v1/cloud-storage/{dest_id}/")
        self.assertEqual(del_resp.status_code, 204)
