# pylint: disable=invalid-name
"""Tests for docker system prune rate limiting in RemediationEngine."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase

from apps.cloud.models import CloudProvider
from apps.deployments.models import Service
from apps.intelligence.remediator import RemediationEngine


class DockerPruneRateLimitTests(TestCase):
    """Ensure the DISK_FULL fix runs docker system prune at most once
    per 24h per server, and only when an admin explicitly triggers it.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="prune-user",
            email="prune@example.com",
            password="pwd",
        )
        self.provider = CloudProvider.objects.create(
            name="prune-provider",
            provider_type="LOCAL",
            is_active=True,
        )
        self.service = Service.objects.create(
            name="prune-service",
            owner=self.user,
            provider=self.provider,
        )
        cache.clear()

    @patch("apps.intelligence.remediator.subprocess.run")
    def test_first_call_succeeds_and_sets_cache(self, mock_run):
        mock_run.return_value.stdout = "pruned"
        mock_run.return_value.stderr = ""

        engine = RemediationEngine()
        result = engine.apply_fix("DISK_FULL", str(self.service.id), explicit_admin=True)

        self.assertTrue(result)
        mock_run.assert_called_once()
        cache_key = f"docker_prune:{self.service.server_id or 'default'}"
        self.assertIsNotNone(cache.get(cache_key))

    @patch("apps.intelligence.remediator.subprocess.run")
    def test_second_call_within_24h_is_rate_limited(self, mock_run):
        mock_run.return_value.stdout = "pruned"
        mock_run.return_value.stderr = ""

        engine = RemediationEngine()
        first = engine.apply_fix("DISK_FULL", str(self.service.id), explicit_admin=True)
        self.assertTrue(first)

        mock_run.reset_mock()
        second = engine.apply_fix("DISK_FULL", str(self.service.id), explicit_admin=True)
        self.assertFalse(second)
        mock_run.assert_not_called()

    @patch("apps.intelligence.remediator.subprocess.run")
    def test_call_after_cache_expires_succeeds(self, mock_run):
        mock_run.return_value.stdout = "pruned"
        mock_run.return_value.stderr = ""

        engine = RemediationEngine()
        first = engine.apply_fix("DISK_FULL", str(self.service.id), explicit_admin=True)
        self.assertTrue(first)

        cache_key = f"docker_prune:{self.service.server_id or 'default'}"
        cache.delete(cache_key)

        mock_run.reset_mock()
        third = engine.apply_fix("DISK_FULL", str(self.service.id), explicit_admin=True)
        self.assertTrue(third)
        mock_run.assert_called_once()

    @patch("apps.intelligence.remediator.subprocess.run")
    def test_proactive_scan_without_admin_flag_is_refused(self, mock_run):
        engine = RemediationEngine()
        result = engine.apply_fix("DISK_FULL", str(self.service.id))
        self.assertFalse(result)
        mock_run.assert_not_called()
