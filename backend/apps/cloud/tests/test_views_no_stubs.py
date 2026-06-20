# pylint: disable=invalid-name
"""Regression tests to ensure cloud endpoints avoid demo/stub responses."""

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class CloudViewsNoStubTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="cloud-staff",
            email="cloud-staff@test.com",
            password="testpass123",
            is_staff=True,
        )
        self.user = User.objects.create_user(
            username="cloud-user",
            email="cloud-user@test.com",
            password="testpass123",
        )
        self.client = APIClient()
        self.provider = CloudProvider.objects.create(
            name="Local Test",
            provider_type=CloudProvider.ProviderType.LOCAL,
            region="local",
            is_active=True,
        )

    def test_sync_endpoint_returns_real_status_shape(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.post(f"/api/v1/cloud/providers/{self.provider.id}/sync/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(response.data["status"], {"synced", "auth_failed"})
        self.assertIn("resource_count", response.data)
        self.assertNotEqual(response.data.get("status"), "Sync started")

    def test_sync_requires_staff(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(f"/api/v1/cloud/providers/{self.provider.id}/sync/")
        self.assertEqual(response.status_code, 403)

    def test_available_regions_provider_filter(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(
            "/api/v1/cloud/providers/available_regions/",
            {"provider_type": "AWS"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(r.get("id") == "af-south-1" for r in response.data))

    def test_troubleshoot_uses_pattern_analysis(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/v1/cloud/intelligence/troubleshoot/",
            {"error_trace": "JavaScript heap out of memory"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["root_cause"], "OOM_KILLED")
        self.assertIn("suggested_actions", response.data)

    def test_generate_iac_returns_real_template(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(
            "/api/v1/cloud/intelligence/generate_iac/",
            {"description": "create postgres database"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["language"], "hcl")
        self.assertIn("resource", response.data["code"])

    def test_optimize_cost_service_scope(self):
        self.client.force_authenticate(user=self.user)
        service = Service.objects.create(
            name="cloud-cost-svc",
            owner=self.user,
            provider=self.provider,
            cpu_cores=1.0,
            memory_mb=1024,
        )
        response = self.client.get(
            "/api/v1/cloud/intelligence/optimize_cost/",
            {"service_id": str(service.id)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["scope"], "service")
        self.assertIn("estimates", response.data)
        self.assertIn("cheapest_provider", response.data)
