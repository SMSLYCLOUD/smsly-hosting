# pylint: disable=invalid-name
"""Tests for SEC (Issue 56): SystemConfigView scopes response to admins."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class SystemConfigScopingTests(TestCase):
    def setUp(self):
        self.url = "/api/v1/system/config/"
        self.client = APIClient()

    def test_anonymous_is_unauthorized(self):
        """The endpoint requires authentication."""
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (401, 403))

    def test_non_admin_sees_only_safe_fields(self):
        """A regular user does not see DEBUG, ALLOWED_HOSTS, REDIS_HOST, etc."""
        user = User.objects.create_user(
            username="scoping-user", password="pw", email="u@e.com"
        )
        self.client.force_authenticate(user=user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data
        # safe fields present
        self.assertIn("VERSION", data)
        self.assertIn("DOMAIN", data)
        self.assertIn("STORAGE_TOTAL_GB", data)
        # admin-only fields absent
        for forbidden in (
            "DEBUG",
            "ALLOWED_HOSTS",
            "REDIS_HOST",
            "CELERY_RESULT_BACKEND",
            "CONTAINER_REGISTRY_URL",
            "REGISTRY_USER",
            "DATABASE_ENGINE_TYPE",
            "GITHUB_WEBHOOK_SECRET_SET",
            "maintenance_actions",
            "CORS_ALLOWED_ORIGINS",
            "CSRF_TRUSTED_ORIGINS",
        ):
            self.assertNotIn(forbidden, data, forbidden)

    def test_admin_sees_full_response_including_maintenance_actions(self):
        """A superuser sees the full payload, including maintenance actions."""
        admin = User.objects.create_superuser(
            username="scoping-admin", password="pw", email="a@e.com"
        )
        self.client.force_authenticate(user=admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.data
        # safe fields still present
        self.assertIn("VERSION", data)
        self.assertIn("STORAGE_TOTAL_GB", data)
        # admin-only fields present
        self.assertIn("DEBUG", data)
        self.assertIn("ALLOWED_HOSTS", data)
        self.assertIn("REDIS_HOST", data)
        self.assertIn("maintenance_actions", data)
        self.assertIsInstance(data["maintenance_actions"], list)

    def test_admin_get_masks_secrets_and_patch_blank_keeps(self):
        """Secrets are never echoed: GET returns "" + *_SET flags, and a
        blank PATCH value keeps the stored secret instead of wiping it."""
        from apps.deployments.models.core import PlatformConfig
        admin = User.objects.create_superuser(
            username="scoping-secrets", password="pw", email="s@e.com"
        )
        self.client.force_authenticate(user=admin)
        config = PlatformConfig.load()
        config.smtp_password = "s3cret-smtp"
        config.save(update_fields=["smtp_password"])
        PlatformConfig.clear_cache()

        data = self.client.get(self.url).data
        self.assertEqual(data["SMTP_PASSWORD"], "")
        self.assertTrue(data["SMTP_PASSWORD_SET"])
        self.assertNotIn("s3cret-smtp", str(data))

        response = self.client.patch(
            self.url, {"SMTP_PASSWORD": ""}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("s3cret-smtp", str(response.data))
        PlatformConfig.clear_cache()
        self.assertEqual(PlatformConfig.load().smtp_password, "s3cret-smtp")
