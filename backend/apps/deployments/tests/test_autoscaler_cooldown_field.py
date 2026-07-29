# pylint: disable=invalid-name
"""Tests for autoscaler cooldown using the dedicated last_scale_at field."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.deployments.models import Project, Service
from apps.deployments.models.metrics import ServiceMetric
from apps.autoscaler.services.legacy_autoscaler import _evaluate_scaling

User = get_user_model()


class AutoscalerCooldownFieldTests(TestCase):
    """The autoscaler cooldown must use the dedicated `last_scale_at`
    field, not `updated_at`, so unrelated writes (e.g. health_status
    updates) do not reset the cooldown.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="cooldown-user", password="pwd")
        self.project = Project.objects.create(name="P", owner=self.user)
        self.service = Service.objects.create(
            name="cooldown-svc",
            owner=self.user,
            project=self.project,
            min_replicas=1,
            max_replicas=3,
            autoscale_cpu_target=80,
        )

    def test_scale_decision_updates_last_scale_at_only(self):
        now = timezone.now()
        ServiceMetric.objects.create(
            service=self.service,
            cpu_usage=90, cpu_limit=100,
            memory_usage=100, memory_limit=200,
            timestamp=now - timedelta(minutes=1),
        )

        # Ensure `updated_at` baseline is captured.
        Service.objects.filter(id=self.service.id).update(
            updated_at=now - timedelta(minutes=10),
            last_scale_at=None,
        )
        self.service.refresh_from_db()
        baseline_updated_at = self.service.updated_at
        self.assertIsNone(self.service.last_scale_at)

        result = _evaluate_scaling(self.service, ServiceMetric)

        self.service.refresh_from_db()
        # The legacy function now returns a dict instead of mutating min_replicas.
        self.assertIsNotNone(result)
        self.assertEqual(result['action'], 'scale_up')
        self.assertEqual(result['replicas'], 2)
        # last_scale_at should now be set
        self.assertIsNotNone(self.service.last_scale_at)
        # updated_at should NOT have changed
        self.assertEqual(self.service.updated_at, baseline_updated_at)

    def test_health_status_update_does_not_reset_cooldown(self):
        # Pretend we scaled recently — set last_scale_at 30 seconds ago.
        recent_scale = timezone.now() - timedelta(seconds=30)
        Service.objects.filter(id=self.service.id).update(last_scale_at=recent_scale)
        self.service.refresh_from_db()
        baseline_last_scale_at = self.service.last_scale_at

        # Simulate a health_status update (e.g. from health monitor).
        self.service.health_status = "unhealthy"
        self.service.save(update_fields=["health_status", "updated_at"])
        self.service.refresh_from_db()

        # updated_at moved forward, but last_scale_at must NOT have moved.
        self.assertGreater(self.service.updated_at, baseline_last_scale_at)
        self.assertEqual(self.service.last_scale_at, baseline_last_scale_at)

    def test_cooldown_uses_last_scale_at_not_updated_at(self):
        # Set last_scale_at 30 seconds ago (within cooldown).
        # Set updated_at to 10 minutes ago (would NOT block under old logic).
        recent_scale = timezone.now() - timedelta(seconds=30)
        Service.objects.filter(id=self.service.id).update(
            last_scale_at=recent_scale,
            updated_at=timezone.now() - timedelta(minutes=10),
        )
        self.service.refresh_from_db()

        now = timezone.now()
        ServiceMetric.objects.create(
            service=self.service,
            cpu_usage=90, cpu_limit=100,
            memory_usage=100, memory_limit=200,
            timestamp=now - timedelta(minutes=1),
        )

        _evaluate_scaling(self.service, ServiceMetric)

        self.service.refresh_from_db()
        # Should be blocked by the 1m cooldown via last_scale_at.
        self.assertEqual(self.service.min_replicas, 1)
