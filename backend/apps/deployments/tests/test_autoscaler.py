from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.deployments.models import Project, Service
from apps.deployments.models.metrics import ServiceMetric
from apps.deployments.models.replica import ServiceReplica
from apps.autoscaler.services.legacy_autoscaler import _evaluate_scaling

User = get_user_model()

class AutoscalerTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="test", password="pwd")
        self.project = Project.objects.create(name="Test Proj", owner=self.user)
        self.service = Service.objects.create(
            name="test-service",
            owner=self.user,
            project=self.project,
            min_replicas=1,
            max_replicas=3,
            autoscale_cpu_target=80
        )

    def test_scale_up_respects_max_replicas(self):
        self.service.max_replicas = 2
        self.service.min_replicas = 1
        self.service.save()
        Service.objects.filter(id=self.service.id).update(last_scale_at=timezone.now() - timedelta(minutes=10))
        self.service.refresh_from_db()
        now = timezone.now()
        ServiceMetric.objects.create(service=self.service, cpu_usage=90, cpu_limit=100, memory_usage=100, memory_limit=200, timestamp=now - timedelta(minutes=1))

        result = _evaluate_scaling(self.service, ServiceMetric)

        self.assertIsNotNone(result)
        self.assertEqual(result['action'], 'scale_up')
        self.assertEqual(result['replicas'], 2)  # capped at max_replicas

    def test_scale_up_increases_replicas(self):
        self.service.min_replicas = 1
        self.service.save()
        Service.objects.filter(id=self.service.id).update(last_scale_at=timezone.now() - timedelta(minutes=10))
        self.service.refresh_from_db()
        now = timezone.now()
        ServiceMetric.objects.create(service=self.service, cpu_usage=90, cpu_limit=100, memory_usage=100, memory_limit=200, timestamp=now - timedelta(minutes=1))

        result = _evaluate_scaling(self.service, ServiceMetric)

        self.assertIsNotNone(result)
        self.assertEqual(result['action'], 'scale_up')
        self.assertEqual(result['replicas'], 2)

    def test_scale_down_respects_absolute_minimum(self):
        self.service.min_replicas = 1
        self.service.save()
        Service.objects.filter(id=self.service.id).update(last_scale_at=timezone.now() - timedelta(minutes=10))
        self.service.refresh_from_db()
        now = timezone.now()
        ServiceMetric.objects.create(service=self.service, cpu_usage=10, cpu_limit=100, memory_usage=100, memory_limit=200, timestamp=now - timedelta(minutes=1))

        result = _evaluate_scaling(self.service, ServiceMetric)

        # min_replicas=1 means current_replicas=1 (home instance + 0 running replicas)
        # Should not drop below 1 → no scale-down
        self.assertIsNone(result)

    def test_scale_down_decreases_replicas(self):
        self.service.min_replicas = 2
        self.service.save()
        Service.objects.filter(id=self.service.id).update(last_scale_at=timezone.now() - timedelta(minutes=10))
        self.service.refresh_from_db()
        ServiceReplica.objects.create(service=self.service, status='RUNNING')
        now = timezone.now()
        ServiceMetric.objects.create(service=self.service, cpu_usage=10, cpu_limit=100, memory_usage=100, memory_limit=200, timestamp=now - timedelta(minutes=1))

        result = _evaluate_scaling(self.service, ServiceMetric)

        self.assertIsNotNone(result)
        self.assertEqual(result['action'], 'scale_down')
        self.assertEqual(result['replicas'], 1)  # 2 - 1 = 1


    def test_cooldown_prevents_rapid_scaling(self):
        self.service.min_replicas = 1
        # Set last_scale_at to 30 seconds ago
        self.service.last_scale_at = timezone.now() - timedelta(seconds=30)
        self.service.save()

        now = timezone.now()
        ServiceMetric.objects.create(service=self.service, cpu_usage=90, cpu_limit=100, memory_usage=100, memory_limit=200, timestamp=now - timedelta(minutes=1))

        result = _evaluate_scaling(self.service, ServiceMetric)

        self.assertIsNone(result)  # Should not scale up due to 1m cooldown

    def test_scale_down_cooldown(self):
        self.service.min_replicas = 2
        # Set last_scale_at to 3 minutes ago
        self.service.last_scale_at = timezone.now() - timedelta(minutes=3)
        self.service.save()

        now = timezone.now()
        ServiceMetric.objects.create(service=self.service, cpu_usage=10, cpu_limit=100, memory_usage=100, memory_limit=200, timestamp=now - timedelta(minutes=1))

        result = _evaluate_scaling(self.service, ServiceMetric)

        self.assertIsNone(result)  # Should not scale down due to 5m cooldown
