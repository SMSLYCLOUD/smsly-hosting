# pylint: disable=invalid-name
"""Regression tests for deployment list payload size and fields."""

from django.contrib.auth.models import User
from rest_framework.test import APITestCase

from apps.deployments.models import Deployment, Service


class DeploymentListPayloadTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="deploy-list-user",
            email="deploy-list-user@example.com",
            password="password123",
        )
        self.client.force_authenticate(user=self.user)
        self.service = Service.objects.create(
            name="deploy-list-service",
            owner=self.user,
        )

    def test_deployments_list_uses_lightweight_payload(self):
        Deployment.objects.create(
            service=self.service,
            status=Deployment.Status.FAILED,
            commit_hash="a" * 40,
            commit_message="test commit",
            ai_diagnosis="test diagnosis",
            build_logs="x" * 5000,
        )

        response = self.client.get("/api/v1/deployments/?page_size=20")
        self.assertEqual(response.status_code, 200)
        rows = response.data.get("results", response.data)
        self.assertEqual(len(rows), 1)
        row = rows[0]

        self.assertIn("ai_diagnosis", row)
        self.assertEqual(row["ai_diagnosis"], "test diagnosis")
        self.assertNotIn("build_logs", row)
