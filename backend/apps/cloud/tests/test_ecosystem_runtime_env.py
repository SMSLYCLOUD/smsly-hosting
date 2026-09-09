import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from apps.deployments.models import EnvironmentVariable, Project, Service


class EcosystemRuntimeEnvTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="runtime-env", password="password123",
        )
        self.project = Project.objects.create(
            owner=self.user, name="Runtime Project", slug="runtime-project",
        )
        self.service = Service.objects.create(
            owner=self.user,
            project=self.project,
            name="runtime-api",
        )
        EnvironmentVariable.objects.create(
            service=self.service, key="PORT", value="8080", is_secret=False,
        )
        EnvironmentVariable.objects.create(
            service=self.service, key="API_TOKEN", value="secret-value", is_secret=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_runtime_env_is_current_and_masks_secrets(self):
        response = self.client.get(
            "/api/v1/cloud/intelligence/runtime-env/",
            {"project_id": str(self.project.id)},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        env = payload["services"]["runtime-api"]["env_vars"]
        self.assertEqual(env["PORT"], "8080")
        self.assertEqual(env["API_TOKEN"], "********")
        self.assertFalse(payload["revealed"])

    def test_runtime_env_reveal_requires_explicit_flag(self):
        response = self.client.get(
            "/api/v1/cloud/intelligence/runtime-env/",
            {"project_id": str(self.project.id), "reveal": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["services"]["runtime-api"]["env_vars"]["API_TOKEN"],
            "secret-value",
        )
